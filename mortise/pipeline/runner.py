"""執行器：依序執行七個階段，處理快取、修正單與驗證迴圈。

快取鍵 =（階段版本、相關設定、上游產物內容、送給該階段的修正單）。
所以：升級某個階段、改某一節設定、或開一張修正單，都只會從受影響的階段重跑到底。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel

from ..artifacts import Correction, CorrectionSource, ElementList, FidelityReport, Intent, Layout, OutputFile, PageSpec, SourceDoc, StyledDoc
from ..artifacts.layout import PageLayout
from ..config import Config
from ..stages import s1_input, s2_page, s3_recognize, s4_structure, s5_settings, s6_export, s7_verify
from .store import Store, atomic_write_text

M = TypeVar("M", bound=BaseModel)
Event = Callable[[dict[str, Any]], None]

STAGE_NAMES = {1: "輸入", 2: "頁面", 3: "辨識", 4: "結構", 5: "設定", 6: "輸出", 7: "驗證"}


class PipelineError(Exception):
    """給使用者看的錯誤（訊息為白話中文）。"""


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:20]


class Pipeline:
    def __init__(self, job_dir: Path, cfg: Optional[Config] = None, on_event: Optional[Event] = None):
        self.dir = Path(job_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.dir)
        self.cfg = cfg or Config.load()
        self.on_event = on_event or (lambda e: None)
        self._state_path = self.dir / "job.json"
        self.state: dict[str, Any] = {"status": "new", "corrections": [], "verify_corrections": [], "rounds": []}
        if self._state_path.exists():
            self.state.update(json.loads(self._state_path.read_text(encoding="utf-8")))

    # ---------- 工作狀態 ----------
    def _save_state(self) -> None:
        atomic_write_text(self._state_path, json.dumps(self.state, ensure_ascii=False, indent=1))

    def start(self, source: Path, filename: str, intent: Optional[Intent] = None) -> None:
        """登記要處理的檔案（複製一份到工作目錄）。"""
        dest = self.dir / "source" / Path(filename).name
        dest.parent.mkdir(exist_ok=True)
        if Path(source).resolve() != dest.resolve():
            shutil.copyfile(source, dest)
        self.state.update({"filename": Path(filename).name, "source": str(dest.relative_to(self.dir)), "intent": (intent or Intent()).model_dump(mode="json"), "status": "ready"})
        self._save_state()

    @property
    def corrections(self) -> list[Correction]:
        return [Correction.model_validate(c) for c in self.state["corrections"]]

    def add_corrections(self, corrections: list[Correction]) -> None:
        """加入修正單（使用者或 AI）。下次 run() 會從受影響的階段重跑。"""
        self.state["corrections"].extend(c.model_dump(mode="json") for c in corrections)
        self._save_state()

    def _for_stage(self, stage: int, page: Optional[int] = None) -> list[Correction]:
        every = self.corrections + [Correction.model_validate(c) for c in self.state["verify_corrections"]]
        return [c for c in every if c.target_stage == stage and (page is None or c.page_index is None or c.page_index == page)]

    # ---------- 快取 ----------
    def _cached(self, stage: int, name: str, cls: Type[M], key: str, fn: Callable[[], M], page: Optional[int] = None) -> tuple[M, str]:
        if self.store.has(name) and self.store.key_of(name) == key:
            self.on_event({"stage": stage, "stage_name": STAGE_NAMES[stage], "page": page, "status": "cached"})
            return self.store.load(name, cls), self.store.hash_of(name)
        self.on_event({"stage": stage, "stage_name": STAGE_NAMES[stage], "page": page, "status": "start"})
        artifact = fn()
        digest = self.store.save(name, artifact, key)
        self.on_event({"stage": stage, "stage_name": STAGE_NAMES[stage], "page": page, "status": "done"})
        return artifact, digest

    # ---------- 執行 ----------
    def run(self) -> FidelityReport:
        if "source" not in self.state:
            raise PipelineError("還沒有指定要處理的檔案。")
        self.state.update({"status": "running", "error": None, "verify_corrections": [], "rounds": []})
        self._save_state()
        try:
            report = self._run_with_loop()
        except (s1_input.InputError, s6_export.ExportNotImplemented) as e:
            self.state.update({"status": "failed", "error": str(e)})
            self._save_state()
            raise PipelineError(str(e)) from e
        except Exception as e:
            self.state.update({"status": "failed", "error": f"處理時發生未預期的錯誤：{e}"})
            self._save_state()
            raise
        self.state["status"] = "done"
        self._save_state()
        return report

    def _run_with_loop(self) -> FidelityReport:
        max_rounds = int(self.cfg.section("verify")["max_rounds"])
        report = self._run_once(0)
        self.state["rounds"].append({"round": 0, "score": report.overall_score, "proposed": len(report.corrections_proposed)})
        best = report
        round_no = 0
        while report.corrections_proposed and round_no < max_rounds:
            round_no += 1
            before = list(self.state["verify_corrections"])
            self.state["verify_corrections"] = before + [c.model_dump(mode="json") for c in report.corrections_proposed]
            report = self._run_once(round_no)
            improved = (report.overall_score or 0.0) > (best.overall_score or 0.0) + 1e-6
            self.state["rounds"].append({"round": round_no, "score": report.overall_score, "proposed": len(report.corrections_proposed), "kept": improved})
            if improved:
                best = report
            else:
                # 整體分數沒有進步：退回上一版
                self.state["verify_corrections"] = before
                best = self._run_once(round_no, final=True)
                break
        self._save_state()
        return best

    def _ai(self):
        if getattr(self, "_ai_dispatcher", None) is None:
            from ..ai import AiDispatcher

            self._ai_dispatcher = AiDispatcher(self.cfg.section("ai"), cache_dir=self.dir / "ai")
        return self._ai_dispatcher

    def _run_once(self, round_no: int, final: bool = False) -> FidelityReport:
        cfg, store = self.cfg, self.store
        ai = self._ai()
        ai_key = (cfg.key("ai"), ai.enabled, ai.provider.model)
        source = self.dir / self.state["source"]
        intent = Intent.model_validate(self.state.get("intent") or {})

        # ① 輸入
        k1 = _digest(s1_input.VERSION, cfg.key("input"), hashlib.sha256(source.read_bytes()).hexdigest(), intent.model_dump(mode="json"))
        doc, h1 = self._cached(1, "s1/source_doc", SourceDoc, k1, lambda: s1_input.run(source, self.state["filename"], intent, cfg, store))

        specs: list[PageSpec] = []
        elements: list[ElementList] = []
        page_layouts: list[PageLayout] = []
        h2s, h3s, h4s = [], [], []
        for ref in doc.pages:
            i = ref.page_index
            tag = f"page-{i + 1:03d}"
            # ② 頁面
            c2 = self._for_stage(2, i)
            k2 = _digest(s2_page.VERSION, cfg.key("page"), h1, i, [c.model_dump(mode="json") for c in c2])
            spec, h2 = self._cached(2, f"s2/{tag}", PageSpec, k2, lambda: s2_page.run(doc, i, cfg, c2, store), i)
            # ③ 辨識（只依賴頁面幾何，不依賴比例尺）
            c3 = self._for_stage(3, i)
            k3 = _digest(s3_recognize.VERSION, cfg.key("recognize"), doc.doc_id, ref.input_type.value.value, s2_page.geometry_key(spec), [c.model_dump(mode="json") for c in c3], ai_key)
            el, h3 = self._cached(3, f"s3/{tag}", ElementList, k3, lambda: s3_recognize.run(doc, spec, cfg, c3, store, ai=ai), i)
            # ④ 結構（逐頁）
            c4 = self._for_stage(4, i)
            k4 = _digest(s4_structure.VERSION, cfg.key("structure"), h3, spec.aspect_ratio, [c.model_dump(mode="json") for c in c4], ai_key)
            pl, h4 = self._cached(4, f"s4/{tag}", PageLayout, k4, lambda: s4_structure.run_page(el, spec, cfg, c4, ai=ai, page_image=self._page_image(spec) if ai.enabled else None), i)
            specs.append(spec)
            elements.append(el)
            page_layouts.append(pl)
            h2s.append(h2)
            h3s.append(h3)
            h4s.append(h4)

        # ④ 結構（跨頁）
        k4d = _digest(s4_structure.VERSION, cfg.key("structure"), h4s, h3s)
        layout, h4d = self._cached(4, "s4/layout", Layout, k4d, lambda: s4_structure.run_document(page_layouts, elements, cfg))
        # ⑤ 設定
        c5 = self._for_stage(5)
        k5 = _digest(s5_settings.VERSION, cfg.key("settings", "fonts", "page_sizes"), h1, h2s, h3s, h4d, [c.model_dump(mode="json") for c in c5])
        styled, h5 = self._cached(5, "s5/styled_doc", StyledDoc, k5, lambda: s5_settings.run(doc, specs, elements, layout, cfg, c5))
        # ⑥ 輸出
        outputs: list[OutputFile] = []
        h6s = []
        base = Path(self.state["filename"]).stem
        c6 = self._for_stage(6)
        for fmt in intent.outputs:
            c6f = [c for c in c6 if c.field.startswith(fmt.value + ".")]
            k6 = _digest(s6_export.VERSION, cfg.key("export"), h5, fmt.value, [c.model_dump(mode="json") for c in c6f])
            out, h6 = self._cached(6, f"s6/{fmt.value}", OutputFile, k6, lambda: s6_export.run(styled, fmt, base, cfg, store, c6f))
            outputs.append(out)
            h6s.append(h6)
        # ⑦ 驗證
        k7 = _digest(s7_verify.VERSION, cfg.key("verify", "recognize"), h2s, h3s, h4d, h5, h6s, round_no)
        report, _ = self._cached(7, "s7/fidelity_report", FidelityReport, k7, lambda: s7_verify.run(doc, specs, elements, layout, styled, outputs, cfg, store, round_no))
        if final:
            report = report.model_copy(update={"corrections_proposed": []})
        return report

    def _page_image(self, spec: PageSpec):
        from PIL import Image

        return Image.open(self.store.blob_path(spec.working_image)).convert("RGB")

    # ---------- 取用結果 ----------
    def output_path(self, fmt: str) -> Path:
        out = self.store.load(f"s6/{fmt}", OutputFile)
        return self.store.blob_path(out.file)

    def export_to(self, dest_dir: Path) -> list[Path]:
        """把輸出檔與還原度報告複製到指定目錄。"""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        written = []
        intent = Intent.model_validate(self.state.get("intent") or {})
        for fmt in intent.outputs:
            if self.store.has(f"s6/{fmt.value}"):
                out = self.store.load(f"s6/{fmt.value}", OutputFile)
                target = dest_dir / out.filename
                if target in written:  # 兩種格式同副檔名同檔名（例如 Word 的版面固定與可重排都是 .docx）：後者加上格式名，不互相覆蓋
                    target = dest_dir / f"{Path(out.filename).stem}_{fmt.value}{Path(out.filename).suffix}"
                shutil.copyfile(self.store.blob_path(out.file), target)
                written.append(target)
        if self.store.has("s7/fidelity_report"):
            target = dest_dir / (Path(self.state["filename"]).stem + ".report.json")
            target.write_text(self.store.load("s7/fidelity_report", FidelityReport).model_dump_json(indent=1), encoding="utf-8")
            written.append(target)
        return written


def user_correction(stage: int, field: str, new_value: Any, element_id: Optional[str] = None, page_index: Optional[int] = None, instruction: str = "", n: int = 0) -> Correction:
    """建立一張使用者修正單。"""
    cid = "u-" + _digest(stage, field, new_value, element_id, page_index, n)[:10]
    return Correction(id=cid, target_stage=stage, page_index=page_index, element_id=element_id, field=field, new_value=new_value, instruction=instruction, source=CorrectionSource.USER)
