"""工作管理：每份上傳的檔案是一份工作（一個目錄），在背景執行流水線。

- 工作目錄：<根目錄>/<工作編號>/，內含原始檔、各階段產物、修正單、回饋。
- 執行：固定大小的執行緒池（PDFium 本身有全域鎖，同時跑多份不會互相破壞）。
- 進度：流水線每個階段開始、完成、沿用快取時發出事件，存在記憶體中供前端輪詢或串流。
- 保留期限：預設 30 天後自動刪除（隱私：使用者檔案不長期保存，見架構文件風險 R7）。
"""

from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..artifacts import Correction, Intent
from ..config import Config
from ..pipeline import Pipeline, PipelineError
from ..pipeline.runner import STAGE_NAMES

TOTAL_STAGES = 7


@dataclass
class Progress:
    status: str = "queued"  # queued、running、done、failed
    stage: int = 0
    stage_name: str = ""
    page: Optional[int] = None
    pages: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    run_count: int = 0

    def fraction(self) -> float:
        """整體進度（0 到 1）：③以前逐頁計，之後逐階段計。"""
        if self.status == "done":
            return 1.0
        if self.stage == 0:
            return 0.0
        if self.stage <= 4 and self.page is not None and self.pages:
            per_page = (self.page + (self.stage - 1) / 4) / self.pages
            return min(0.6, 0.05 + 0.55 * per_page)
        return min(0.98, 0.6 + 0.4 * (self.stage - 4) / (TOTAL_STAGES - 4 + 1))


class JobManager:
    def __init__(self, root: Path, workers: int = 2, retention_days: float = 30.0, cfg: Optional[Config] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention_s = retention_days * 86400
        self.cfg = cfg
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mortise-job")
        self.progress: dict[str, Progress] = {}
        self.lock = threading.Lock()
        self.running: set[str] = set()

    # ---------- 建立與查詢 ----------
    def create(self, upload: Path, filename: str, intent: Intent, allow_training: bool = False) -> str:
        job_id = time.strftime("%Y%m%d") + "-" + secrets.token_hex(6)
        pipe = Pipeline(self.root / job_id, cfg=self.cfg)
        pipe.start(upload, filename, intent)
        pipe.state.update({"created_at": time.time(), "allow_training": bool(allow_training)})
        pipe._save_state()
        self.submit(job_id)
        return job_id

    def exists(self, job_id: str) -> bool:
        return _safe_id(job_id) and (self.root / job_id / "job.json").exists()

    def pipeline(self, job_id: str) -> Pipeline:
        if not self.exists(job_id):
            raise KeyError(job_id)
        return Pipeline(self.root / job_id, cfg=self.cfg)

    def status(self, job_id: str) -> dict[str, Any]:
        pipe = self.pipeline(job_id)
        prog = self.progress.get(job_id)
        state = pipe.state
        status = prog.status if prog else {"done": "done", "failed": "failed", "running": "failed", "ready": "queued"}.get(state.get("status", ""), state.get("status", ""))
        return {
            "job_id": job_id,
            "filename": state.get("filename"),
            "status": status,
            "progress": round(prog.fraction(), 3) if prog else (1.0 if status == "done" else 0.0),
            "stage": prog.stage if prog else None,
            "stage_name": prog.stage_name if prog else None,
            "page": (prog.page + 1) if prog and prog.page is not None else None,
            "pages": prog.pages if prog else None,
            "error": (prog.error if prog else None) or state.get("error"),
            "rounds": state.get("rounds", []),
            "corrections": len(state.get("corrections", [])),
            "intent": state.get("intent"),
            "created_at": state.get("created_at"),
            "allow_training": state.get("allow_training", False),
        }

    def events_since(self, job_id: str, index: int) -> list[dict[str, Any]]:
        prog = self.progress.get(job_id)
        return prog.events[index:] if prog else []

    # ---------- 執行 ----------
    def submit(self, job_id: str) -> None:
        with self.lock:
            prog = self.progress.setdefault(job_id, Progress())
            prog.status = "queued"
            prog.error = None
        self.pool.submit(self._run, job_id)

    def add_corrections(self, job_id: str, corrections: list[Correction]) -> None:
        pipe = self.pipeline(job_id)
        pipe.add_corrections(corrections)
        self.submit(job_id)

    def _run(self, job_id: str) -> None:
        with self.lock:
            if job_id in self.running:  # 同一份工作不同時跑兩次：跑完再跑一次
                self.pool.submit(self._run_later, job_id)
                return
            self.running.add(job_id)
        prog = self.progress[job_id]
        prog.status, prog.started_at, prog.stage, prog.page = "running", time.time(), 0, None
        prog.run_count += 1

        def on_event(e: dict[str, Any]) -> None:
            prog.stage = e["stage"]
            prog.stage_name = e["stage_name"]
            prog.page = e.get("page")
            prog.events.append({**e, "t": round(time.time(), 3), "progress": round(prog.fraction(), 3)})

        try:
            pipe = Pipeline(self.root / job_id, cfg=self.cfg, on_event=on_event)
            try:
                from ..artifacts import SourceDoc

                if pipe.store.has("s1/source_doc"):
                    prog.pages = len(pipe.store.load("s1/source_doc", SourceDoc).pages)
            except Exception:
                pass
            pipe.run()
            prog.status = "done"
        except PipelineError as e:
            prog.status, prog.error = "failed", str(e)
        except Exception as e:  # 未預期的錯誤：記下來，不讓執行緒池掛掉
            prog.status, prog.error = "failed", f"處理時發生未預期的錯誤：{e}"
        finally:
            prog.finished_at = time.time()
            prog.events.append({"status": prog.status, "t": round(time.time(), 3), "progress": round(prog.fraction(), 3), "error": prog.error})
            with self.lock:
                self.running.discard(job_id)

    def _run_later(self, job_id: str) -> None:
        while job_id in self.running:
            time.sleep(0.2)
        self._run(job_id)

    # ---------- 回饋 ----------
    def save_feedback(self, job_id: str, feedback: dict[str, Any]) -> Path:
        """存一份回饋（評分、意見、修正）。只有使用者同意時，才會被收進訓練與評測資料。"""
        pipe = self.pipeline(job_id)
        folder = pipe.dir / "feedback"
        folder.mkdir(exist_ok=True)
        path = folder / f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}.json"
        record = {"job_id": job_id, "filename": pipe.state.get("filename"), "allow_training": pipe.state.get("allow_training", False), "received_at": time.time(), **feedback}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    # ---------- 刪除與保留期限 ----------
    def delete(self, job_id: str) -> None:
        if self.exists(job_id) and job_id not in self.running:
            shutil.rmtree(self.root / job_id, ignore_errors=True)
            self.progress.pop(job_id, None)

    def purge_expired(self, now: Optional[float] = None) -> list[str]:
        now = now or time.time()
        removed = []
        for d in self.root.iterdir():
            state = d / "job.json"
            if not state.exists() or d.name in self.running:
                continue
            try:
                created = json.loads(state.read_text(encoding="utf-8")).get("created_at") or state.stat().st_mtime
            except Exception:
                created = state.stat().st_mtime
            if now - created > self.retention_s:
                shutil.rmtree(d, ignore_errors=True)
                self.progress.pop(d.name, None)
                removed.append(d.name)
        return removed


def _safe_id(job_id: str) -> bool:
    return bool(job_id) and all(ch.isalnum() or ch == "-" for ch in job_id) and len(job_id) <= 64


def stage_names() -> dict[int, str]:
    return dict(STAGE_NAMES)
