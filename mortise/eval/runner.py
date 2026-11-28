"""批次評測：一整個樣本目錄跑完，交出每個樣本的分數、總表與給人看的報告。

    python -m mortise.eval run 樣本目錄 [--out 輸出目錄] [--baseline 先前的 summary.json]
                               [--variants screenshot,scan,photo] [--limit N]

樣本目錄的約定（照這樣放檔案就能評測）：
  樣本目錄/
    slides/                       ← 子目錄 = 分組（報表會依分組列出；可以多層，例如 posters/2024/）
      季報.pdf                    ← 一個檔案就是一個樣本：PDF、PNG、JPEG（副檔名 .pdf .png .jpg .jpeg）
      季報.truth.json             ← （可選）人工標準答案，格式見 load_truth()
      季報.feedback.json          ← （可選）回饋，格式見 feedback.py
    screenshots/
      line_截圖.png
    單張.jpg                      ← 放在根目錄的樣本，分組顯示為「（未分組）」
  名稱規則：標準答案與回饋檔必須與樣本「同名」（去掉樣本副檔名後加 .truth.json／.feedback.json）。

每個樣本做哪些評測：
  原生 PDF（每頁都是原生或混合）：
    (a) native：直接跑擷取產線，記錄端到端指標（⑦還原度報告）；有 .truth.json 時也與它比對。
    (b) 自我監督：把每頁的工作影像（150 dpi）用 synth.degrade 加上 screenshot／scan／photo 干擾，
        存成 PNG 當成影像樣本送進流水線（走影像產線），再以擷取產線的元素清單與版面為標準答案比對。
        座標對回：預測（影像頁面相對座標）→ 干擾後影像像素（PageSpec.source_to_page 的反矩陣）
        → 原工作影像像素（degrade 矩陣的反矩陣）→ 原頁面相對座標（除以工作影像寬）。
  影像樣本（PNG、JPEG、掃描 PDF）：有 .truth.json 就比對；沒有就只記錄端到端指標與各階段的信心值分佈。

輸出（--out 目錄，預設是樣本目錄旁的 樣本目錄名-eval/）：
  samples/<樣本路徑，斜線換成 __>.json   每個樣本一份：每種評測的指標、原始計數、干擾參數、信心值分佈、錯誤
  summary.json                             總表：平均、依評測類型與分組拆開、最差樣本、與基準的變化、錯誤
  summary.md                               給人看的繁體中文報告
  jobs/                                    各評測的流水線工作目錄（快取；同一設定重跑會沿用前三個階段）
  degraded/                                合成的干擾影像（可關閉）

單一樣本（或單一評測）失敗不會中斷整批：錯誤記在該樣本的 errors 與總表的 errors，然後繼續。

已知限制：
- 本模組一律透過 Pipeline 評測，不直接呼叫任何辨識模組：影像產線若還是暫時版（整頁當成一張圖），
  合成評測的文字指標為 0、整頁點陣化比例為 1；影像辨識模組接上之後不必修改本模組就會反映進步
  （2026-11-21 開發期間影像產線已接上，基準數字見 summary.md）。
- 影像產線接上後，每個合成評測要跑一次完整辨識（每頁約 10 到 60 秒），四份 fixture 加三種干擾約 6 分鐘。
- 擷取產線的標準答案缺圓形、圓角矩形與複雜路徑（它們留在底圖），旋轉文字的標準答案可能不可靠。
- 多頁 PDF 的自我監督預設最多評前 max_pages_per_sample 頁。

可單獨升級的點：平行化（每個樣本彼此獨立，可用 process pool）；依樣本類型抽樣的分層總表；
更多干擾種類（synth.py 擴充後在 variants 列出即可）。
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
import time
import traceback
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image

from ..artifacts import (
    Color, ElementList, FidelityReport, FontCandidate, ImageElement, ImageKind, InputType, Intent, Layout, OutputFormat,
    PageSpec, Point, Provenance, Quad, ShapeElement, ShapeGeometry, SourceDoc, StyledDoc, TextLine, TextRun, BlobRef, measured,
)
from ..artifacts.layout import PageLayout
from ..config import Config
from ..pipeline import Pipeline
from . import metrics as M
from .synth import CURVED_KINDS, KINDS, degrade, degrade_curved, sample_page_map

SAMPLE_EXTS = (".pdf", ".png", ".jpg", ".jpeg")
UNGROUPED = "（未分組）"

DEFAULT_PARAMS: dict[str, Any] = {
    "variants": ["screenshot", "scan", "photo"],  # 原生 PDF 自我監督要做的干擾種類；彎曲的紙寫 curved_<種類>（種類見 synth.CURVED_KINDS，例如 curved_curl_top）
    "seed": 0,  # 基礎亂數種子；每個（樣本、頁、干擾）的種子由它與名稱的 CRC32 導出，可重現
    "strength": 1.0,  # 干擾強度倍數（見 synth.degrade）
    "max_pages_per_sample": 5,  # 自我監督每個樣本最多評幾頁
    "worst_n": 10,  # 報表列出最差的幾個評測
    "save_degraded_images": True,  # 是否保留合成的干擾影像（在 degraded/ 下）
    "regression_tolerance": {},  # 指標名稱 → 容許退步幅度（與值同單位）；沒列的用 metrics.METRICS 的預設
    "metrics": {},  # 傳給 metrics 的參數覆寫（例如 IoU 門檻、綜合分數權重）
    "synth": {},  # 傳給 synth.degrade 的參數覆寫
    "outputs": ["pptx"],  # 評測時要求的輸出格式（⑦目標驗證目前只驗證 pptx）
    "md_metrics_by_type": [  # summary.md 依類型與分組拆開時列出的指標（全部指標都在 summary.json）
        "composite", "text_recall", "text_precision", "cer_zh", "cer_en", "page_cer", "position_error_pt", "size_error_pct",
        "font_top1", "font_top3", "color_delta_e", "bold_accuracy", "image_recall", "shape_recall", "paragraph_pair_f1",
        "boxes_per_paragraph", "reading_order", "e2e_score", "e2e_similarity", "e2e_text_match", "e2e_linebreak_match", "rasterized_page",
        "dewarp_pos_p95_px", "page_coverage", "background_frac", "pdf_text_cer",
    ],
}

TYPE_LABELS = {
    "native_pdf": "原生 PDF（擷取產線）",
    "synth_screenshot": "合成截圖（影像產線）",
    "synth_scan": "合成掃描（影像產線）",
    "synth_photo": "合成照片（影像產線）",
    **{f"synth_curved_{k}": f"合成彎曲照片：{v}（影像產線）" for k, v in (("flat", "平的紙"), ("bend", "弓起"), ("curl_top", "上緣捲起"),
                                                                    ("corner_curl", "捲角"), ("crease", "折痕"), ("curl_crease", "捲起加折痕"))},
    "image_screenshot": "截圖樣本",
    "image_photo": "照片樣本",
    "image_scanned_pdf": "掃描 PDF 樣本",
    "image_mixed_pdf": "混合 PDF 樣本",
}


def _p(params: dict | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ====================================================================== 樣本


@dataclass
class Sample:
    path: Path
    key: str  # 相對於樣本目錄的路徑（斜線分隔），例如 "slides/季報.pdf"
    group: str
    truth_path: Optional[Path] = None
    feedback_path: Optional[Path] = None

    @property
    def safe_key(self) -> str:
        return self.key.replace("/", "__")


def sidecar(path: Path, suffix: str) -> Path:
    """樣本的同名附檔：季報.pdf → 季報.truth.json。"""
    return path.with_name(path.stem + suffix)


def discover(root: Path, limit: Optional[int] = None) -> list[Sample]:
    root = Path(root)
    if root.is_file():
        files, base = [root], root.parent
    else:
        files = sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in SAMPLE_EXTS and not any(part.startswith(".") for part in f.relative_to(root).parts))
        base = root
    out = []
    for f in files:
        rel = f.relative_to(base)
        group = rel.parent.as_posix() if rel.parent.as_posix() not in ("", ".") else UNGROUPED
        t, fb = sidecar(f, ".truth.json"), sidecar(f, ".feedback.json")
        out.append(Sample(f, rel.as_posix(), group, t if t.exists() else None, fb if fb.exists() else None))
        if limit and len(out) >= limit:
            break
    return out


def variant_seed(base: int, key: str, page: int, variant: str) -> int:
    return (int(base) * 1000003 + zlib.crc32(f"{key}|{page}|{variant}".encode("utf-8"))) & 0xFFFFFFFF


# ====================================================================== 流水線產物


@dataclass
class RunArtifacts:
    doc: SourceDoc
    specs: list[PageSpec]
    elements: list[ElementList]
    layout: Optional[Layout]
    styled: Optional[StyledDoc]
    report: Optional[FidelityReport]
    seconds: float = 0.0
    store: Any = None  # 產物倉庫（讀 PageSpec 指向的檔案用，例如攤平對應圖）


def run_pipeline(job_dir: Path, source: Path, cfg: Config, outputs: list[str]) -> tuple[Pipeline, RunArtifacts]:
    """跑一次流水線（沿用同一工作目錄的快取；先清掉舊的修正單，評測不受回饋影響）。"""
    t0 = time.time()
    pipe = Pipeline(job_dir, cfg)
    pipe.state["corrections"] = []
    pipe.start(source, source.name, Intent(outputs=[OutputFormat(o) for o in outputs]))
    report = pipe.run()
    return pipe, load_artifacts(pipe, report, time.time() - t0)


def load_artifacts(pipe: Pipeline, report: Optional[FidelityReport] = None, seconds: float = 0.0) -> RunArtifacts:
    st = pipe.store
    doc = st.load("s1/source_doc", SourceDoc)
    specs = [st.load(f"s2/page-{r.page_index + 1:03d}", PageSpec) for r in doc.pages]
    els = [st.load(f"s3/page-{r.page_index + 1:03d}", ElementList) for r in doc.pages]
    layout = st.load("s4/layout", Layout) if st.has("s4/layout") else None
    styled = st.load("s5/styled_doc", StyledDoc) if st.has("s5/styled_doc") else None
    if report is None and st.has("s7/fidelity_report"):
        report = st.load("s7/fidelity_report", FidelityReport)
    return RunArtifacts(doc, specs, els, layout, styled, report, seconds, st)


def run_store(run: RunArtifacts):
    """這次流水線的產物倉庫（RunArtifacts 沒有直接帶，從 load_artifacts 時記下的屬性取）。"""
    return run.store


def page_layout(run: RunArtifacts, page_index: int) -> Optional[PageLayout]:
    if run.layout is None:
        return None
    return next((p for p in run.layout.pages if p.page_index == page_index), None)


def mat3(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape(3, 3)


def confidence_summary(run: RunArtifacts) -> dict[str, Any]:
    """各階段的信心值分佈（每階段一個總分佈＋各欄位的筆數與平均）。"""
    stages: dict[str, Any] = {}
    parts = {"1_輸入": [run.doc], "2_頁面": run.specs, "3_辨識": run.elements, "4_結構": [run.layout] if run.layout else [], "5_設定": [run.styled] if run.styled else []}
    for name, objs in parts.items():
        fields: dict[str, list[float]] = {}
        for o in objs:
            M.collect_confidences(o, fields)
        allv = [v for vs in fields.values() for v in vs]
        stages[name] = {"all": M.distribution(allv), "fields": {k: {"n": len(v), "mean": float(np.mean(v))} for k, v in sorted(fields.items())}}
    if run.report is not None:
        conf = [e.confidence for p in run.report.pages for e in p.elements]
        stages["7_驗證"] = {"all": M.distribution(conf), "fields": {}}
    return stages


# ====================================================================== 人工標準答案


@dataclass
class TruthPage:
    page_index: int
    elements: ElementList  # 標準答案座標系（相對單位，寬為 1）
    units: str  # rel、px、pt
    page_width_pt: Optional[float] = None
    image_width_px: Optional[float] = None
    groups: Optional[dict[str, str]] = None  # 行編號 → 段落鍵
    order: Optional[list[str]] = None  # 行的閱讀順序
    box_count: Optional[int] = None
    has_baseline: bool = False


def _hex(c: Any) -> Color:
    if isinstance(c, (list, tuple)):
        return Color(r=int(c[0]), g=int(c[1]), b=int(c[2]))
    s = str(c or "#000000").lstrip("#")
    return Color(r=int(s[0:2], 16), g=int(s[2:4], 16), b=int(s[4:6], 16))


def _quad_of(item: dict, k: float) -> Quad:
    if "quad" in item:
        pts = [(float(x) * k, float(y) * k) for x, y in item["quad"]]
        return Quad(tl=Point(x=pts[0][0], y=pts[0][1]), tr=Point(x=pts[1][0], y=pts[1][1]), br=Point(x=pts[2][0], y=pts[2][1]), bl=Point(x=pts[3][0], y=pts[3][1]))
    x0, y0, x1, y1 = (float(v) * k for v in item["bbox"])
    return Quad.from_rect(x0, y0, x1, y1)


_W = "eval.truth@1"
_DUMMY_BLOB = BlobRef(sha256="0" * 64, media_type="image/png", size=0, ext="png")


def load_truth(path: Path, image_width_px: Optional[float] = None) -> list[TruthPage]:
    """讀 .truth.json。兩種寫法都接受：

    簡易格式（給人填）：
      {"units": "px" | "rel" | "pt",              ← 座標單位：影像像素、頁寬為 1 的相對單位、pt
       "pages": [{"page_index": 0,
                  "page_width_pt": 960,            ← （可選）實際頁寬；pt 單位時必填
                  "image_width_px": 2000,          ← （可選）px 單位時影像寬度，預設取樣本影像寬
                  "texts": [{"text": "標題", "bbox": [x0, y0, x1, y1]  或 "quad": [[x,y]×4（左上、右上、右下、左下）],
                             "size_pt": 18, "font": "Arial" 或 ["Arial", ...], "color": "#1F2937",
                             "bold": false, "italic": false,
                             "paragraph": "p1",     ← （可選）同一段落的行填同一個鍵
                             "box": "b1",           ← （可選）同一文字框的段落填同一個鍵
                             "baseline": [[x,y],[x,y]]}],   ← （可選）
                  "images": [{"bbox": [...], "kind": "photo"}],
                  "shapes": [{"bbox": [...], "geometry": "rect" | "line" | "ellipse" | "path"}]}]}
      texts 的順序就是閱讀順序。
    產物格式（由 write_truth_template 產生後再修改）：每頁 {"page_index", "page_width_pt", "elements": ElementList, "layout": PageLayout}。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    units = data.get("units", "rel")
    out = []
    for pg in data.get("pages", []):
        pi = int(pg.get("page_index", 0))
        pw = pg.get("page_width_pt")
        if "elements" in pg:
            el = ElementList.model_validate(pg["elements"])
            lay = PageLayout.model_validate(pg["layout"]) if pg.get("layout") else None
            groups = M.paragraph_groups(lay) if lay else None
            out.append(TruthPage(pi, el, "rel", pw, None, groups, M.line_order(lay, el) if lay else None, len(lay.text_boxes) if lay else None, True))
            continue
        iw = float(pg.get("image_width_px") or image_width_px or 1.0)
        k = {"rel": 1.0, "px": 1.0 / iw, "pt": 1.0 / float(pw or 1.0)}[units]
        if units == "pt" and not pw:
            raise ValueError(f"{path}：units 為 pt 時每頁必須給 page_width_pt")
        texts, groups, boxes = [], {}, {}
        has_bl = False
        for n, t in enumerate(pg.get("texts", [])):
            q = _quad_of(t, k)
            lid = f"truth-t{n:04d}"
            size_rel = float(t.get("size_pt", 0.0)) / float(pw) if pw and t.get("size_pt") else float(t.get("size_rel", q.height * 0.8))
            fonts = t.get("font") or []
            fonts = [fonts] if isinstance(fonts, str) else fonts
            if t.get("baseline"):
                has_bl = True
                (bx0, by0), (bx1, by1) = t["baseline"]
                bl = (Point(x=bx0 * k, y=by0 * k), Point(x=bx1 * k, y=by1 * k))
            else:
                bl = (q.bl, q.br)
            run = TextRun(text=t["text"], quad=q, font_size=measured(size_rel, 1.0, Provenance.USER, _W),
                          font_candidates=[FontCandidate(name=f) for f in fonts],
                          bold=measured(bool(t.get("bold", False)), 1.0, Provenance.USER, _W),
                          italic=measured(bool(t.get("italic", False)), 1.0, Provenance.USER, _W),
                          color=measured(_hex(t.get("color")), 1.0, Provenance.USER, _W))
            texts.append(TextLine(id=lid, quad=q, z=n, baseline=bl, runs=[run]))
            if t.get("paragraph") is not None:
                groups[lid] = str(t["paragraph"])
                if t.get("box") is not None:
                    boxes[str(t["box"])] = True
        images = [ImageElement(id=f"truth-i{n:04d}", quad=_quad_of(i, k), z=n, image=_DUMMY_BLOB, pixel_size=(1, 1),
                               kind=measured(ImageKind(i.get("kind", "photo")), 1.0, Provenance.USER, _W)) for n, i in enumerate(pg.get("images", []))]
        shapes = [ShapeElement(id=f"truth-s{n:04d}", quad=_quad_of(s, k), z=n, geometry=ShapeGeometry(s.get("geometry", "rect"))) for n, s in enumerate(pg.get("shapes", []))]
        el = ElementList(page_index=pi, production_line="image", texts=texts, images=images, shapes=shapes)
        out.append(TruthPage(pi, el, units, float(pw) if pw else None, iw if units == "px" else None, groups or None,
                             [t.id for t in texts], len(boxes) or None, has_bl))
    return out


def write_truth_template(run: RunArtifacts, path: Path) -> Path:
    """把一次流水線的結果寫成簡易格式的 .truth.json（單位 rel），讓人修改後當標準答案。"""
    pages = []
    for spec, el in zip(run.specs, run.elements):
        pl = page_layout(run, spec.page_index)
        groups = M.paragraph_groups(pl) if pl else {}
        box_of = {pid: b.id for b in (pl.text_boxes if pl else []) for pid in b.paragraph_ids}
        order = M.line_order(pl, el) if pl else [t.id for t in el.texts]
        by_id = {t.id: t for t in el.texts}
        pw = spec.page_width_pt.value
        texts = []
        for lid in order:
            t = by_id[lid]
            r = max(t.runs, key=lambda r: len(r.text))
            texts.append({"text": t.text, "quad": [[p.x, p.y] for p in t.quad.points], "size_pt": round(t.font_size * pw, 3),
                          "font": [f.name for f in r.font_candidates[:3]], "color": "#" + r.color.value.hex, "bold": r.bold.value,
                          "italic": r.italic.value, "paragraph": groups.get(lid), "box": box_of.get(groups.get(lid)),
                          "baseline": [[t.baseline[0].x, t.baseline[0].y], [t.baseline[1].x, t.baseline[1].y]]})
        images = [{"quad": [[p.x, p.y] for p in i.quad.points], "kind": i.kind.value.value} for i in el.images if i.kind.value != ImageKind.FULL_PAGE]
        shapes = [{"quad": [[p.x, p.y] for p in s.quad.points], "geometry": s.geometry.value} for s in el.shapes if not s.is_page_background]
        pages.append({"page_index": spec.page_index, "page_width_pt": pw, "texts": texts, "images": images, "shapes": shapes})
    Path(path).write_text(json.dumps({"units": "rel", "pages": pages}, ensure_ascii=False, indent=1), encoding="utf-8")
    return Path(path)


# ====================================================================== 單一樣本


def _err(where: str, e: BaseException) -> dict[str, str]:
    return {"where": where, "error": f"{type(e).__name__}: {e}", "traceback": "".join(traceback.format_exception(e)[-3:])}


def _page_kind(run: RunArtifacts) -> tuple[bool, str]:
    kinds = [r.input_type.value for r in run.doc.pages]
    native = run.doc.media_type == "application/pdf" and all(k in (InputType.NATIVE_PDF, InputType.MIXED_PDF) for k in kinds)
    return native, ("native_pdf" if native else "image_" + (kinds[0].value if kinds else "unknown"))


def _rasterized(run: RunArtifacts, acc: M.Accumulator, mp: dict) -> None:
    for spec, el in zip(run.specs, run.elements):
        acc.value("rasterized_page", 1.0 if M.is_rasterized(el, spec.aspect_ratio, mp) else 0.0)


def _feedback_rating(sample: Sample) -> Optional[float]:
    if not sample.feedback_path:
        return None
    from .feedback import load_feedback

    fb = load_feedback(sample.feedback_path)
    vals = [r.score / r.max_score for r in fb.page_ratings if r.max_score]
    return float(np.mean(vals)) if vals else None


def evaluate_sample(sample: Sample, out_dir: Path, cfg: Optional[Config] = None, params: dict | None = None) -> dict[str, Any]:
    """評一個樣本：主評測（直接跑流水線）＋原生 PDF 的自我監督評測。不丟例外，錯誤記在 errors。"""
    p = _p(params)
    cfg = cfg or Config.load()
    mp = p["metrics"]
    result: dict[str, Any] = {"sample": sample.key, "group": sample.group, "file": str(sample.path), "has_truth": bool(sample.truth_path),
                              "has_feedback": bool(sample.feedback_path), "evaluations": [], "errors": []}
    job_root = Path(out_dir) / "jobs" / sample.safe_key
    try:
        _, run = run_pipeline(job_root / "main", sample.path, cfg, p["outputs"])
    except Exception as e:  # noqa: BLE001 —— 單一樣本失敗不中斷整批
        result["errors"].append(_err("main", e))
        return result
    native, etype = _page_kind(run)
    result.update({"media_type": run.doc.media_type, "pages": len(run.doc.pages), "type": etype,
                   "input_types": [r.input_type.value.value for r in run.doc.pages]})
    # ---- (a) 主評測
    acc = M.Accumulator()
    M.compare_end_to_end(run.report, acc)
    _rasterized(run, acc, mp)
    try:
        rating = _feedback_rating(sample)
        acc.value("user_rating", rating)
    except Exception as e:  # noqa: BLE001
        result["errors"].append(_err("feedback", e))
    truth_note = None
    if sample.truth_path:
        try:
            iw = run.doc.pages[0].pixel_size[0] if run.doc.pages and run.doc.pages[0].pixel_size else None
            for tp in load_truth(sample.truth_path, iw):
                if tp.page_index >= len(run.specs):
                    continue
                spec, el = run.specs[tp.page_index], run.elements[tp.page_index]
                h = np.eye(3)
                if tp.units == "px":  # 預測頁面相對 → 影像像素 → 除以影像寬
                    h = np.diag([1.0 / tp.image_width_px, 1.0 / tp.image_width_px, 1.0]) @ np.linalg.inv(mat3(spec.source_to_page))
                pred = M.transform_elements(el, h) if not np.allclose(h, np.eye(3)) else el
                c = tp.elements.texts[0].quad.center if tp.elements.texts else Point(x=0.5, y=0.3)
                pw = tp.page_width_pt or spec.page_width_pt.value / max(M.local_scale(h, c.x, c.y), 1e-12)
                if not tp.page_width_pt:
                    truth_note = "標準答案沒有給 page_width_pt：pt 換算用系統推定的頁寬，pt 類指標僅供參考。"
                M.evaluate_page(pred, tp.elements, pw, page_layout(run, tp.page_index), None, tp.groups, tp.order, mp, acc, tp.box_count, tp.has_baseline)
        except Exception as e:  # noqa: BLE001
            result["errors"].append(_err("truth", e))
    result["evaluations"].append({"variant": "native" if native else "original", "type": etype, "metrics": acc.finalize(mp), "counts": acc.counts,
                                  "seconds": round(run.seconds, 2), "confidence": confidence_summary(run), "notes": [truth_note] if truth_note else []})
    # ---- (b) 自我監督（原生 PDF）
    if native:
        for variant in p["variants"]:
            try:
                result["evaluations"].append(_self_supervised(sample, run, variant, job_root, Path(out_dir), cfg, p))
            except Exception as e:  # noqa: BLE001
                result["errors"].append(_err(f"synth_{variant}", e))
    return result


def _curved_kind(variant: str) -> Optional[str]:
    k = variant[len("curved_"):] if variant.startswith("curved_") else None
    return k if k in CURVED_KINDS else None


def _working_to_photo(pspec: PageSpec, store) -> Callable[[np.ndarray], np.ndarray]:
    """影像產線的頁面相對座標 → 照片像素：有攤平對應圖（PageSpec.dewarp_map）就在圖上取樣，否則用 source_to_page 的反矩陣。"""
    hinv = np.linalg.inv(mat3(pspec.source_to_page))
    if pspec.dewarp_map is not None:
        import cv2

        from ..recognition.dewarp import load_maps

        mx, my = load_maps(store.blob_path(pspec.dewarp_map))  # 新（粗網格）舊（逐像素）兩種格式都讀得懂
        ww = float(pspec.working_image_size[0])

        def fn(pts: np.ndarray) -> np.ndarray:
            q = np.asarray(pts, np.float64).reshape(-1, 2) * ww - 0.5
            n = len(q)
            if n == 0:
                return np.zeros((0, 2), np.float64)
            # cv2.remap 的查詢圖每一維不能超過 32767：補齊成 1024 的倍數、排成 (列, 1024) 再查
            cols = 1024
            pad = (-n) % cols
            xs = np.pad(q[:, 0], (0, pad)).astype(np.float32).reshape(-1, cols)
            ys = np.pad(q[:, 1], (0, pad)).astype(np.float32).reshape(-1, cols)
            x = cv2.remap(mx, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()[:n]
            y = cv2.remap(my, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).ravel()[:n]
            return np.stack([x, y], 1).astype(np.float64)
        return fn

    def fn_h(pts: np.ndarray) -> np.ndarray:
        h = np.hstack([np.asarray(pts, np.float64), np.ones((len(pts), 1))]) @ hinv.T
        return h[:, :2] / h[:, 2:3]
    return fn_h


def _curved_extra_metrics(pspec: PageSpec, store, page_map: np.ndarray, w0: float, h0: float, pred, truth_el, acc: M.Accumulator) -> None:
    """彎曲照片才有的指標：工作影像每個像素對回原頁面的位置誤差 p95、頁面覆蓋率、背景比例；以原生 PDF 文字層為答案的 CER。"""
    to_photo = _working_to_photo(pspec, store)
    ww, wh = pspec.working_image_size
    step = 4
    ys, xs = np.mgrid[0:wh:step, 0:ww:step].astype(np.float64) + 0.5
    ph = to_photo(np.stack([xs.ravel() / ww, ys.ravel() / ww], 1))
    full_x = np.full((wh, ww), np.nan, np.float32)
    full_y = np.full((wh, ww), np.nan, np.float32)
    iy, ix = (ys.ravel() - 0.5).astype(int), (xs.ravel() - 0.5).astype(int)
    full_x[iy, ix], full_y[iy, ix] = ph[:, 0], ph[:, 1]
    g = M.dewarp_geometry(full_x, full_y, page_map, (w0, h0), step=step)
    for k in ("dewarp_pos_p95_px", "page_coverage", "background_frac"):
        acc.value(k, g.get(k))
    truth_lines = [{"text": t.text, "bbox": [min(q.x for q in t.quad.points) * w0, min(q.y for q in t.quad.points) * w0,
                                             max(q.x for q in t.quad.points) * w0, max(q.y for q in t.quad.points) * w0]} for t in truth_el.texts]
    ocr = [(t.text, (t.quad.center.x * w0, t.quad.center.y * w0)) for t in pred.texts]
    if truth_lines:
        acc.value("pdf_text_cer", M.pdf_text_cer(ocr, truth_lines).get("pdf_text_cer"))


def _self_supervised(sample: Sample, truth_run: RunArtifacts, variant: str, job_root: Path, out_dir: Path, cfg: Config, p: dict) -> dict[str, Any]:
    curved = _curved_kind(variant)
    if variant not in KINDS and curved is None:
        raise ValueError(f"不認得的干擾種類：{variant}")
    mp = p["metrics"]
    acc = M.Accumulator()
    infos, secs, conf = [], 0.0, {}
    tmp_dir = (out_dir / "degraded" / sample.safe_key) if p["save_degraded_images"] else (job_root / "_degraded")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pages = truth_run.specs[: int(p["max_pages_per_sample"])]
    for spec in pages:
        i = spec.page_index
        main_store = Pipeline(job_root / "main", cfg).store
        image = np.asarray(Image.open(main_store.blob_path(spec.working_image)).convert("RGB"))
        seed = variant_seed(p["seed"], sample.key, i, variant)
        page_map = None
        if curved:
            img, page_map, info = degrade_curved(image, curved, seed, p["strength"], p["synth"])
            mat = None
            info = {k: v for k, v in info.items() if k != "page_outline"}  # 外框折線很長，不寫進報表
        else:
            img, mat, info = degrade(image, variant, seed, p["strength"], p["synth"])
        info["page_index"] = i
        src = tmp_dir / f"{Path(sample.key).stem}.p{i + 1:03d}.{variant}.png"
        Image.fromarray(img).save(src, compress_level=1)
        _, run = run_pipeline(job_root / f"{variant}-p{i + 1:03d}", src, cfg, p["outputs"])
        secs += run.seconds
        pspec, pel = run.specs[0], run.elements[0]
        w0 = float(spec.working_image_size[0])
        if curved:
            # 預測（照片的頁面相對座標）→ 照片像素 → 逐像素真值 → 原頁面像素 → 原頁面相對座標（對不到紙的元素算漏抓）
            to_photo = _working_to_photo(pspec, run_store(run))

            def to_page(pts: np.ndarray, to_photo=to_photo, page_map=page_map) -> np.ndarray:
                ph = to_photo(pts)
                u_, v_, ok = sample_page_map(page_map, ph[:, 0], ph[:, 1])
                out = np.stack([u_, v_], 1).astype(np.float64) / w0
                out[~ok] = np.nan
                return out
            pred = M.transform_elements_pointwise(pel, to_page)
        else:
            # 預測（干擾影像的頁面相對座標）→ 原頁面相對座標
            to_pred = mat3(pspec.source_to_page) @ mat @ np.diag([w0, w0, 1.0])
            h = np.linalg.inv(to_pred)
            pred = M.transform_elements(pel, h)
        M.evaluate_page(pred, truth_run.elements[i], spec.page_width_pt.value, page_layout(run, 0), page_layout(truth_run, i), params=mp, acc=acc)
        if curved:
            _curved_extra_metrics(pspec, run_store(run), page_map, w0, float(spec.working_image_size[1]), pred, truth_run.elements[i], acc)
        M.compare_end_to_end(run.report, acc)
        _rasterized(run, acc, mp)
        info["image"] = str(src) if p["save_degraded_images"] else None
        infos.append(info)
        conf = confidence_summary(run)
    if not p["save_degraded_images"]:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"variant": variant, "type": f"synth_{variant}", "metrics": acc.finalize(mp), "counts": acc.counts, "seconds": round(secs, 2),
            "degrade": infos, "confidence": conf, "notes": []}


# ====================================================================== 總表


def _mean_table(rows: list[dict]) -> dict[str, dict[str, float]]:
    vals: dict[str, list[float]] = {}
    for r in rows:
        for k, v in r["metrics"].items():
            if v is not None:
                vals.setdefault(k, []).append(float(v))
    return {k: {"mean": float(np.mean(v)), "n": len(v), "min": float(np.min(v)), "max": float(np.max(v))} for k, v in vals.items()}


def summarize(results: list[dict], params: dict | None = None, baseline: Optional[dict] = None, cfg: Optional[Config] = None) -> dict[str, Any]:
    p = _p(params)
    rows = [{"sample": r["sample"], "group": r["group"], "type": e["type"], "variant": e["variant"], "metrics": e["metrics"]}
            for r in results for e in r.get("evaluations", [])]
    by_type: dict[str, list] = {}
    by_group: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row)
        by_group.setdefault(row["group"], []).append(row)
    scored = [r for r in rows if r["metrics"].get("composite") is not None]
    worst = sorted(scored, key=lambda r: r["metrics"]["composite"])[: int(p["worst_n"])]
    errors = [{"sample": r["sample"], **e} for r in results for e in r.get("errors", [])]
    summary: dict[str, Any] = {
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "config_hash": (cfg or Config.load()).key(*sorted((cfg or Config.load()).data.keys())),
        "n_samples": len(results), "n_evaluations": len(rows), "n_failed_samples": sum(1 for r in results if not r.get("evaluations")),
        "metric_meta": M.metric_meta(),
        "overall": _mean_table(rows),
        "by_type": {k: _mean_table(v) for k, v in sorted(by_type.items())},
        "by_group": {k: _mean_table(v) for k, v in sorted(by_group.items())},
        "worst": [{"sample": r["sample"], "type": r["type"], "group": r["group"], "composite": r["metrics"]["composite"],
                   **{k: r["metrics"].get(k) for k in ("text_f1", "page_cer", "e2e_score", "rasterized_page")}} for r in worst],
        "errors": errors,
        "params": {k: v for k, v in p.items() if k not in ("md_metrics_by_type",)},
    }
    if baseline:
        summary["baseline"] = compare_to_baseline(summary, baseline, p)
    return summary


def compare_to_baseline(summary: dict, baseline: dict, params: dict | None = None) -> dict[str, Any]:
    """逐指標比較：overall 與 by_type。退步超過容許範圍的標成 regression。"""
    p = _p(params)
    tol = p["regression_tolerance"]

    def diff(cur: dict, base: dict) -> dict[str, Any]:
        out = {}
        for k in sorted(set(cur) & set(base)):
            info = M.METRICS.get(k)
            if info is None:
                continue
            c, b = cur[k]["mean"], base[k]["mean"]
            d = c - b
            worse = -d if info.higher_is_better else d
            t = float(tol.get(k, info.tolerance))
            out[k] = {"current": c, "baseline": b, "delta": d, "status": "regression" if worse > t else ("improved" if -worse > t else "same"), "tolerance": t}
        return out

    res = {"created": baseline.get("created"), "overall": diff(summary["overall"], baseline.get("overall", {})),
           "by_type": {t: diff(v, baseline.get("by_type", {}).get(t, {})) for t, v in summary["by_type"].items()}}
    res["regressions"] = [{"scope": "overall", "metric": k, **v} for k, v in res["overall"].items() if v["status"] == "regression"]
    res["regressions"] += [{"scope": t, "metric": k, **v} for t, d in res["by_type"].items() for k, v in d.items() if v["status"] == "regression"]
    return res


def _fmt(v: Optional[float], unit: str = "比例") -> str:
    if v is None:
        return "—"
    if unit == "比例":
        return f"{v:.3f}"
    return f"{v:.2f}"


def render_markdown(summary: dict, params: dict | None = None) -> str:
    p = _p(params)
    meta = summary["metric_meta"]
    L: list[str] = [f"# Mortise 評測報告", "", f"- 產生時間：{summary['created']}", f"- 樣本數：{summary['n_samples']}（評測 {summary['n_evaluations']} 次；完全失敗 {summary['n_failed_samples']} 個樣本；錯誤 {len(summary['errors'])} 筆）",
                    f"- 設定雜湊：`{summary['config_hash']}`", "- 方向：↑ 越高越好，↓ 越低越好。平均是對「評測」做的平均（每個樣本的每種評測算一筆）。", ""]
    base = summary.get("baseline")
    L += ["## 各指標平均", ""]
    head = "| 指標 | 方向 | 單位 | 平均 | 筆數 |" + (" 基準 | 變化 | 狀態 |" if base else "")
    L += [head, "|" + "---|" * (head.count("|") - 1)]
    status_txt = {"regression": "**退步**", "improved": "進步", "same": "持平"}
    for k, info in meta.items():
        if k not in summary["overall"]:
            continue
        v = summary["overall"][k]
        row = f"| {info['label']} | {'↑' if info['higher_is_better'] else '↓'} | {info['unit']} | {_fmt(v['mean'], info['unit'])} | {v['n']} |"
        if base:
            d = base["overall"].get(k)
            row += f" {_fmt(d['baseline'], info['unit'])} | {d['delta']:+.3f} | {status_txt[d['status']]} |" if d else " — | — | 新指標 |"
        L.append(row)
    for title, table, labels in (("依評測類型", summary["by_type"], TYPE_LABELS), ("依分組", summary["by_group"], {})):
        if not table:
            continue
        cols = list(table.keys())
        L += ["", f"## {title}", "", "| 指標 | " + " | ".join(labels.get(c, c) for c in cols) + " |", "|---|" + "---|" * len(cols)]
        for k in p["md_metrics_by_type"]:
            if not any(k in table[c] for c in cols):
                continue
            info = meta[k]
            L.append(f"| {info['label']}{'↑' if info['higher_is_better'] else '↓'} | " + " | ".join(_fmt(table[c][k]["mean"], info["unit"]) if k in table[c] else "—" for c in cols) + " |")
    L += ["", f"## 最差的 {len(summary['worst'])} 個評測（依綜合分數）", "", "| 樣本 | 類型 | 分組 | 綜合分數 | 文字行 F1 | 整頁字元錯誤率 | 還原度總分 | 整頁點陣化 |", "|---|---|---|---|---|---|---|---|"]
    for w in summary["worst"]:
        L.append(f"| {w['sample']} | {TYPE_LABELS.get(w['type'], w['type'])} | {w['group']} | {_fmt(w['composite'])} | {_fmt(w.get('text_f1'))} | {_fmt(w.get('page_cer'))} | {_fmt(w.get('e2e_score'))} | {_fmt(w.get('rasterized_page'))} |")
    if base:
        L += ["", "## 與基準相比的退步", ""]
        regs = base["regressions"]
        if not regs:
            L.append("沒有超過容許範圍的退步。")
        for r in regs:
            info = meta[r["metric"]]
            scope = "總體" if r["scope"] == "overall" else TYPE_LABELS.get(r["scope"], r["scope"])
            L.append(f"- {scope}／{info['label']}：{_fmt(r['baseline'], info['unit'])} → {_fmt(r['current'], info['unit'])}（變化 {r['delta']:+.3f}，容許 {r['tolerance']}）")
    if summary["errors"]:
        L += ["", "## 錯誤", ""]
        for e in summary["errors"]:
            L.append(f"- {e['sample']}（{e['where']}）：{e['error']}")
    return "\n".join(L) + "\n"


# ====================================================================== 批次


def run_batch(sample_dir: Path, out_dir: Optional[Path] = None, baseline: Optional[Path | dict] = None, variants: Optional[list[str]] = None,
              limit: Optional[int] = None, cfg: Optional[Config] = None, params: dict | None = None,
              on_progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """跑整批評測並寫出三種輸出。回傳總表 dict。"""
    p = _p(params)
    if variants is not None:
        p["variants"] = variants
    sample_dir = Path(sample_dir)
    out_dir = Path(out_dir) if out_dir else sample_dir.parent / f"{sample_dir.name}-eval"
    (out_dir / "samples").mkdir(parents=True, exist_ok=True)
    cfg = cfg or Config.load()
    say = on_progress or (lambda s: None)
    results = []
    samples = discover(sample_dir, limit)
    for n, s in enumerate(samples, 1):
        say(f"[{n}/{len(samples)}] {s.key}")
        try:
            r = evaluate_sample(s, out_dir, cfg, p)
        except Exception as e:  # noqa: BLE001 —— 最後一道防線
            r = {"sample": s.key, "group": s.group, "file": str(s.path), "evaluations": [], "errors": [_err("evaluate_sample", e)]}
        (out_dir / "samples" / f"{s.safe_key}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        results.append(r)
    base = None
    if baseline is not None:
        base = baseline if isinstance(baseline, dict) else json.loads(Path(baseline).read_text(encoding="utf-8"))
    summary = summarize(results, p, base, cfg)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(summary, p), encoding="utf-8")
    say(f"完成：{out_dir / 'summary.md'}")
    return summary


def _stderr(s: str) -> None:
    print(s, file=sys.stderr)
