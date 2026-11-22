"""工作管理：每份上傳的檔案是一份工作（一個目錄），在背景執行流水線。

- 工作目錄：<根目錄>/<工作編號>/，內含原始檔、各階段產物、修正單、回饋。
- 執行：固定大小的執行緒池（PDFium 本身有全域鎖，同時跑多份不會互相破壞）。
- 進度：流水線每個階段開始、完成、沿用快取時發出事件，存在記憶體中供前端輪詢或串流。
- 保留期限：預設 30 天後自動刪除（隱私：使用者檔案不長期保存，見架構文件風險 R7）。
- 回饋資料庫：上傳時同意用於改善辨識的工作，其檔案、修正與評分另存到回饋根目錄，供評測、校準與調參使用。
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
    run_start: int = 0  # 這一次執行的第一則事件在 events 裡的位置（重跑時進度串流從這裡開始送）
    peak: float = 0.0  # 這一次執行到目前為止的最高進度（⑦提出修正時會從⑤再跑一輪，對外的進度不倒退）

    def fraction(self) -> float:
        """整體進度（0 到 1），同一次執行中不會倒退：③以前逐頁計，之後逐階段計。"""
        self.peak = max(self.peak, self._estimate())
        return self.peak

    def _estimate(self) -> float:
        if self.status == "done":
            return 1.0
        if self.stage == 0:
            return 0.0
        if self.stage == 1:
            return 0.03
        if self.stage <= 4 and self.page is not None and self.pages:
            per_page = (self.page + (self.stage - 1) / 4) / self.pages
            return min(0.6, 0.05 + 0.55 * per_page)
        if self.stage < 4:  # 逐頁的階段但不知道頁數（不應發生）：照階段估，不倒退
            return 0.05 + 0.55 * (self.stage - 1) / 4
        return min(0.98, 0.6 + 0.4 * (self.stage - 4) / (TOTAL_STAGES - 4 + 1))


class _Cancelled(Exception):
    """工作在執行中被刪除：在下一個階段開始前中止流水線。"""


INTERRUPTED = "伺服器在處理這份檔案時中斷（例如重新啟動），請重新轉換。"


class JobManager:
    def __init__(self, root: Path, workers: int = 2, retention_days: float = 30.0, cfg: Optional[Config] = None, feedback_root: Optional[Path] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.feedback_root = Path(feedback_root) if feedback_root else None
        self.retention_s = retention_days * 86400
        self.cfg = cfg
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mortise-job")
        self.progress: dict[str, Progress] = {}
        self.lock = threading.Lock()
        self.running: set[str] = set()
        self.deleting: set[str] = set()  # 已要求刪除、但流水線還在跑的工作：對外當作已經不存在，停下來後立刻刪掉檔案

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
        return _safe_id(job_id) and job_id not in self.deleting and (self.root / job_id / "job.json").exists()

    def pipeline(self, job_id: str) -> Pipeline:
        if not self.exists(job_id):
            raise KeyError(job_id)
        return Pipeline(self.root / job_id, cfg=self.cfg)

    def status(self, job_id: str) -> dict[str, Any]:
        pipe = self.pipeline(job_id)
        prog = self.progress.get(job_id)
        state = pipe.state
        status = prog.status if prog else {"done": "done", "failed": "failed", "running": "failed", "ready": "queued"}.get(state.get("status", ""), state.get("status", ""))
        interrupted = prog is None and state.get("status") == "running"  # 伺服器重新啟動前還在處理：記錄停在 running
        return {
            "job_id": job_id,
            "filename": state.get("filename"),
            "status": status,
            "progress": round(prog.fraction(), 3) if prog else (1.0 if status == "done" else 0.0),
            "stage": prog.stage if prog else None,
            "stage_name": prog.stage_name if prog else None,
            "page": (prog.page + 1) if prog and prog.page is not None else None,
            "pages": prog.pages if prog else None,
            "error": (prog.error if prog else None) or state.get("error") or (INTERRUPTED if interrupted else None),
            "rounds": state.get("rounds", []),
            "corrections": len(state.get("corrections", [])),
            "intent": state.get("intent"),
            "created_at": state.get("created_at"),
            "allow_training": state.get("allow_training", False),
        }

    def events_since(self, job_id: str, index: int) -> list[dict[str, Any]]:
        prog = self.progress.get(job_id)
        return prog.events[index:] if prog else []

    def run_start(self, job_id: str) -> int:
        """最近一次排入執行的第一則事件的位置（進度串流從這裡開始送）。"""
        prog = self.progress.get(job_id)
        return prog.run_start if prog else 0

    # ---------- 執行 ----------
    def submit(self, job_id: str) -> None:
        with self.lock:
            prog = self.progress.setdefault(job_id, Progress())
            prog.status = "queued"
            prog.error = None
            prog.stage, prog.page, prog.peak = 0, None, 0.0
            prog.run_start = len(prog.events)
        self.pool.submit(self._run, job_id)

    def add_corrections(self, job_id: str, corrections: list[Correction]) -> None:
        pipe = self.pipeline(job_id)
        self.record_corrections(job_id, corrections)  # 先記下系統當時的值，再重跑
        pipe.add_corrections(corrections)
        self.submit(job_id)

    def _run(self, job_id: str) -> None:
        with self.lock:
            if job_id in self.running:  # 同一份工作不同時跑兩次：跑完再跑一次
                self.pool.submit(self._run_later, job_id)
                return
            if job_id in self.deleting or not (self.root / job_id / "job.json").exists():  # 排隊時就被刪除了
                self.progress.pop(job_id, None)
                return
            self.running.add(job_id)
        prog = self.progress[job_id]
        prog.status, prog.started_at, prog.stage, prog.page, prog.peak = "running", time.time(), 0, None, 0.0
        prog.run_count += 1
        holder: dict[str, Pipeline] = {}

        def count_pages() -> None:
            """頁數（逐頁的進度要用）：①完成後才知道；重跑時①的產物已經在了。"""
            try:
                from ..artifacts import SourceDoc

                pipe = holder.get("pipe")
                if pipe is not None and pipe.store.has("s1/source_doc"):
                    prog.pages = len(pipe.store.load("s1/source_doc", SourceDoc).pages)
            except Exception:
                pass

        def on_event(e: dict[str, Any]) -> None:
            if job_id in self.deleting:
                raise _Cancelled()
            prog.stage = e["stage"]
            prog.stage_name = e["stage_name"]
            prog.page = e.get("page")
            if e["stage"] == 1 and e.get("status") in ("done", "cached") and not prog.pages:
                count_pages()
            prog.events.append({**e, "t": round(time.time(), 3), "progress": round(prog.fraction(), 3)})

        try:
            pipe = Pipeline(self.root / job_id, cfg=self.cfg, on_event=on_event)
            holder["pipe"] = pipe
            count_pages()
            pipe.run()
            prog.status = "done"
        except PipelineError as e:
            prog.status, prog.error = "failed", str(e)
        except _Cancelled:
            prog.status, prog.error = "failed", "這份工作已被刪除。"
        except Exception as e:  # 未預期的錯誤：記下來，不讓執行緒池掛掉
            prog.status, prog.error = "failed", f"處理時發生未預期的錯誤：{e}"
        finally:
            prog.finished_at = time.time()
            prog.events.append({"status": prog.status, "t": round(time.time(), 3), "progress": round(prog.fraction(), 3), "error": prog.error})
            with self.lock:
                self.running.discard(job_id)
                remove = job_id in self.deleting
            if remove:
                self._remove(job_id)

    def _run_later(self, job_id: str) -> None:
        while job_id in self.running:
            time.sleep(0.2)
        self._run(job_id)

    # ---------- 回饋 ----------
    def save_feedback(self, job_id: str, feedback: dict[str, Any]) -> Path:
        """存一份回饋（評分、意見、某個元素對或錯）。
        一律存在工作目錄（隨工作在保留期限後刪除）；使用者同意用於改善辨識時，另外收進回饋資料庫（見 _to_dataset）。"""
        pipe = self.pipeline(job_id)
        folder = pipe.dir / "feedback"
        folder.mkdir(exist_ok=True)
        path = folder / f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}.json"
        record = {"job_id": job_id, "filename": pipe.state.get("filename"), "allow_training": pipe.state.get("allow_training", False), "received_at": time.time(), **feedback}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        entry: dict[str, Any] = {"comments": [feedback["comment"]] if feedback.get("comment") else []}
        if feedback.get("tags"):
            entry["comments"].append("標籤：" + "、".join(feedback["tags"]))
        if feedback.get("rating") is not None:
            entry["page_ratings"] = [{"page_index": (feedback.get("page") or 1) - 1, "score": feedback["rating"], "max_score": 5, "comment": feedback.get("comment") or ""}]
        if feedback.get("element_id") and feedback.get("correct") is not None:
            item = {"page_index": (feedback.get("page") or 1) - 1, "element_id": feedback["element_id"], "field": "element", "target_stage": 4}
            if feedback["correct"]:
                entry["confirmations"] = [item]
            else:
                entry["comments"].append(f"使用者標示元素 {feedback['element_id']} 有錯")
        self._to_dataset(job_id, entry)
        return path

    def record_corrections(self, job_id: str, corrections: list[Correction]) -> None:
        """使用者的修正也是回饋：連同系統當時的值與信心值一起收進回饋資料庫（需同意）。"""
        pipe = self.pipeline(job_id)
        items = []
        try:
            from ..eval.feedback import resolve_value
            from ..eval.runner import load_artifacts

            arts = load_artifacts(pipe)
        except Exception:
            arts, resolve_value = None, None
        for c in corrections:
            item = c.model_dump(mode="json")
            if arts is not None:
                try:
                    snap = resolve_value(arts, c.target_stage, c.field, c.element_id, c.page_index)
                    if snap.get("resolved"):
                        item["system_value"] = snap.get("system_value")
                        item["system_confidence"] = snap.get("system_confidence")
                except Exception:
                    pass
            items.append(item)
        self._to_dataset(job_id, {"corrections": items})

    def _to_dataset(self, job_id: str, entry: dict[str, Any]) -> Optional[Path]:
        """回饋資料庫：<回饋根目錄>/samples/ 下放樣本檔與同名的 .feedback.json（mortise.feedback/1 格式），
        可以直接拿來跑 python -m mortise.eval run、annotations、calibrate、tune。只收使用者同意的工作。"""
        pipe = self.pipeline(job_id)
        if not pipe.state.get("allow_training") or self.feedback_root is None:
            return None
        samples = self.feedback_root / "samples"
        samples.mkdir(parents=True, exist_ok=True)
        name = f"{job_id}__{pipe.state.get('filename', 'upload')}"
        sample = samples / name
        if not sample.exists():
            shutil.copyfile(pipe.dir / pipe.state["source"], sample)
        fb_path = samples / (Path(name).stem + ".feedback.json")
        data: dict[str, Any] = {"schema": "mortise.feedback/1", "sample": name, "author": "user", "comments": [], "page_ratings": [], "corrections": [], "confirmations": []}
        if fb_path.exists():
            data.update(json.loads(fb_path.read_text(encoding="utf-8")))
        for key in ("comments", "page_ratings", "corrections", "confirmations"):
            data.setdefault(key, [])
            data[key] += entry.get(key, [])
        data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        fb_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return fb_path

    # ---------- 刪除與保留期限 ----------
    def delete(self, job_id: str) -> None:
        """刪除工作的所有檔案。還在處理中的工作：先標記（對外立刻當作不存在，API 一律回 404），
        流水線在下一個階段開始前中止，停下來後馬上刪除檔案。"""
        if not self.exists(job_id):
            return
        with self.lock:
            if job_id in self.running:
                self.deleting.add(job_id)
                return
        self._remove(job_id)

    def _remove(self, job_id: str) -> None:
        shutil.rmtree(self.root / job_id, ignore_errors=True)
        with self.lock:
            self.progress.pop(job_id, None)
            self.deleting.discard(job_id)

    def purge_expired(self, now: Optional[float] = None) -> list[str]:
        now = now or time.time()
        removed = []
        for d in self.root.iterdir():
            if self.feedback_root is not None and d.resolve() == self.feedback_root.resolve():
                continue  # 回饋資料庫是使用者同意保存的資料，不跟著工作過期
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
