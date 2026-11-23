"""成績單：一個引擎版本（見 mortise/engine.py）在固定考卷上的成績。

    python -m mortise.eval scorecard [--quick] --out 目錄 [--jobs 2] [--seed 0] [--config 覆寫檔.toml]
    python -m mortise.eval scorecard compare 舊.json 新.json [--gate 指標=容許量 ...] [--gates 門檻檔.json] [--md 輸出.md]

一、考卷（固定、可重現）
  從 tests/fixtures 的原生 PDF（加上一張程式產生的表格頁）出題：用 synth.py 以固定種子把每一頁做成
  截圖、掃描、平的手機照片、彎曲的照片（弓起、捲邊、捲角、折痕），每一題都跑完整的七個階段。
  答案來自原生 PDF 本身（文字、字級、位置、表格），所以不需要人工標準答案。
  --quick：3 份文件、7 題影像加 3 題原生（4 核機器、--jobs 2 實測 106 秒）；預設是完整版（7 份文件、32 題，實測 7 分 29 秒；
  都是 2026-11-23 在同時有其他工作的機器上量的，見 docs/scorecards/README.md）。
  同一個考卷、同一個種子、同一個引擎版本，分數完全一樣（處理時間除外，它受機器負載影響）。

二、記錄的指標（每一題各一份，再依輸入類型平均）
  文字：整頁字元錯誤率 page_cer（含漏抓多抓）、已配對行的 cer、文字行 F1、字級誤差 size_error_pt（pt）、
        彎曲照片另有以 PDF 文字層為答案的 pdf_text_cer。
  ⑦驗證：還原度總分 e2e_score，以及每個輸出格式各自的分數 fmt_<格式>（可編輯 PDF、PowerPoint、Word 等）。
  照片四角：每張平的與彎曲的照片都直接跑 page_geometry.find_page_quad，與合成時的真實四角比：
        quad_success（四個角都在影像對角線 1% 以內，門檻取設定 [page] quad_max_corner_err_frac）、
        quad_corner_err_pct（最差一角的誤差佔對角線 %）、quad_flagged（系統會請使用者確認四角的比例）、
        quad_confident_wrong（錯了卻沒有請使用者確認的比例；這是最危險的錯，應該是 0）。
        以上量的是偵測器本身；另外 page_quad_success、page_quad_review、page_quad_silent_wrong 量整條流程：
        ②最後用的四角（PageSpec.source_corners）與②自己判斷要不要請使用者確認（quad_needs_review、dewarp_needs_review）。
  表格：table_found（原生 PDF 有表格的頁，影像版也找到表格的比例）、table_shape_exact（列數與欄數完全一致的比例）。
  時間：seconds_per_page（每頁從①到⑦的秒數）；成績單也記整份考卷的總時間。
  另外保留評測模組（runner.py）算出的其他指標（字型命中率、顏色差、段落分組……），都在 JSON 裡。

三、輸出（--out 目錄）
  <引擎版本>.json   完整成績（mortise.scorecard/1）：引擎版本與組成、考卷、每一題的指標、依輸入類型的平均、重點、錯誤
  <引擎版本>.md     給人看的繁體中文成績單
  工作目錄（流水線的中間產物）預設放在暫存目錄、跑完刪掉；--work 目錄 可以保留（會放在 目錄/<引擎版本>/ 底下，
  不同引擎版本不共用快取：快取鍵不含程式碼雜湊，共用會拿到舊程式的結果）。
  結束碼：全部題目都跑完是 0；有題目出錯（錯誤列在成績單最後）是 4。

四、比較（「新模型要比舊模型好才採用」的閘門）
  compare 依每個指標的方向算進步或退步，總體與每一種輸入類型各比一次（照片退步不會被截圖的進步蓋掉）。
  平均是「配對」的：每個指標只用兩邊都有這個指標的題目算平均，兩邊比的一定是同一批題目。
  設了門檻的指標（預設見 DEFAULT_GATES，可用 --gate 指標=容許量 或 --gates 檔案 改；容許量可寫百分比，例如 seconds_per_page=50%）
  退步超過容許量時算「超過門檻的退步」。另外這三種情況也直接算超過門檻的退步（新版本當掉，不能當成沒事）：
    - 缺題：舊成績單有、新成績單沒有的題目（同一個樣本、同一種干擾）；
    - 缺指標：同一題舊的有、新的沒有的指標（只算設了門檻的指標；沒設門檻的只列出）；
    - 新的錯誤：新成績單 errors 裡、舊成績單沒有的錯誤（同一個樣本、同一個地方）。
  確定要放行時（例如故意拿掉某種題目），加 --allow-missing：這三種只列出、不算退步。
  考卷本身（考卷檔與出題、評分的程式）的指紋記在 exam；兩份不同時報表上用粗體警告，加 --strict-exam 時結束碼是 3。
  結束碼：沒有超過門檻的退步是 0；有是 1；檔案讀不到或格式不對是 2；--strict-exam 而考卷不同是 3。可以直接接 CI。

已知限制：
- 考卷是合成的：真實照片的光線、鏡頭變形、摩爾紋不在裡面（見 synth.py 的已知限制）；真實照片的成績要另外用回饋資料評。
- 每一種輸入類型的題數不多（quick 只有 1 到 3 題），平均值的顆粒很粗：一題錯就差很多。門檻的預設值是照這個顆粒定的。
- 表格只比列數與欄數，不比儲存格內容與合併儲存格。
- 處理時間受機器負載影響，預設不設門檻（只列出）。
- 各格式分數讀自⑦報告第一行的說明文字（報告目前沒有結構化的欄位）；讀不到就留空。
- 每一份文件都在新的子行程裡評（--jobs 1 也是）：文字辨識模型在一個行程裡只載入一次，
  同一個行程連跑兩份不同設定的成績單會沿用第一次的模型。

可單獨升級的點：考卷可以加入真實照片（附人工四角與文字答案）；表格可改比儲存格內容；各格式分數改讀結構化欄位。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ..config import Config
from . import metrics as M

SCHEMA = "mortise.scorecard/1"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


# ====================================================================== 考卷


@dataclass
class Item:
    """考卷上的一份文件：哪個檔、評前幾頁、做哪些干擾。"""

    name: str  # 樣本名（成績單裡的鍵）
    source: str  # "fixture:檔名"（tests/fixtures 底下）或 "synth:table"（程式產生的表格頁）
    group: str  # 分組（簡報、文件、履歷、海報、表格）
    variants: list[str]
    pages: int = 1  # 自我監督評前幾頁


SUITES: dict[str, list[Item]] = {
    "quick": [
        Item("slide_basic.pdf", "fixture:slide_basic.pdf", "簡報", ["screenshot", "photo"]),
        Item("doc_a4.pdf", "fixture:doc_a4.pdf", "文件", ["scan", "photo", "curved_curl_top"]),
        Item("cv_two_column.pdf", "fixture:cv_two_column.pdf", "履歷", ["screenshot", "curved_bend"]),
    ],
    "full": [
        Item("slide_basic.pdf", "fixture:slide_basic.pdf", "簡報", ["screenshot", "scan", "photo", "curved_bend"]),
        Item("slide_dark.pdf", "fixture:slide_dark.pdf", "簡報", ["screenshot", "photo"]),
        Item("deck.pdf", "fixture:deck.pdf", "簡報", ["screenshot", "photo"], pages=2),
        Item("doc_a4.pdf", "fixture:doc_a4.pdf", "文件", ["screenshot", "scan", "photo", "curved_flat", "curved_curl_top", "curved_corner_curl"]),
        Item("cv_two_column.pdf", "fixture:cv_two_column.pdf", "履歷", ["screenshot", "scan", "photo", "curved_bend", "curved_crease"]),
        Item("poster.pdf", "fixture:poster.pdf", "海報", ["screenshot", "photo", "curved_bend"]),
        Item("table_synth.pdf", "synth:table", "表格", ["scan", "photo", "curved_curl_crease"]),
    ],
}
# 每一題要求的輸出格式（⑦會逐一驗證，分數記在 fmt_<格式>）
SUITE_OUTPUTS = {"quick": ["pdf", "pptx", "docx_flow"], "full": ["pdf", "svg", "pptx", "docx_flow", "xlsx"]}

# 輸入類型（成績單依這些分組平均）
CATEGORY_LABELS = {"native": "原生 PDF", "screenshot": "截圖", "scan": "掃描", "photo": "平的照片", "curved": "彎曲的照片"}
VARIANT_LABELS = {"native": "原生 PDF", "screenshot": "截圖", "scan": "掃描", "photo": "平的照片", "curved_flat": "照片（3D 平紙）",
                  "curved_bend": "弓起的照片", "curved_curl_top": "上緣捲起的照片", "curved_corner_curl": "捲角的照片",
                  "curved_crease": "有折痕的照片", "curved_curl_crease": "捲起加折痕的照片"}


def category(variant: str) -> str:
    return "curved" if variant.startswith("curved_") else ("native" if variant in ("native", "original") else variant)


# ====================================================================== 指標說明


@dataclass(frozen=True)
class Info:
    label: str
    unit: str  # 比例、pt、%、秒
    higher_is_better: bool
    gate: Optional[str] = None  # 預設的「不可退步」容許量（"0.01" 絕對值、"50%" 相對值）；None 表示只列出、不設門檻


_FMT_LABELS = {"pdf": "可編輯 PDF", "svg": "SVG", "pptx": "PowerPoint", "docx_flow": "Word（可重排）", "docx_fixed": "Word（版面固定）",
               "xlsx": "Excel", "ai_pdf": "給 Illustrator 的 PDF", "idml": "InDesign", "google_slides": "Google 簡報", "template": "模板",
               "svg_assets": "向量圖 SVG"}

SC_METRICS: dict[str, Info] = {
    "page_cer": Info("文字錯字率（整頁，含漏字多字）", "比例", False, "0.01"),
    "cer": Info("文字錯字率（找到的行）", "比例", False, None),
    "pdf_text_cer": Info("文字錯字率（彎曲照片，以 PDF 文字層為答案）", "比例", False, "0.02"),
    "text_f1": Info("文字行找到率（F1）", "比例", True, "0.02"),
    "size_error_pt": Info("字級誤差", "pt", False, "0.25"),
    "font_top1": Info("字型命中率（第一名）", "比例", True, None),
    "e2e_score": Info("還原度總分（⑦）", "比例", True, "0.02"),
    **{f"fmt_{k}": Info(f"⑦ {v} 分數", "比例", True, "0.02") for k, v in _FMT_LABELS.items()},
    "quad_success": Info("照片四角成功率（誤差 ≤ 對角線 1%）", "比例", True, "0"),
    "quad_corner_err_pct": Info("照片四角最大誤差（佔對角線）", "%", False, None),
    "quad_flagged": Info("請使用者確認四角的比例", "比例", False, None),
    "quad_confident_wrong": Info("四角錯了卻沒請使用者確認的比例", "比例", False, "0"),
    "page_quad_success": Info("照片四角成功率（整條流程，②最後用的四角）", "比例", True, "0"),
    "page_quad_review": Info("流程請使用者確認四角的比例", "比例", False, None),
    "page_quad_silent_wrong": Info("流程用錯的四角卻沒請使用者確認的比例", "比例", False, "0"),
    "table_found": Info("表格找到率", "比例", True, "0"),
    "table_shape_exact": Info("表格列數欄數完全正確率", "比例", True, "0"),
    "rasterized_page": Info("整頁變成圖片（文字不可編輯）的比例", "比例", False, "0"),
    "composite": Info("綜合分數（評測模組）", "比例", True, "0.02"),
    "seconds_per_page": Info("每頁處理時間", "秒", False, None),
}
DEFAULT_GATES = {k: v.gate for k, v in SC_METRICS.items() if v.gate is not None}
HEADLINE = ["page_cer", "size_error_pt", "e2e_score", "fmt_pdf", "fmt_pptx", "fmt_docx_flow", "quad_success", "quad_confident_wrong",
            "page_quad_success", "page_quad_silent_wrong", "table_shape_exact", "seconds_per_page"]
_IMAGE_SCOPE = ("page_cer", "size_error_pt", "quad_success", "quad_confident_wrong", "page_quad_success", "page_quad_silent_wrong", "table_shape_exact")


def metric_info(name: str) -> Optional[Info]:
    if name in SC_METRICS:
        return SC_METRICS[name]
    m = M.METRICS.get(name)
    if m is None:
        return None
    unit = "比例" if m.unit == "比例" else m.unit
    return Info(m.label, unit, m.higher_is_better, None)


# ====================================================================== 每一題的額外指標


def format_scores(report: Any) -> dict[str, float]:
    """⑦報告裡每個輸出格式的分數：優先讀結構化欄位（format_scores，若日後追加），否則讀報告第一行說明
    「各格式的分數：可編輯 PDF 0.545；SVG 0.553；…」。讀不到的格式不列。"""
    direct = getattr(report, "format_scores", None)
    if isinstance(direct, dict) and direct:
        return {str(k): float(v) for k, v in direct.items() if v is not None}
    try:
        from ..stages.s7_verify import _LABEL

        labels = {f.value: lab for f, lab in _LABEL.items()}
    except Exception:  # noqa: BLE001
        labels = {}
    note = next((n for n in (getattr(report, "notes", None) or []) if n.startswith("各格式的分數：")), "")
    if not note:
        return {}
    body = note[len("各格式的分數："):].split("。")[0]
    out = {}
    for part in body.split("；"):
        for fmt, lab in sorted(labels.items(), key=lambda kv: -len(kv[1])):  # 長的標籤先比（「Word（可重排）」不要被「Word」吃掉）
            m = re.match(re.escape(lab) + r"\s+(\d+(?:\.\d+)?)", part.strip())
            if m:
                out[fmt] = float(m.group(1))
                break
    return out


def _corner_truth(variant: str, info: dict) -> Optional[list]:
    """合成時記下的真實四角：平的照片用 page_corners；彎曲時整張弓起用紙的實體角，捲邊、捲角、折痕用「展平後」的角
    （synth.degrade_curved 的說明：實體的角翹起來了，攤平要找的是平的那片紙延伸出去的角）。"""
    if variant == "photo" or variant == "curved_bend":
        return info.get("page_corners")
    return info.get("flat_corners") or info.get("page_corners")


def _corner_check(pred, truth, size: tuple[int, int], flagged: bool, cfg: Config) -> dict[str, float]:
    diag = math.hypot(*size)
    err = np.linalg.norm(np.asarray(pred, np.float64) - np.asarray(truth, np.float64), axis=1)
    frac = float(err.max() / diag)
    ok = frac <= float(cfg.section("page").get("quad_max_corner_err_frac", 0.01))
    return {"success": float(ok), "err_pct": 100.0 * frac, "flagged": float(flagged), "silent_wrong": float((not ok) and not flagged)}


def quad_metrics(image_path: Path, variant: str, info: dict, cfg: Config) -> dict[str, Any]:
    """照片四角（偵測器本身）：直接跑 find_page_quad，與合成時記下的真實四角比。「請使用者確認」依設定 [page] 的
    quad_review_confidence（信心值低於它、或 status 不是 ok）。"""
    from PIL import Image

    from ..recognition.page_geometry import find_page_quad

    truth = _corner_truth(variant, info)
    if truth is None:
        return {}
    img = np.asarray(Image.open(image_path).convert("RGB"))
    H, W = img.shape[:2]
    t0 = time.perf_counter()
    q = find_page_quad(img, {"input_type": "photo"})
    ms = (time.perf_counter() - t0) * 1000.0
    review = float(cfg.section("page").get("quad_review_confidence", 0.6))
    c = _corner_check(q.corners, truth, (W, H), q.status != "ok" or float(q.confidence) < review, cfg)
    return {"quad_success": c["success"], "quad_corner_err_pct": c["err_pct"], "quad_flagged": c["flagged"], "quad_confident_wrong": c["silent_wrong"],
            "quad_ms": ms, "_quad_status": q.status, "_quad_confidence": float(q.confidence)}


def page_quad_metrics(spec: Any, variant: str, info: dict, cfg: Config) -> dict[str, Any]:
    """照片四角（整條流程）：②最後用的四角（PageSpec.source_corners，原圖像素）與真實四角比，
    「請使用者確認」用②自己的判斷（quad_needs_review 或 dewarp_needs_review）。②還沒有這些欄位時不算。"""
    truth = _corner_truth(variant, info)
    corners = getattr(spec, "source_corners", None)
    size = getattr(spec, "source_size", None) or info.get("output_size")
    if truth is None or not corners or not size or not hasattr(spec, "quad_needs_review"):
        return {}
    pred = [[float(pt.x), float(pt.y)] for pt in corners]
    flagged = bool(spec.quad_needs_review) or bool(getattr(spec, "dewarp_needs_review", False))
    c = _corner_check(pred, truth, (int(size[0]), int(size[1])), flagged, cfg)
    return {"page_quad_success": c["success"], "page_quad_review": c["flagged"], "page_quad_silent_wrong": c["silent_wrong"],
            "_page_quad_status": getattr(spec, "quad_status", None)}


def _table_shapes(layout_page) -> list[tuple[int, int]]:
    if layout_page is None:
        return []
    return [(len(t.row_lines) - 1, len(t.col_lines) - 1) for t in layout_page.tables]


def table_metrics(truth: list[tuple[int, int]], pred: list[tuple[int, int]]) -> dict[str, Optional[float]]:
    """表格：答案有表格時才算。只比列數與欄數（不比位置；一頁的表格不多）。"""
    if not truth:
        return {}
    left = list(pred)
    exact = 0
    for t in truth:
        if t in left:
            left.remove(t)
            exact += 1
    return {"table_found": min(len(pred), len(truth)) / len(truth), "table_shape_exact": exact / len(truth)}


# 程式產生的表格頁：13 列資料＋1 列表頭、4 欄（synth.table_page_pdf）
_SYNTH_TABLE_SHAPE = [(14, 4)]


def _evaluate_item(item: dict, sample_path: str, work: str, cfg_path: Optional[str], outputs: list[str], seed: int) -> dict[str, Any]:
    """（在子行程裡）評一份文件：原生＋各種干擾，再補上成績單自己的指標。不丟例外。"""
    os.environ.setdefault("MORTISE_OFFLINE", "1")
    from ..pipeline import Pipeline
    from .runner import Sample, evaluate_sample, load_artifacts, page_layout

    cfg = Config.load(Path(cfg_path) if cfg_path else None)
    it = Item(**item)
    sample = Sample(Path(sample_path), it.name, it.group)
    t0 = time.time()
    res = evaluate_sample(sample, Path(work), cfg, {"variants": it.variants, "outputs": outputs, "seed": seed, "max_pages_per_sample": it.pages,
                                                    "save_degraded_images": True})
    job_root = Path(work) / "jobs" / sample.safe_key
    out: dict[str, Any] = {"sample": it.name, "group": it.group, "errors": [dict(e, where=e.get("where", "")) for e in res.get("errors", [])],
                           "evaluations": [], "seconds": round(time.time() - t0, 2)}
    truth_run = None
    try:
        truth_run = load_artifacts(Pipeline(job_root / "main", cfg))
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"where": "main", "error": f"{type(e).__name__}: {e}"})
    for ev in res.get("evaluations", []):
        variant = "native" if ev["variant"] in ("native", "original") else ev["variant"]
        mets = {k: v for k, v in ev["metrics"].items() if v is not None}
        extra: dict[str, Any] = {}
        n_pages = max(1, len(ev.get("degrade") or []) or (len(truth_run.specs) if truth_run else 1))
        try:
            if variant == "native":
                runs = [truth_run] if truth_run else []
            else:
                runs = [load_artifacts(Pipeline(job_root / f"{variant}-p{d['page_index'] + 1:03d}", cfg)) for d in ev.get("degrade", [])]
            # ⑦各格式分數（各頁的平均）
            fs: dict[str, list[float]] = {}
            for r in runs:
                for k, v in format_scores(r.report).items():
                    fs.setdefault(k, []).append(v)
            mets.update({f"fmt_{k}": float(np.mean(v)) for k, v in fs.items()})
            # 表格
            tm: dict[str, list[float]] = {}
            if truth_run is not None:
                if variant == "native":
                    pairs = [(_SYNTH_TABLE_SHAPE if it.source == "synth:table" and i == 0 else [], _table_shapes(page_layout(truth_run, i)))
                             for i in range(min(it.pages, len(truth_run.specs)))]
                else:
                    pairs = []
                    for d, r in zip(ev.get("degrade", []), runs):
                        pi = d["page_index"]
                        truth = _SYNTH_TABLE_SHAPE if it.source == "synth:table" and pi == 0 else _table_shapes(page_layout(truth_run, pi))
                        pairs.append((truth, _table_shapes(page_layout(r, 0))))
                for truth, pred in pairs:
                    for k, v in table_metrics(truth, pred).items():
                        tm.setdefault(k, []).append(v)
            mets.update({k: float(np.mean(v)) for k, v in tm.items()})
            # 照片四角
            if variant == "photo" or variant.startswith("curved_"):
                qs: dict[str, list] = {}
                for d, r in zip(ev.get("degrade", []), runs):
                    found = dict(page_quad_metrics(r.specs[0], variant, d, cfg)) if r.specs else {}
                    if d.get("image"):
                        found.update(quad_metrics(Path(d["image"]), variant, d, cfg))
                    for k, v in found.items():
                        qs.setdefault(k, []).append(v)
                for k, v in qs.items():
                    if k.startswith("_"):
                        extra[k[1:]] = v
                    else:
                        mets[k] = float(np.mean(v))
        except Exception as e:  # noqa: BLE001 —— 補充指標失敗不影響評測模組的指標
            out["errors"].append({"where": f"scorecard_{variant}", "error": f"{type(e).__name__}: {e}", "traceback": "".join(traceback.format_exception(e)[-3:])})
        mets["seconds_per_page"] = float(ev.get("seconds") or 0.0) / n_pages
        out["evaluations"].append({"key": f"{it.name}|{variant}", "sample": it.name, "group": it.group, "variant": variant, "category": category(variant),
                                   "pages": n_pages, "seconds": ev.get("seconds"), "metrics": mets, **extra})
    return out


# ====================================================================== 跑考卷


def _sample_path(item: Item, inputs: Path, fixtures: Path) -> Path:
    kind, _, name = item.source.partition(":")
    if kind == "fixture":
        p = fixtures / name
        if not p.exists():
            raise FileNotFoundError(f"找不到考卷用的檔案：{p}（成績單要在原始碼目錄裡跑，或用 --fixtures 指定 tests/fixtures 的位置）")
        return p
    if kind == "synth" and name == "table":
        from .synth import table_page_pdf

        inputs.mkdir(parents=True, exist_ok=True)
        p = inputs / item.name
        if not p.exists():
            p.write_bytes(table_page_pdf(0))
        return p
    raise ValueError(f"不認得的考卷來源：{item.source}")


# 出題與評分的程式：改了它們，同一個引擎也會得到不同的分數（考卷變了，不是引擎變了）
EXAM_CODE = ("synth.py", "runner.py", "metrics.py", "scorecard.py")


def exam_fingerprint(items: list[Item], fixtures: Optional[Path] = None) -> dict[str, Any]:
    """考卷本身的指紋：每份考卷檔的 SHA-256（程式產生的頁面記來源，內容由 synth.py 決定）與出題、評分程式的 SHA-256。"""
    from ..engine import _digest, _sha_file

    fx = Path(fixtures) if fixtures else FIXTURES
    files: dict[str, Optional[str]] = {}
    for it in items:
        kind, _, name = it.source.partition(":")
        files[it.name] = _sha_file(fx / name) if kind == "fixture" else f"generated:{it.source}"
    here = Path(__file__).resolve().parent
    code = {n: _sha_file(here / n) for n in EXAM_CODE}
    return {"files": files, "code": code, "hash": _digest({"files": files, "code": code}, 8)}


def aggregate(evaluations: list[dict]) -> dict[str, Any]:
    """依總體與輸入類型平均每個指標（每一題算一筆）。"""
    def table(rows: list[dict]) -> dict[str, dict[str, float]]:
        vals: dict[str, list[float]] = {}
        for r in rows:
            for k, v in r["metrics"].items():
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    vals.setdefault(k, []).append(float(v))
        return {k: {"mean": float(np.mean(v)), "n": len(v)} for k, v in sorted(vals.items())}

    by_cat: dict[str, list] = {}
    for e in evaluations:
        by_cat.setdefault(e["category"], []).append(e)
    images = [e for e in evaluations if e["category"] != "native"]
    return {"overall": table(evaluations), "images": table(images),
            "by_type": {c: table(by_cat[c]) for c in CATEGORY_LABELS if c in by_cat}}


def run_scorecard(out_dir: Path, suite: str = "full", seed: int = 0, jobs: int = 2, cfg_path: Optional[Path] = None,
                  work: Optional[Path] = None, fixtures: Optional[Path] = None, items: Optional[list[Item]] = None,
                  outputs: Optional[list[str]] = None, say: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """跑一份考卷，寫出 <引擎版本>.json 與 .md。回傳成績單 dict（另含 "_paths"）。"""
    from .. import engine

    say = say or (lambda s: None)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config.load(cfg_path)
    eng = engine.describe(cfg)
    items = items if items is not None else SUITES[suite]
    outputs = outputs or SUITE_OUTPUTS.get(suite, SUITE_OUTPUTS["quick"])
    tmp = None
    if work is None:
        tmp = Path(tempfile.mkdtemp(prefix="mortise-scorecard-"))
        work_dir = tmp
    else:
        work_dir = Path(work) / eng["version"]
    work_dir.mkdir(parents=True, exist_ok=True)
    old_env = os.environ.get("MORTISE_OFFLINE")
    os.environ["MORTISE_OFFLINE"] = "1"  # 考卷不連網：UVDoc 有快取就用、沒有就不用（引擎版本會記下是哪一種）
    t0 = time.time()
    load0 = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    results: list[dict] = []
    errors: list[dict] = []
    try:
        tasks = []
        for it in items:
            try:
                tasks.append((it, _sample_path(it, work_dir / "_inputs", Path(fixtures) if fixtures else FIXTURES)))
            except Exception as e:  # noqa: BLE001
                errors.append({"sample": it.name, "where": "input", "error": f"{type(e).__name__}: {e}"})
        tasks.sort(key=lambda t: -(len(t[0].variants) * t[0].pages))  # 題目多的先跑
        args = [(asdict(it), str(p), str(work_dir), str(cfg_path) if cfg_path else None, list(outputs), int(seed)) for it, p in tasks]
        # 一律在新的子行程裡評（--jobs 1 也是）：③的文字辨識模型在一個行程裡只載入一次，在這個行程裡評的話，
        # 換了 OCR 模型的設定也會沿用先前載入的模型，成績單卻記著新模型的版本
        import multiprocessing as mp

        if args:
            with ProcessPoolExecutor(max_workers=max(1, min(jobs, len(args))), mp_context=mp.get_context("spawn")) as ex:
                futs = {ex.submit(_evaluate_item, *a): a[0]["name"] for a in args}
                for a in args:
                    say(f"評 {a[0]['name']}（{', '.join(a[0]['variants'])}）")
                for f in as_completed(futs):
                    try:
                        results.append(f.result())
                        say(f"完成 {futs[f]}（{results[-1]['seconds']:.0f} 秒）")
                    except Exception as e:  # noqa: BLE001
                        errors.append({"sample": futs[f], "where": "worker", "error": f"{type(e).__name__}: {e}"})
    finally:
        if old_env is None:
            os.environ.pop("MORTISE_OFFLINE", None)
        else:
            os.environ["MORTISE_OFFLINE"] = old_env
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    wall = time.time() - t0
    order = {it.name: n for n, it in enumerate(items)}
    results.sort(key=lambda r: order.get(r["sample"], 99))
    evaluations = [e for r in results for e in r["evaluations"]]
    errors += [{"sample": r["sample"], **e} for r in results for e in r["errors"]]
    agg = aggregate(evaluations)
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "engine": eng,
        "suite": suite if items is SUITES.get(suite) else f"{suite}（自訂）",
        "seed": int(seed),
        "outputs": list(outputs),
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "machine": {"cpus": os.cpu_count(), "python": platform.python_version(), "system": platform.system(), "load_at_start": load0, "jobs": jobs},
        "wall_seconds": round(wall, 1),
        "items": [asdict(it) for it in items],
        "exam": exam_fingerprint(items, fixtures),
        "evaluations": evaluations,
        "summary": agg,
        "headline": headline(agg),
        "metric_meta": {k: asdict(i) for k, i in ((k, metric_info(k)) for k in sorted(agg["overall"])) if i is not None},
        "errors": errors,
    }
    js = out_dir / f"{eng['version']}.json"
    md = out_dir / f"{eng['version']}.md"
    js.write_text(json.dumps(card, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md.write_text(render_markdown(card), encoding="utf-8")
    card["_paths"] = {"json": str(js), "md": str(md)}
    say(f"成績單：{md}（{wall:.0f} 秒）")
    return card


def headline(agg: dict) -> dict[str, Any]:
    """重點：文字與四角看影像題（原生 PDF 不經過辨識）；⑦與時間看全部。"""
    out = {}
    for k in HEADLINE:
        scope = "images" if k in _IMAGE_SCOPE else "overall"
        v = agg[scope].get(k)
        out[k] = {"mean": v["mean"] if v else None, "n": v["n"] if v else 0, "scope": scope}
    return out


# ====================================================================== 給人看的成績單


def _fmt(v: Optional[float], unit: str) -> str:
    if v is None:
        return "—"
    if unit == "比例":
        return f"{v:.3f}"
    if unit == "秒":
        return f"{v:.1f}"
    return f"{v:.2f}"


def render_markdown(card: dict) -> str:
    eng = card["engine"]
    L = [f"# Mortise 成績單：{eng['name']}", "", f"- 引擎版本：`{eng['version']}`",
         f"- 考卷：{card['suite']}（{len(card['evaluations'])} 題；種子 {card['seed']}；輸出格式 {', '.join(card['outputs'])}）",
         f"- 產生時間：{card['created']}；總時間 {card['wall_seconds']:.0f} 秒（{card['machine']['cpus']} 核，同時跑 {card['machine']['jobs']} 份；"
         f"開始時的負載 {format(card['machine']['load_at_start'], '.2f') if card['machine']['load_at_start'] is not None else '—'}）",
         f"- 考卷指紋：`{(card.get('exam') or {}).get('hash', '—')}`（考卷檔與出題、評分程式的雜湊；不同時兩份成績單不能直接比）",
         "- 方向：↑ 越高越好，↓ 越低越好。「影像題」是截圖、掃描、照片（原生 PDF 不經過文字辨識，不算在文字與四角裡）。", "",
         "## 重點", "", "| 項目 | 方向 | 單位 | 分數 | 題數 | 範圍 |", "|---|---|---|---|---|---|"]
    for k, h in card["headline"].items():
        i = metric_info(k)
        L.append(f"| {i.label} | {'↑' if i.higher_is_better else '↓'} | {i.unit} | {_fmt(h['mean'], i.unit)} | {h['n']} | {'影像題' if h['scope'] == 'images' else '全部'} |")
    by = card["summary"]["by_type"]
    cats = [c for c in CATEGORY_LABELS if c in by]
    L += ["", "## 依輸入類型", "", "| 指標 | " + " | ".join(CATEGORY_LABELS[c] for c in cats) + " |", "|---|" + "---|" * len(cats)]
    keys = [k for k in SC_METRICS if any(k in by[c] for c in cats)]
    for k in keys:
        i = SC_METRICS[k]
        L.append(f"| {i.label}{'↑' if i.higher_is_better else '↓'} | " + " | ".join(_fmt(by[c][k]["mean"], i.unit) if k in by[c] else "—" for c in cats) + " |")
    L += ["", "## 每一題", "", "四角：「偵測器」是 find_page_quad 本身；「流程」是②最後用的四角與②自己決定要不要請使用者確認。", "",
          "| 樣本 | 分組 | 輸入 | 頁數 | 錯字率 | 字級誤差 pt | 還原度 | 四角（偵測器） | 四角（流程） | 表格 | 每頁秒數 |", "|---|---|---|---|---|---|---|---|---|---|---|"]

    def quad(m: dict, ok: str, wrong: str) -> str:
        if ok not in m:
            return "—"
        if m[ok] >= 1:
            return "對"
        return "**錯且沒有提醒**" if m.get(wrong) else "錯（有請使用者確認）"

    for e in card["evaluations"]:
        m = e["metrics"]
        L.append(f"| {e['sample']} | {e['group']} | {VARIANT_LABELS.get(e['variant'], e['variant'])} | {e['pages']} | {_fmt(m.get('page_cer'), '比例')} | "
                 f"{_fmt(m.get('size_error_pt'), 'pt')} | {_fmt(m.get('e2e_score'), '比例')} | {quad(m, 'quad_success', 'quad_confident_wrong')} | "
                 f"{quad(m, 'page_quad_success', 'page_quad_silent_wrong')} | {_fmt(m.get('table_shape_exact'), '比例')} | {_fmt(m.get('seconds_per_page'), '秒')} |")
    if card["errors"]:
        L += ["", "## 錯誤", ""]
        L += [f"- {e.get('sample', '')}（{e.get('where', '')}）：{e.get('error', '')}" for e in card["errors"]]
    L += ["", "## 怎麼用", "", "和另一份成績單比較（退步超過門檻時結束碼是 1）：", "",
          "```bash", f"python -m mortise.eval scorecard compare 舊的.json {eng['version']}.json", "```", ""]
    return "\n".join(L) + "\n"


# ====================================================================== 比較


def parse_gates(specs: Optional[list[str]] = None, path: Optional[Path] = None, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """門檻：指標 → 容許量字串（"0.01" 絕對值；"50%" 相對於舊值）；寫 "off" 拿掉一個預設門檻。"""
    gates = dict(DEFAULT_GATES if base is None else base)
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        gates.update({str(k): str(v) for k, v in (data.get("gates", data)).items()})
    for s in specs or []:
        k, _, v = s.partition("=")
        if not k or not v:
            raise ValueError(f"門檻要寫成 指標=容許量（例如 page_cer=0.01 或 seconds_per_page=50%）：{s}")
        gates[k.strip()] = v.strip()
    return {k: v for k, v in gates.items() if v.lower() not in ("off", "none", "")}


def _allowed(gate: str, old: float) -> float:
    if gate.endswith("%"):
        return abs(old) * float(gate[:-1]) / 100.0
    return float(gate)


def _valid(v: Any) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _where_scope(where: str) -> str:
    """錯誤發生的地方 → 輸入類型（synth_photo、scorecard_curved_bend → photo、curved；其他是整份文件）。"""
    for head in ("synth_", "scorecard_", ""):
        v = where[len(head):] if where.startswith(head) else None
        if v and (v in VARIANT_LABELS or v.startswith("curved_")):
            return category(v)
    return "overall"


def exam_diff(old: dict, new: dict) -> dict[str, Any]:
    """兩份成績單的考卷是不是同一份：考卷檔（兩邊都有的樣本）與出題、評分程式的雜湊。"""
    eo, en = old.get("exam"), new.get("exam")
    if not eo or not en:
        which = "舊" if not eo else "新"
        return {"same": None, "notes": [f"{which}成績單沒有記考卷指紋（較早的成績單），無法確認兩份的考卷相同。"]}
    notes = []
    for name in sorted(set(eo.get("code", {})) | set(en.get("code", {}))):
        if eo.get("code", {}).get(name) != en.get("code", {}).get(name):
            notes.append(f"評分或出題的程式 {name} 不同")
    fo, fn = eo.get("files", {}), en.get("files", {})
    for name in sorted(set(fo) & set(fn)):
        if fo[name] != fn[name]:
            notes.append(f"考卷檔 {name} 的內容不同")
    return {"same": not notes, "notes": notes}


def compare(old: dict, new: dict, gates: Optional[dict[str, str]] = None, allow_missing: bool = False) -> dict[str, Any]:
    """比較兩份成績單。回傳每個範圍每個指標的變化（配對平均），與超過門檻的退步。
    缺題、缺指標（設了門檻的）、新的錯誤也算超過門檻的退步（allow_missing=True 時只列出，見開頭「四、比較」）。"""
    gates = DEFAULT_GATES if gates is None else gates
    for c in (old, new):
        if c.get("schema") != SCHEMA:
            raise ValueError(f"不是成績單（schema 應為 {SCHEMA}）")
    ko = {e["key"]: e for e in old["evaluations"]}
    kn = {e["key"]: e for e in new["evaluations"]}
    common = [k for k in ko if k in kn]
    only_old, only_new = sorted(set(ko) - set(kn)), sorted(set(kn) - set(ko))
    problems: list[dict[str, Any]] = []
    for k in only_old:
        problems.append({"kind": "missing_item", "scope": ko[k]["category"], "metric": None, "key": k,
                         "detail": "舊成績單有這一題、新成績單沒有（新版本可能在這一題當掉了）"})
    old_err = {(str(e.get("sample", "")), str(e.get("where", ""))) for e in old.get("errors", [])}
    for e in new.get("errors", []):
        sk = (str(e.get("sample", "")), str(e.get("where", "")))
        if sk not in old_err:
            problems.append({"kind": "new_error", "scope": _where_scope(sk[1]), "metric": None, "key": f"{sk[0]}|{sk[1]}",
                             "detail": str(e.get("error", ""))[:300]})
            old_err.add(sk)
    ungated_missing: list[dict[str, Any]] = []
    order = {k: i for i, k in enumerate(SC_METRICS)}
    names = sorted({m for k in common for m in ko[k]["metrics"]} | {m for k in common for m in kn[k]["metrics"]},
                   key=lambda m: (order.get(m, len(order)), m))  # 成績單的主要指標在前
    for m in names:
        for k in common:
            if _valid(ko[k]["metrics"].get(m)) and not _valid(kn[k]["metrics"].get(m)):
                row = {"kind": "missing_metric", "scope": ko[k]["category"], "metric": m, "key": k,
                       "detail": "舊成績單這一題有這個指標、新成績單沒有"}
                (problems if m in gates else ungated_missing).append(row)
    scopes = {"overall": common, **{c: [k for k in common if ko[k]["category"] == c] for c in CATEGORY_LABELS}}
    rows, regressions = [], []
    eps = 1e-9
    for scope, keys in scopes.items():
        for m in names:
            info = metric_info(m)
            if info is None:
                continue
            pairs = [(float(ko[k]["metrics"][m]), float(kn[k]["metrics"][m])) for k in keys
                     if _valid(ko[k]["metrics"].get(m)) and _valid(kn[k]["metrics"].get(m))]
            if not pairs:
                continue
            ov, nv = float(np.mean([a for a, _ in pairs])), float(np.mean([b for _, b in pairs]))
            d = nv - ov
            worse = -d if info.higher_is_better else d
            gate = gates.get(m)
            allowed = _allowed(gate, ov) if gate is not None else None
            status = "same"
            if worse > eps:
                status = "regression" if (allowed is not None and worse > allowed + eps) else "worse"
            elif worse < -eps:
                status = "improved"
            row = {"kind": "metric", "scope": scope, "metric": m, "old": ov, "new": nv, "delta": d, "status": status, "gate": gate, "n": len(pairs)}
            rows.append(row)
            if status == "regression":
                regressions.append(row)
    for pr in problems:
        pr["status"] = "warning" if allow_missing else "regression"
        if not allow_missing:
            regressions.append(pr)
    return {"old": {"version": old["engine"]["version"], "name": old["engine"]["name"], "suite": old.get("suite")},
            "new": {"version": new["engine"]["version"], "name": new["engine"]["name"], "suite": new.get("suite")},
            "common": len(common), "only_old": only_old, "only_new": only_new,
            "same_engine": old["engine"]["version"] == new["engine"]["version"], "same_seed": old.get("seed") == new.get("seed"),
            "same_outputs": sorted(old.get("outputs") or []) == sorted(new.get("outputs") or []),
            "exam": exam_diff(old, new), "allow_missing": allow_missing,
            "rows": rows, "problems": problems, "ungated_missing": ungated_missing, "regressions": regressions, "gates": dict(gates)}


_PROBLEM_LABELS = {"missing_item": "缺題", "missing_metric": "缺指標", "new_error": "新的錯誤"}


def render_compare(res: dict) -> str:
    L = [f"# 成績單比較：{res['old']['name']} → {res['new']['name']}", "", f"- 舊：`{res['old']['version']}`", f"- 新：`{res['new']['version']}`",
         f"- 比較的題目：{res['common']} 題（兩邊都有做的；每個指標只用兩邊都有這個指標的題目平均）"]
    if res["only_old"]:
        how = "只列出（--allow-missing）" if res.get("allow_missing") else "**算超過門檻的退步**"
        L.append(f"- **舊成績單有、新成績單沒有的題目：{len(res['only_old'])} 題**（新版本可能當掉了；{how}）")
    if res["only_new"]:
        L.append(f"- 新成績單多做的題目：{len(res['only_new'])} 題（舊的沒有，不比）")
    ex = res.get("exam") or {}
    if ex.get("same") is not True:
        L.append("- **注意：兩份成績單的考卷不一定相同，分數的差異可能來自考卷而不是引擎：" + "；".join(n.rstrip("。") for n in (ex.get("notes") or ["不明"])) + "。**")
    if not res["same_seed"]:
        L.append("- **注意：兩份的種子不同，題目的影像不一樣，比較只供參考。**")
    if not res.get("same_outputs", True):
        L.append("- **注意：兩份要求的輸出格式不同（例如快速版與完整版），⑦還原度總分取最差的格式，不能直接比；請比各格式自己的分數。**")
    if res["same_engine"]:
        L.append("- 兩份是同一個引擎版本（差異只可能來自處理時間）。")
    if res.get("problems"):
        L += ["", "## 缺題、缺指標、新的錯誤", ""]
        tail = "（--allow-missing：只列出，不算退步）" if res.get("allow_missing") else "（都算超過門檻的退步）"
        L.append(f"新版本少做的、少了的、新出現的錯誤{tail}：")
        L.append("")
        for pr in res["problems"]:
            scope = "總體" if pr["scope"] == "overall" else CATEGORY_LABELS.get(pr["scope"], pr["scope"])
            what = f"{_PROBLEM_LABELS[pr['kind']]}：{pr['key']}" + (f" 的 {metric_info(pr['metric']).label if metric_info(pr['metric']) else pr['metric']}" if pr.get("metric") else "")
            L.append(f"- **{what}**（{scope}）：{pr['detail']}")
    if res.get("ungated_missing"):
        L += ["", f"另有 {len(res['ungated_missing'])} 個沒設門檻的指標在新成績單裡缺了（只列出）："
              + "、".join(sorted({f"{r['key']} 的 {r['metric']}" for r in res['ungated_missing']})[:20])]
    L += ["", "## 各項指標", "", "| 範圍 | 指標 | 方向 | 舊 | 新 | 變化 | 門檻 | 題數 | 結果 |", "|---|---|---|---|---|---|---|---|---|"]
    txt = {"regression": "**退步（超過門檻）**", "worse": "**退步**（門檻內）", "improved": "進步", "same": "持平"}
    shown = [r for r in res["rows"] if r["scope"] == "overall" or r["status"] != "same"]
    for r in shown:
        info = metric_info(r["metric"])
        scope = "總體" if r["scope"] == "overall" else CATEGORY_LABELS.get(r["scope"], r["scope"])
        delta = f"{r['delta']:+.3f}" if info.unit == "比例" else f"{r['delta']:+.2f}"
        if r["status"] in ("regression", "worse"):
            delta = f"**{delta}**"
        L.append(f"| {scope} | {info.label} | {'↑' if info.higher_is_better else '↓'} | {_fmt(r['old'], info.unit)} | {_fmt(r['new'], info.unit)} | {delta} | "
                 f"{r['gate'] or '—'} | {r['n']} | {txt[r['status']]} |")
    L.append("")
    if res["regressions"]:
        L.append(f"**結論：有 {len(res['regressions'])} 項退步超過門檻（含缺題、缺指標、新的錯誤），不建議採用新版本。**")
    else:
        L.append("結論：沒有超過門檻的退步，可以採用新版本（仍請看一下上面標粗體的小退步與注意事項）。")
    return "\n".join(L) + "\n"


# ====================================================================== 最新成績單（給 GET /api/engine）


def default_dirs() -> list[Path]:
    env = os.environ.get("MORTISE_SCORECARDS")
    dirs = [Path(p) for p in env.split(os.pathsep) if p] if env else []
    dirs.append(Path(__file__).resolve().parents[2] / "docs" / "scorecards")
    return dirs


def latest_summary(current_version: Optional[str] = None, dirs: Optional[list[Path]] = None) -> Optional[dict[str, Any]]:
    """找最新的成績單（和目前引擎版本相同的優先），回傳摘要；一份都沒有回 None。"""
    cards = []
    for d in dirs or default_dirs():
        for p in sorted(Path(d).glob("*.json")) if Path(d).is_dir() else []:
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if c.get("schema") == SCHEMA:
                cards.append((p, c))
    if not cards:
        return None
    same = [pc for pc in cards if pc[1]["engine"]["version"] == current_version]
    p, c = max(same or cards, key=lambda pc: pc[1].get("created", ""))
    return {"version": c["engine"]["version"], "name": c["engine"]["name"], "created": c.get("created"), "suite": c.get("suite"),
            "matches_current": c["engine"]["version"] == current_version, "file": p.name, "wall_seconds": c.get("wall_seconds"),
            "headline": {k: {"label": metric_info(k).label if metric_info(k) else k, **v} for k, v in c.get("headline", {}).items()},
            "errors": len(c.get("errors", []))}


# ====================================================================== 命令列


def main_cli(argv: list[str], say: Callable[[str], None]) -> int:
    import argparse

    if argv and argv[0] == "compare":
        ap = argparse.ArgumentParser(prog="python -m mortise.eval scorecard compare", description="比較兩份成績單；退步超過門檻時結束碼是 1")
        ap.add_argument("old", type=Path)
        ap.add_argument("new", type=Path)
        ap.add_argument("--gate", action="append", default=[], help="指標=容許量（例如 page_cer=0.01、seconds_per_page=50%%；寫 off 拿掉預設門檻）")
        ap.add_argument("--gates", type=Path, help="門檻檔（JSON：{\"page_cer\": \"0.01\", ...}）")
        ap.add_argument("--no-default-gates", action="store_true", help="不用預設門檻，只用 --gate／--gates 指定的")
        ap.add_argument("--md", type=Path, help="把比較結果另存成 Markdown")
        ap.add_argument("--allow-missing", action="store_true", help="缺題、缺指標、新的錯誤只列出、不算退步（確定是故意的時候才用）")
        ap.add_argument("--strict-exam", action="store_true", help="兩份成績單的考卷（考卷檔、出題與評分程式）不同時，結束碼是 3")
        a = ap.parse_args(argv[1:])
        try:
            old = json.loads(a.old.read_text(encoding="utf-8"))
            new = json.loads(a.new.read_text(encoding="utf-8"))
            gates = parse_gates(a.gate, a.gates, {} if a.no_default_gates else None)
            res = compare(old, new, gates, allow_missing=a.allow_missing)
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"無法比較：{e}", file=sys.stderr)
            return 2
        text = render_compare(res)
        print(text)
        if a.md:
            a.md.write_text(text, encoding="utf-8")
        if a.strict_exam and res["exam"]["same"] is not True:
            print("考卷不同（--strict-exam）：這兩份成績單不能拿來決定要不要採用。", file=sys.stderr)
            return 3
        return 1 if res["regressions"] else 0
    ap = argparse.ArgumentParser(prog="python -m mortise.eval scorecard", description="用固定考卷給目前的引擎打成績")
    ap.add_argument("--quick", action="store_true", help="快速考卷（4 核機器約 3 分鐘內）")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=2, help="同時評幾份文件（行程數）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config", type=Path, help="設定覆寫檔（例如自動調參的 override.toml，或換 OCR 模型的設定）")
    ap.add_argument("--work", type=Path, help="保留中間產物的目錄（預設用暫存目錄、跑完刪掉）")
    ap.add_argument("--fixtures", type=Path, help="tests/fixtures 的位置（預設是原始碼目錄裡的）")
    a = ap.parse_args(argv)
    card = run_scorecard(a.out, "quick" if a.quick else "full", a.seed, a.jobs, a.config, a.work, a.fixtures, say=say)
    h = card["headline"]
    print(f"引擎 {card['engine']['name']}（{card['engine']['version']}）：{len(card['evaluations'])} 題、{card['wall_seconds']:.0f} 秒、錯誤 {len(card['errors'])} 筆")
    for k, v in h.items():
        i = metric_info(k)
        print(f"  {i.label}：{_fmt(v['mean'], i.unit)}{'' if i.unit == '比例' else ' ' + i.unit}（{v['n']} 題）")
    print(f"成績單：{card['_paths']['md']}")
    return 0 if not card["errors"] else 4
