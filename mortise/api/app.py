"""對外 API（FastAPI）。前端頁面只透過這裡和流水線溝通。

啟動：
    uvicorn mortise.api.app:app --host 0.0.0.0 --port 8000
環境變數：
    MORTISE_JOBS      工作目錄的根目錄（預設 ./jobs）
    MORTISE_WORKERS   同時處理幾份檔案（預設 2）
    MORTISE_CORS      允許的前端來源，逗號分隔（預設不開放跨來源）
    MORTISE_RETENTION_DAYS  檔案保留天數（預設 30）
    MORTISE_FEEDBACK  回饋資料庫的目錄（預設 工作目錄/_feedback）：只收上傳時同意用於改善辨識的檔案
    MORTISE_WEB_DIST  前端建置結果的目錄（預設 專案根目錄/web/dist）

端點一覽（詳細說明見各函數）：
    GET  /api/health                         服務狀態（含檔案大小上限與接受的檔案類型）
    GET  /api/formats                        可選的輸出格式（label 為中文、label_en 為英文）
    GET  /api/page-sizes                     輸出尺寸的預設清單（A0…Tabloid 一律直式，另有簡報尺寸）
    POST /api/jobs                           上傳檔案，建立工作（可指定原始尺寸與輸出尺寸）
    GET  /api/jobs/{id}                      工作狀態與進度
    GET  /api/jobs/{id}/events               進度串流（Server-Sent Events）
    GET  /api/jobs/{id}/report               還原度報告
    GET  /api/jobs/{id}/pages                各頁尺寸與預覽圖網址
    GET  /api/jobs/{id}/pages/{n}/image      預覽圖：original（原圖）、result（某個輸出格式的成品渲染）、overlay（疊圖）
    GET  /api/jobs/{id}/pages/{n}/elements   可修正的元素清單（文字框、表格、圖片、形狀，含信心值與問題）
    POST /api/jobs/{id}/corrections          送出修正，只從受影響的階段重跑
    GET  /api/jobs/{id}/download/{format}    下載輸出檔（format 為輸出格式或 report）
    POST /api/jobs/{id}/feedback             回饋（評分、意見）
    DELETE /api/jobs/{id}                    立即刪除這份工作的所有檔案

網頁：
    /                                        前端（web/ 建置後的 web/dist；還沒建置時顯示建置說明）
    /dev                                     開發用的簡易測試頁
    /api 開頭的路徑永遠由 API 處理，不會被前端攔走；其他不是實體檔案的路徑一律回前端的 index.html（單頁應用的路由）。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from ..artifacts import BlobRef, FidelityReport, Intent, Layout, OutputFile, OutputFormat, PageSpec, PhysicalSize, SourceDoc, StyledDoc, UsageMode
from ..config import Config
from ..pipeline.runner import user_correction
from ..pipeline.store import atomic_write_text
from ..stages import s6_export
from ..tools import pdfium as pdf
from ..tools.units import to_pt
from ..verify.lines import CODES as LINE_CODES
from .jobs import JobManager

CFG = Config.load()
JOBS = JobManager(
    Path(os.environ.get("MORTISE_JOBS", "jobs")),
    workers=int(os.environ.get("MORTISE_WORKERS", "2")),
    retention_days=float(os.environ.get("MORTISE_RETENTION_DAYS", "30")),
    cfg=CFG,
    feedback_root=Path(os.environ.get("MORTISE_FEEDBACK", str(Path(os.environ.get("MORTISE_JOBS", "jobs")) / "_feedback"))),
)

@asynccontextmanager
async def _lifespan(_app):
    JOBS.purge_expired()  # 啟動時清掉超過保留期限的工作
    yield


app = FastAPI(title="Mortise API", version="0.1.0", description="把不可編輯的文件還原成可編輯、排版一致的檔案", lifespan=_lifespan)
_origins = [o.strip() for o in os.environ.get("MORTISE_CORS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])


class _PrivacyHeaders:
    """每個回應加上 X-Content-Type-Options: nosniff 與 Referrer-Policy: no-referrer。
    /api/jobs/ 底下的回應都是使用者的文件或由它衍生的資料：一律 Cache-Control: private（共用快取、CDN 不可存）；
    沒有另外指定時用 no-store。預覽圖自己帶 private, no-cache 加 ETag（瀏覽器可以重新驗證）。
    用純 ASGI 寫，不包住回應本體，進度串流（SSE）照常逐則送出。"""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        private = scope.get("path", "").startswith("/api/jobs")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if not (private and k.lower() == b"cache-control" and b"private" not in v.lower())]
                names = {k.lower() for k, _ in headers}
                if private and b"cache-control" not in names:
                    headers.append((b"cache-control", b"private, no-store"))
                if b"x-content-type-options" not in names:
                    headers.append((b"x-content-type-options", b"nosniff"))
                if b"referrer-policy" not in names:
                    headers.append((b"referrer-policy", b"no-referrer"))
                message = {**message, "headers": headers}
            await send(message)

        await self.inner(scope, receive, send_with_headers)


app.add_middleware(_PrivacyHeaders)

FORMAT_LABELS = {
    OutputFormat.PPTX: "PowerPoint（.pptx）",
    OutputFormat.GOOGLE_SLIDES: "Google 簡報（匯入用的 .pptx）",
    OutputFormat.DOCX_FIXED: "Word 版面固定（.docx）",
    OutputFormat.DOCX_FLOW: "Word 可重排（.docx）",
    OutputFormat.XLSX: "Excel 表格（.xlsx）",
    OutputFormat.AI_PDF: "Illustrator 可編輯 PDF（.pdf）",
    OutputFormat.IDML: "InDesign（.idml）",
    OutputFormat.TEMPLATE: "版面模板（.pptx）",
    OutputFormat.PDF: "可編輯 PDF（.pdf）",
    OutputFormat.SVG: "SVG 整頁（.svg；多頁為 .zip）",
    OutputFormat.SVG_ASSETS: "SVG 向量圖（每個圖一個 .svg，打包成 .zip）",
}
FORMAT_LABELS_EN = {
    OutputFormat.PDF: "Editable PDF (.pdf)",
    OutputFormat.SVG: "SVG (.svg)",
    OutputFormat.SVG_ASSETS: "SVG graphics only (.zip)",
    OutputFormat.PPTX: "PowerPoint (.pptx)",
    OutputFormat.DOCX_FIXED: "Word, fixed layout (.docx)",
    OutputFormat.DOCX_FLOW: "Word, reflowable (.docx)",
    OutputFormat.XLSX: "Excel (.xlsx)",
    OutputFormat.AI_PDF: "PDF for Illustrator (.pdf)",
    OutputFormat.IDML: "InDesign (.idml)",
    OutputFormat.GOOGLE_SLIDES: "Google Slides (.pptx)",
    OutputFormat.TEMPLATE: "Layout template (.pptx)",
}
ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"]  # 與 stages/s1_input.py 的檔案類型判斷一致
_TARGET_MIN_PT, _TARGET_MAX_PT = 36.0, 14400.0  # 原始尺寸與輸出尺寸的範圍：0.5 吋到 200 吋（PDF 規格建議的頁面上限）

# 修正欄位 → 送給哪個階段
FIELD_STAGE = {"size": 2, "corners": 2, "role": 4, "formula": 5, "align": 5, "font_family": 5, "font_size_pt": 5, "color": 5, "text": 5}


def _job(job_id: str):
    try:
        return JOBS.pipeline(job_id)
    except KeyError:
        raise HTTPException(404, "找不到這份工作（可能已過保留期限而刪除）。")


def _load(pipe, name: str, cls):
    if not pipe.store.has(name):
        raise HTTPException(409, "這份工作還沒處理到這一步。")
    return pipe.store.load(name, cls)


_PARSED: "dict[tuple[str, str, str], Any]" = {}
_PARSED_LOCK = threading.Lock()
_PARSED_MAX = 8


def _load_parsed(pipe, name: str, cls):
    """解析過的大型產物（成品文件、還原度報告）留在記憶體裡重用，以內容雜湊為鍵（重跑後雜湊改變，自然不會拿到舊的）。
    只給讀取用：拿到的物件不可修改。最多留 _PARSED_MAX 份，先放進來的先丟。"""
    key = (str(pipe.dir), name, pipe.store.hash_of(name))
    with _PARSED_LOCK:
        hit = _PARSED.get(key)
    if hit is not None:
        return hit
    obj = pipe.store.load(name, cls)
    with _PARSED_LOCK:
        _PARSED[key] = obj
        while len(_PARSED) > _PARSED_MAX:
            _PARSED.pop(next(iter(_PARSED)))
    return obj


# ====================================================================== 基本資訊


@app.get("/api/health")
def health() -> dict[str, Any]:
    from ..tools import libreoffice

    return {
        "ok": True, "verify_engine": libreoffice.version() if libreoffice.available() else None, "formats": [f.value for f in s6_export.available()],
        "max_file_mb": CFG.section("input")["max_file_mb"], "accepted": list(ACCEPTED_TYPES),
    }


@app.get("/api/formats")
def formats() -> list[dict[str, Any]]:
    ready = set(s6_export.available())
    return [{"format": f.value, "label": FORMAT_LABELS.get(f, f.value), "label_en": FORMAT_LABELS_EN.get(f, f.value), "available": f in ready} for f in OutputFormat]


@app.get("/api/page-sizes")
def page_sizes() -> list[dict[str, Any]]:
    """輸出尺寸的預設清單（default.toml 的 [output_sizes]）。A、B 系列與 Letter、Legal、Tabloid 一律以直式（寬 ≤ 高）列出，
    要橫式由前端對調寬高後送到 POST /api/jobs 的 target_width、target_height；簡報尺寸（Slide 16:9、Slide 4:3）照原樣列出（橫式）。"""
    return [{"id": str(s["id"]), "label": str(s.get("label", s["id"])), "width_mm": float(s["width_mm"]), "height_mm": float(s["height_mm"])}
            for s in CFG.section("output_sizes").get("sizes", [])]


# ====================================================================== 建立工作


@app.post("/api/jobs", status_code=201)
async def create_job(
    file: UploadFile = File(..., description="PDF、PNG 或 JPEG"),
    outputs: str = Form("pptx", description="輸出格式，逗號分隔，例如 pptx,docx_flow"),
    mode: Literal["rescue", "template"] = Form("rescue", description="rescue：檔案急救；template：靈感模板"),
    width: Optional[float] = Form(None, description="已知的實際寬度（可省略）"),
    height: Optional[float] = Form(None, description="已知的實際高度"),
    unit: Literal["mm", "cm", "in", "pt", "px"] = Form("mm"),
    allow_training: bool = Form(False, description="是否同意用這份檔案改善辨識（預設不同意）"),
    target_width: Optional[float] = Form(None, description="輸出尺寸的寬（可省略；與 target_height 一起填才算指定）"),
    target_height: Optional[float] = Form(None, description="輸出尺寸的高"),
    target_unit: Literal["mm", "cm", "in", "pt"] = Form("mm", description="輸出尺寸的單位"),
) -> dict[str, Any]:
    """建立工作。輸出尺寸（target_width、target_height）兩個都有值才算指定：⑤把每一頁等比例縮放到放得進這個尺寸的最大比例並置中；
    只填一個回 422（不默默忽略，免得使用者以為指定了尺寸）。直式或橫式由前端決定好寬高再送。"""
    target = _checked_size(target_width, target_height, target_unit, "輸出尺寸")
    size = _checked_size(width, height, unit, "原始尺寸")
    try:
        wanted = [OutputFormat(x.strip()) for x in outputs.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "不認得的輸出格式。可用的格式見 /api/formats。")
    ready = set(s6_export.available())
    missing = [f.value for f in wanted if f not in ready]
    if missing:
        raise HTTPException(422, f"這些輸出格式還沒有開放：{', '.join(missing)}")
    limit = CFG.section("input")["max_file_mb"] * 1024 * 1024
    suffix = Path(file.filename or "upload").suffix[:10]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(413, f"檔案超過 {CFG.section('input')['max_file_mb']} MB 上限。")
            tmp.write(chunk)
    try:
        intent = Intent(outputs=wanted or [OutputFormat.PPTX], mode=UsageMode(mode), known_size=size, target_size=target)
        job_id = JOBS.create(Path(tmp.name), Path(file.filename or "upload").name, intent, allow_training)
    finally:
        os.unlink(tmp.name)
    return JOBS.status(job_id)



def _checked_size(w: Optional[float], h: Optional[float], unit: str, what: str) -> Optional[PhysicalSize]:
    """原始尺寸與輸出尺寸共用的檢查：寬與高要一起填（或都不填）、必須是有限的正數，換算後要在 0.5 到 200 吋之間。
    超出範圍回 422：太小的原始尺寸會讓流水線除以零，太大的會讓驗證時的渲染要求巨大的記憶體。"""
    if w is None and h is None:
        return None
    if w is None or h is None:
        raise HTTPException(422, f"{what}的寬與高要一起填（或都不填）。")
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
        raise HTTPException(422, f"{what}的寬與高要是大於 0 的數字。")
    wp, hp = to_pt(w, unit), to_pt(h, unit)
    if not (_TARGET_MIN_PT <= wp <= _TARGET_MAX_PT and _TARGET_MIN_PT <= hp <= _TARGET_MAX_PT):
        raise HTTPException(422, f"{what}要在 {_TARGET_MIN_PT / 72 * 25.4:.1f} 到 {_TARGET_MAX_PT / 72 * 25.4:.0f} mm 之間。")
    return PhysicalSize(width=w, height=h, unit=unit)


# ====================================================================== 狀態與進度


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    _job(job_id)
    return JOBS.status(job_id)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """進度串流：每個階段開始與完成各一則事件，最後一則的 status 為 done 或 failed。"""
    _job(job_id)

    async def stream():
        sent = JOBS.run_start(job_id)  # 只送這一次執行的事件：修正後重跑時重新連線，不會先收到上一次的完成事件
        idle = 0
        while True:
            events = JOBS.events_since(job_id, sent)
            for e in events:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            sent += len(events)
            try:
                st = JOBS.status(job_id)
            except KeyError:  # 串流途中工作被刪除
                yield f"data: {json.dumps({'status': 'failed', 'final': True, 'error': '這份工作已被刪除。'}, ensure_ascii=False)}\n\n"
                return
            if st["status"] in ("done", "failed") and not events:
                final = {"status": st["status"], "final": True, "progress": st["progress"], "error": st["error"]}
                yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
                return
            idle = 0 if events else idle + 1
            if idle and idle % 50 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ====================================================================== 結果


@app.get("/api/jobs/{job_id}/report")
def job_report(job_id: str) -> dict[str, Any]:
    pipe = _job(job_id)
    report = _load(pipe, "s7/fidelity_report", FidelityReport)
    data = report.model_dump(mode="json", exclude={"target_render"})
    data["rounds"] = pipe.state.get("rounds", [])
    data["outputs"] = _outputs_summary(pipe)
    return data


def _outputs_summary(pipe) -> list[dict[str, Any]]:
    """各輸出檔的摘要（給前端在下載前提醒）：檔名、自檢結果與說明；svg_assets 另附 zip 裡的向量圖數量
    （0 表示 zip 裡只有說明檔，前端會建議改選整頁的 SVG）。"""
    out = []
    for f in _intent(pipe).outputs:
        if not pipe.store.has(f"s6/{f.value}"):
            continue
        of = pipe.store.load(f"s6/{f.value}", OutputFile)
        item: dict[str, Any] = {"format": f.value, "filename": of.filename, "selfcheck_ok": of.selfcheck_ok, "notes": list(of.selfcheck_notes)}
        if f == OutputFormat.SVG_ASSETS:
            try:
                import zipfile

                with zipfile.ZipFile(pipe.store.blob_path(of.file)) as z:
                    item["vector_count"] = int(json.loads(z.read("manifest.json")).get("count", 0))
            except Exception:  # 讀不到 manifest：不提供數量（前端不提醒）
                item["vector_count"] = None
        out.append(item)
    return out


@app.get("/api/jobs/{job_id}/pages")
def job_pages(job_id: str) -> list[dict[str, Any]]:
    """各頁的尺寸（輸出頁面，pt）、原始內容在輸出頁面上的位置（placement；沒有指定輸出尺寸時為 null）與預覽圖網址。
    images 的 result、overlay 不帶 format，畫的是這份工作的第一個輸出格式；result_images 列出每個輸出格式各自的網址。"""
    pipe = _job(job_id)
    doc = _load(pipe, "s1/source_doc", SourceDoc)
    styled = pipe.store.load("s5/styled_doc", StyledDoc) if pipe.store.has("s5/styled_doc") else None
    outputs = [f.value for f in _intent(pipe).outputs]
    out = []
    for ref in doc.pages:
        n = ref.page_index + 1
        item: dict[str, Any] = {"page": n, "input_type": ref.input_type.value.value, "input_type_confidence": ref.input_type.confidence, "placement": None}
        if pipe.store.has(f"s2/page-{n:03d}"):
            spec = pipe.store.load(f"s2/page-{n:03d}", PageSpec)
            item.update({"width_pt": spec.page_width_pt.value, "height_pt": spec.page_height_pt, "size_source": spec.page_width_pt.source.value, "size_confidence": spec.page_width_pt.confidence, "notes": spec.notes})
            item["source_width_pt"], item["source_height_pt"] = spec.page_width_pt.value, spec.page_height_pt  # 原始內容的尺寸（不受輸出尺寸影響）
        if styled and ref.page_index < len(styled.pages):
            sp = styled.pages[ref.page_index]
            item.update({"width_pt": sp.width_pt, "height_pt": sp.height_pt, "placement": _box_json(sp.placement) if sp.placement else None})
        base = f"/api/jobs/{job_id}/pages/{n}/image"
        item["images"] = {k: f"{base}?kind={k}" for k in ("original", "result", "overlay")}
        item["result_images"] = {f: f"{base}?kind=result&format={f}" for f in outputs}
        item["elements"] = f"/api/jobs/{job_id}/pages/{n}/elements"
        out.append(item)
    return out


def _box_json(b) -> dict[str, float]:
    return {"x": b.x, "y": b.y, "w": b.w, "h": b.h, "rotation": b.rotation}


def _intent(pipe) -> Intent:
    return Intent.model_validate(pipe.state.get("intent") or {})


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _png(im: Image.Image) -> Response:
    return Response(_png_bytes(im), media_type="image/png", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------- 預覽圖的渲染


_RENDER_VERSION = "2"  # 預覽圖的畫法改了就加 1，舊的快取一起失效
_NEUTRAL_LOCK = threading.Lock()


def _pdf_page(path: Path, page_index: int, width_px: int) -> Optional[Image.Image]:
    """用 PDFium 把 PDF 的一頁畫成指定寬度（像素）的影像。沒有這一頁回傳 None。"""
    doc = pdf.open_document(str(path))
    try:
        if page_index >= len(doc):
            return None
        reader = pdf.PageReader(doc, page_index)
        try:
            bx = reader.boxes()
            l, b, r, t = bx.get("crop", bx["media"])
            return reader.render(72.0 * width_px / max(r - l, 1.0)).convert("RGB")
        finally:
            reader.close()
    finally:
        doc.close()


def _neutral_pdf(pipe, styled: StyledDoc) -> Path:
    """成品文件的中性渲染（給 Illustrator 的 PDF 輸出器畫出來的 PDF，⑦的 7A 也是用它）。依成品文件的內容快取在工作目錄。"""
    folder = pipe.dir / "previews"
    folder.mkdir(exist_ok=True)
    meta = folder / f"neutral-{pipe.store.hash_of('s5/styled_doc')}.json"
    with _NEUTRAL_LOCK:
        if meta.exists():
            ref = BlobRef.model_validate_json(meta.read_text(encoding="utf-8"))
            if pipe.store.blob_path(ref).exists():
                return pipe.store.blob_path(ref)
        out = s6_export.run(styled, OutputFormat.AI_PDF, "neutral", pipe.cfg, pipe.store)
        atomic_write_text(meta, out.file.model_dump_json())
        return pipe.store.blob_path(out.file)


def _svg_live(pipe, page_index: int, width_px: int) -> Optional[Image.Image]:
    """用 resvg 現畫 SVG 輸出檔的一頁（與⑦的 SVG 回讀驗證同一套字型清單與渲染前調整）。resvg 無法使用時回傳 None。"""
    from ..exporters import svg as svgx
    from ..verify import svg_target

    resvg_py, _ = svg_target.engine()
    if resvg_py is None or not pipe.store.has("s6/svg"):
        return None
    out = pipe.store.load("s6/svg", OutputFile)
    pages, others = svgx.unpack(out, pipe.store)
    data = pages.get(page_index)
    if data is None:
        return None
    _, root = svgx.check(data, set(others) if others else None)
    if root is None:
        return None
    p = svg_target._params(pipe.cfg)
    _, lp = svgx.params_for(pipe.cfg)
    book = svg_target.font_book(list(p["svg_font_dirs"]) or None)
    text, _ = svg_target.render_copy(root, book, others, bool(p["svg_emulate_bold"]), float(lp.get("faux_bold_stroke_ratio", 0.03)))
    return svg_target.render(resvg_py, text, width_px, book)


def _render_result(pipe, fmt: OutputFormat, page_index: int, width_px: int, styled: StyledDoc, report: Optional[FidelityReport]) -> tuple[Optional[Image.Image], str]:
    """某個輸出格式的成品畫成影像。回傳（影像, 用什麼畫的）。
    pdf、ai_pdf：PDFium 直接畫輸出檔；svg：resvg 現畫（不行就用⑦驗證時的渲染）；pptx：⑦用 LibreOffice 轉出的 PDF；
    其他格式、或那個格式沒有渲染結果時：成品文件的中性渲染。"""
    image = None
    if fmt in (OutputFormat.PDF, OutputFormat.AI_PDF) and pipe.store.has(f"s6/{fmt.value}"):
        out = pipe.store.load(f"s6/{fmt.value}", OutputFile)
        image = _pdf_page(pipe.store.blob_path(out.file), page_index, width_px)
        if image is not None:
            return image, fmt.value
    if fmt == OutputFormat.SVG:
        try:
            image = _svg_live(pipe, page_index, width_px)
        except Exception:  # resvg 現畫失敗：改用驗證時的渲染
            image = None
        if image is not None:
            return image, "svg-resvg"
        fr = next((r for r in (report.format_reports if report else []) if r.format == OutputFormat.SVG and r.render is not None), None)
        if fr is not None and pipe.store.blob_path(fr.render).exists():
            image = _pdf_page(pipe.store.blob_path(fr.render), page_index, width_px)
            if image is not None:
                return image, "svg-verify"
    if fmt == OutputFormat.PPTX and report is not None and report.target_render is not None and pipe.store.blob_path(report.target_render).exists():
        image = _pdf_page(pipe.store.blob_path(report.target_render), page_index, width_px)
        if image is not None:
            return image, "pptx-libreoffice"
    return _pdf_page(_neutral_pdf(pipe, styled), page_index, width_px), "neutral"


def _stamp(pipe, *names: str) -> str:
    """快取戳記：相關產物的內容雜湊（工作重跑、產物內容改變時跟著改變，舊的預覽圖就不再使用）。"""
    parts = [_RENDER_VERSION] + [f"{nm}={pipe.store.hash_of(nm) if pipe.store.has(nm) else '-'}" for nm in names]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# 寫進磁碟快取的寬度（前端使用的級距與預設寬度）。其他寬度照樣畫，但不存檔：
# 否則任意寬度 × 頁數 × 種類 × 格式都會各存一份，耗用磁碟。
_CACHED_WIDTHS = {160, 600, 1000, 1200, 1600, 2400, 3200, 4000}


def _cached_png(pipe, job_id: str, key: str, stamp: str, make, cacheable: bool = True) -> tuple[bytes, dict[str, str]]:
    """預覽圖的磁碟快取：<工作目錄>/previews/<key>-<stamp>.png（另存一個同名的 .json 記用什麼畫的）。
    工作還在排隊或處理中時不寫快取（那時的產物可能只更新了一半）。同一個 key 的舊戳記檔案在寫入新檔時一併刪除。
    cacheable 為 False（不常用的寬度）時只畫不存。"""
    folder = pipe.dir / "previews"
    path = folder / f"{key}-{stamp}.png"
    info = path.with_suffix(".json")
    if path.exists():
        meta = json.loads(info.read_text(encoding="utf-8")) if info.exists() else {}
        return path.read_bytes(), meta
    im, meta = make()
    data = _png_bytes(im)
    if cacheable and JOBS.status(job_id)["status"] not in ("queued", "running"):
        folder.mkdir(exist_ok=True)
        for old in folder.glob(f"{key}-*"):
            if old.stem != path.stem:
                old.unlink(missing_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        atomic_write_text(info, json.dumps(meta))
    return data, meta


@app.get("/api/jobs/{job_id}/pages/{n}/image")
def page_image(
    request: Request, job_id: str, n: int, kind: Literal["original", "result", "overlay"] = "original",
    width: int = Query(1200, ge=100, le=4000), format: Optional[str] = Query(None, description="result、overlay 要畫哪個輸出格式；省略時用這份工作的第一個輸出格式"),
) -> Response:
    """original：原圖（攤平後的工作影像）；result：某個輸出格式的成品畫出來的樣子；
    overlay：兩者半透明疊在一起，並用紅框標出低信心或有問題的元素。

    format（只對 result、overlay 有意義）：pdf、ai_pdf 用 PDFium 直接畫輸出檔；svg 用 resvg 現畫（不行就用驗證時的渲染）；
    pptx 用⑦以 LibreOffice 轉出的 PDF；其他格式或沒有渲染結果時，退回成品文件的中性渲染（ai_pdf 輸出器畫出來的 PDF）。
    回應標頭 X-Mortise-Render 寫明用什麼畫的。format 不是認得的格式回 422；這份工作沒有輸出這個格式回 404。

    有指定輸出尺寸時：result 是整張輸出頁面（含留白），寬為 width、高依輸出頁面的長寬比；original 仍是原圖；
    overlay 以輸出頁面為底，原圖縮放後疊在原始內容的位置（placement）。
    同一份工作、同一個格式、同一個寬度的圖會快取，工作重跑後自動失效（以產物的內容雜湊為準）。"""
    pipe = _job(job_id)
    intent = _intent(pipe)
    if pipe.store.has("s1/source_doc") and not 1 <= n <= len(pipe.store.load("s1/source_doc", SourceDoc).pages):
        raise HTTPException(404, "沒有這一頁。")
    fmt = None
    if kind != "original":
        if format is None:
            fmt = intent.outputs[0] if intent.outputs else OutputFormat.PPTX
        else:
            try:
                fmt = OutputFormat(format)
            except ValueError:
                raise HTTPException(422, "不認得的輸出格式。可用的格式見 /api/formats。")
            if fmt not in intent.outputs:
                raise HTTPException(404, f"這份工作沒有輸出 {fmt.value} 格式。")
    needed = [f"s2/page-{n:03d}"] + ([] if kind == "original" else ["s5/styled_doc", "s7/fidelity_report"])
    if not all(pipe.store.has(nm) for nm in needed):
        raise HTTPException(409, "這份工作還沒處理到這一步。")
    # 先用產物的雜湊算戳記與 ETag、查磁碟快取；命中時不必解析成品文件與報告（大文件每次解析要幾十毫秒）
    stamp = _stamp(pipe, f"s2/page-{n:03d}", *(() if kind == "original" else ("s5/styled_doc", f"s6/{fmt.value}", "s7/fidelity_report")))
    key = f"{kind}-{fmt.value if fmt else 'source'}-p{n}-w{width}"
    etag = f'"{stamp}-{key}"'
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    spec = styled = report = None

    def load() -> None:
        nonlocal spec, styled, report
        spec = pipe.store.load(f"s2/page-{n:03d}", PageSpec)
        if kind != "original":
            styled = _load_parsed(pipe, "s5/styled_doc", StyledDoc)
            if not 1 <= n <= len(styled.pages):
                raise HTTPException(404, "沒有這一頁。")
            report = _load_parsed(pipe, "s7/fidelity_report", FidelityReport)

    def original_image(w: int) -> Image.Image:
        im = Image.open(pipe.store.blob_path(spec.working_image)).convert("RGB")
        return im.resize((w, max(1, round(w * im.size[1] / im.size[0]))), Image.LANCZOS)

    def make():
        load()
        if kind == "original":
            return original_image(width), {"render": "original"}
        sp = styled.pages[n - 1]
        result, engine = _render_result(pipe, fmt, n - 1, width, styled, report)
        if result is None:
            raise HTTPException(409, "沒有輸出檔的渲染結果。")
        if sp.placement is None:
            orig = original_image(width)
            result = result.resize(orig.size, Image.LANCZOS)  # 與原圖同尺寸，疊圖時才對得齊
        else:
            result = result.resize((width, max(1, round(width * sp.height_pt / sp.width_pt))), Image.LANCZOS)
        if kind == "result":
            return result, {"render": engine}
        scale = width / sp.width_pt
        if sp.placement is None:
            over = Image.blend(orig, result, 0.5)
        else:  # 輸出頁面為底；原圖縮放到原始內容的位置再疊上去（留白處只有成品）
            pl = sp.placement
            box = (round(pl.x * scale), round(pl.y * scale), round((pl.x + pl.w) * scale), round((pl.y + pl.h) * scale))
            region = result.crop(box)
            src = Image.open(pipe.store.blob_path(spec.working_image)).convert("RGB").resize(region.size, Image.LANCZOS)
            over = result.copy()
            over.paste(Image.blend(src, region, 0.5), box[:2])
        flagged = _flagged(report, n - 1, fmt)
        draw = ImageDraw.Draw(over)
        for item in list(sp.text_boxes) + list(sp.tables) + list(sp.images):
            if item.id in flagged:
                b = item.box
                draw.rectangle([b.x * scale, b.y * scale, (b.x + b.w) * scale, (b.y + b.h) * scale], outline=(220, 30, 30), width=3)
        return over, {"render": engine}

    data, meta = _cached_png(pipe, job_id, key, stamp, make, cacheable=width in _CACHED_WIDTHS)
    if meta.get("render"):
        headers["X-Mortise-Render"] = meta["render"]
    return Response(data, media_type="image/png", headers=headers)


def _flagged(report: FidelityReport, page_index: int, fmt: Optional[OutputFormat] = None) -> dict[str, list[str]]:
    """低信心或驗證有問題的元素 → 問題說明。
    除了各頁的問題，也收各輸出格式回讀驗證裡「逐行文字對照」的問題（這一行找不到、疊在一起、錯位；
    element_id 是那一行所屬的文字框或表格）：指定 fmt（Overlay 用）時只收那個格式的，不指定就收所有格式的（相同的說明只列一次）。"""
    low = CFG.section("verify").get("low_confidence", 0.7)
    out: dict[str, list[str]] = {}
    page = report.pages[page_index]
    for e in page.elements:
        if e.confidence < low:
            out.setdefault(e.element_id, []).append(f"信心值 {e.confidence:.2f} 偏低")
    for i in page.issues:
        if i.element_id and i.severity.value in ("warning", "error"):
            out.setdefault(i.element_id, []).append(i.message)
    for fr in report.format_reports:
        if fmt is not None and fr.format != fmt:
            continue
        for fp in fr.pages:
            if fp.page_index != page_index:
                continue
            for i in fp.issues:
                if i.code in LINE_CODES and i.element_id and i.severity.value in ("warning", "error"):
                    msgs = out.setdefault(i.element_id, [])
                    if i.message not in msgs:
                        msgs.append(i.message)
    return out


@app.get("/api/jobs/{job_id}/pages/{n}/elements")
def page_elements(job_id: str, n: int) -> dict[str, Any]:
    """前端修正畫面用：每個元素的位置（pt，原點左上）、內容、樣式、信心值與問題。"""
    pipe = _job(job_id)
    if not pipe.store.has("s5/styled_doc"):
        raise HTTPException(409, "這份工作還沒處理到這一步。")
    styled = _load_parsed(pipe, "s5/styled_doc", StyledDoc)
    if not 1 <= n <= len(styled.pages):
        raise HTTPException(404, "沒有這一頁。")
    report = _load_parsed(pipe, "s7/fidelity_report", FidelityReport) if pipe.store.has("s7/fidelity_report") else None
    reports = {e.element_id: e for e in report.pages[n - 1].elements} if report else {}
    flagged = _flagged(report, n - 1) if report else {}
    sp = styled.pages[n - 1]

    def para(p) -> dict[str, Any]:
        return {
            "id": p.id, "text": "".join(r.text for r in p.runs), "align": p.align.value, "role": p.role.value, "style_id": p.style_id,
            "runs": [{"text": r.text, "font": r.font.family.value, "font_confidence": r.font.family.confidence, "verified_with": r.font.verification_family,
                      "size_pt": r.size_pt.value, "size_source": r.size_pt.source.value, "bold": r.bold, "italic": r.italic, "color": "#" + r.color.hex} for r in p.runs],
        }

    def meta(item_id: str) -> dict[str, Any]:
        e = reports.get(item_id)
        return {"confidence": e.confidence if e else None, "fields": {k: v.value for k, v in e.fields.items()} if e else {}, "issues": flagged.get(item_id, [])}

    def box(b) -> dict[str, float]:
        return {"x": b.x, "y": b.y, "w": b.w, "h": b.h, "rotation": b.rotation}

    return {
        "page": n, "width_pt": sp.width_pt, "height_pt": sp.height_pt,
        "background_color": "#" + sp.background_color.hex if sp.background_color else None,
        "text_boxes": [{"id": t.id, "box": box(t.box), "z": t.z, "paragraphs": [para(p) for p in t.paragraphs], **meta(t.id)} for t in sp.text_boxes],
        "tables": [{"id": t.id, "box": box(t.box), "z": t.z, "rows": len(t.row_heights_pt), "cols": len(t.col_widths_pt), "header_rows": t.header_rows,
                    "cells": [{"row": c.row, "col": c.col, "row_span": c.row_span, "col_span": c.col_span, "paragraphs": [para(p) for p in c.paragraphs],
                               "id": f"{t.id}#{c.row},{c.col}", "value": c.value, "number_format": c.number_format,
                               "formula": c.formula, "formula_candidate": c.formula_candidate} for c in t.cells], **meta(t.id)} for t in sp.tables],
        "images": [{"id": i.id, "box": box(i.box), "z": i.z, "url": None, **meta(i.id)} for i in sp.images],
        "shapes": [{"id": s.id, "box": box(s.box), "z": s.z, "geometry": s.geometry, "fill": "#" + s.fill.hex if s.fill else None, **meta(s.id)} for s in sp.shapes],
        "styles": [{"id": s.id, "name": s.name, "font": s.font_family, "size_pt": s.size_pt, "bold": s.bold, "italic": s.italic, "color": "#" + s.color.hex} for s in styled.styles],
    }


# ====================================================================== 修正與回饋


class CorrectionIn(BaseModel):
    field: Literal["size", "corners", "text", "role", "align", "font_family", "font_size_pt", "color", "formula"]
    value: Any = Field(..., description="size 為 {width, height, unit}；corners 為原圖像素座標的四個點 [[x,y]×4]（左上、右上、右下、左下）；color 為 #RRGGBB；其他為字串或數字")
    element_id: Optional[str] = Field(None, description="文字框、段落或文字行的編號；表格儲存格為「表格編號#列,欄」；size 不需要")
    page: Optional[int] = Field(None, description="頁碼（從 1 起算）；size 可省略表示整份文件")


@app.post("/api/jobs/{job_id}/corrections", status_code=202)
def post_corrections(job_id: str, items: list[CorrectionIn]) -> dict[str, Any]:
    """使用者的修正：信心值 1.0，之後任何重跑都不會覆蓋。只從受影響的階段重跑（例如改尺寸不會重跑辨識）。"""
    pipe = _job(job_id)
    existing = len(pipe.corrections)
    corrections = []
    for i, it in enumerate(items):
        stage = FIELD_STAGE[it.field]
        value = it.value
        if it.field == "size":
            try:
                value = PhysicalSize(**value).model_dump()
            except Exception:
                raise HTTPException(422, "size 的值要是 {width, height, unit}。")
        if it.field == "text" and it.element_id and "-t-" in it.element_id:
            stage = 3  # 指名到文字行：修正辨識結果
        corrections.append(user_correction(stage, it.field, value, it.element_id, (it.page - 1) if it.page else None, n=existing + i))
    JOBS.add_corrections(job_id, corrections)
    return JOBS.status(job_id)


class FeedbackIn(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5, description="整體評分 1 到 5")
    page: Optional[int] = None
    element_id: Optional[str] = None
    correct: Optional[bool] = Field(None, description="這個元素是對的（True）或錯的（False）")
    comment: Optional[str] = Field(None, max_length=5000)
    tags: list[str] = Field(default_factory=list, description="例如 font_wrong、text_wrong、layout_broken、table_wrong")


@app.post("/api/jobs/{job_id}/feedback", status_code=201)
def post_feedback(job_id: str, fb: FeedbackIn) -> dict[str, Any]:
    _job(job_id)
    path = JOBS.save_feedback(job_id, fb.model_dump())
    return {"ok": True, "id": path.stem}


# ====================================================================== 下載與刪除


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str) -> Response:
    pipe = _job(job_id)
    if fmt == "report":
        report = _load(pipe, "s7/fidelity_report", FidelityReport)
        name = Path(pipe.state.get("filename", "report")).stem + ".report.json"
        return Response(report.model_dump_json(indent=1, exclude={"target_render"}), media_type="application/json", headers={"Content-Disposition": _attachment(name)})
    try:
        of = OutputFormat(fmt)
    except ValueError:
        raise HTTPException(404, "不認得的格式。")
    out = _load(pipe, f"s6/{of.value}", OutputFile)
    return FileResponse(pipe.store.blob_path(out.file), media_type=out.file.media_type, filename=out.filename)


def _attachment(name: str) -> str:
    """下載的 Content-Disposition：檔名可能有中文或引號，HTTP 標頭只能放 latin-1，所以用 RFC 5987 的 filename* 帶 UTF-8 檔名，
    另附一個只有 ASCII 的後備檔名給舊瀏覽器。"""
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    _job(job_id)
    JOBS.delete(job_id)
    return Response(status_code=204)


# ====================================================================== 開發用的簡易頁面（正式前端在 web/）


@app.get("/dev", response_class=HTMLResponse)
def dev_page() -> str:
    return """<!doctype html><meta charset="utf-8"><title>Mortise</title>
<style>body{font:15px system-ui;margin:2rem;max-width:900px}img{max-width:100%;border:1px solid #ccc}</style>
<h1>Mortise 開發測試頁</h1>
<form id="f"><input type="file" name="file" required> 輸出 <input name="outputs" value="pptx"> <button>上傳</button></form>
<pre id="s"></pre><div id="r"></div>
<script>
f.onsubmit=async e=>{e.preventDefault();const j=await (await fetch('/api/jobs',{method:'POST',body:new FormData(f)})).json();
s.textContent=JSON.stringify(j,null,1);const es=new EventSource(`/api/jobs/${j.job_id}/events`);
es.onmessage=async m=>{const d=JSON.parse(m.data);s.textContent=JSON.stringify(d);if(d.final){es.close();
const pages=await (await fetch(`/api/jobs/${j.job_id}/pages`)).json();
r.innerHTML=pages.map(p=>`<h3>第 ${p.page} 頁</h3><img src="${p.images.overlay}">`).join('')+
`<p><a href="/api/jobs/${j.job_id}/download/pptx">下載 PPTX</a> · <a href="/api/jobs/${j.job_id}/download/report">還原度報告</a></p>`}}}
</script>"""


# ====================================================================== 前端（web/dist）

WEB_DIST = Path(os.environ.get("MORTISE_WEB_DIST", str(Path(__file__).resolve().parents[2] / "web" / "dist")))
_LONG_CACHE = "public, max-age=31536000, immutable"  # Vite 建置出的 assets/ 檔名含內容雜湊，內容變了檔名就變
# 前端頁面的內容安全政策：只載入自己的檔案（字型自帶、不連外部服務）；pdf.js 需要 blob: 的 worker 與 WebAssembly；
# 圖片有預覽用的 blob:；不允許被別的網站用 iframe 嵌入。
_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
    "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


_BUILD_HELP = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>Mortise</title>
<style>body{font:15px/1.6 system-ui,sans-serif;margin:3rem auto;max-width:640px;padding:0 1rem;color:#222}code,pre{background:#f3f3f3;border-radius:4px}pre{padding:.8rem 1rem}code{padding:.1rem .3rem}</style>
<h1>Mortise</h1>
<p>前端還沒有建置（找不到 <code>web/dist/index.html</code>）。在專案根目錄執行：</p>
<pre>cd web
npm install
npm run build</pre>
<p>建置完重新整理這一頁即可（不必重新啟動伺服器）。開發時可以改跑 <code>npm run dev</code>（Vite 會把 <code>/api</code> 轉到 http://127.0.0.1:8000）。</p>
<p>API 說明：<a href="/docs">/docs</a>　開發測試頁：<a href="/dev">/dev</a></p>
</html>"""


class _Frontend(StaticFiles):
    """前端的靜態檔案加單頁應用的後備：
    - /api 開頭的路徑一律 404（API 的路由在前面；走到這裡表示 API 沒有這個端點，絕不回前端頁面）。
    - 實體檔案照常回傳；assets/ 底下的檔名含內容雜湊，給長快取；其他檔案（index.html、favicon 等）不快取或短快取。
    - 不是實體檔案的路徑回 index.html（前端自己的路由）；但 assets/ 底下找不到的檔案回 404，免得瀏覽器把 HTML 當成程式碼載入。
    - 還沒建置（沒有 index.html）時，首頁顯示建置說明。目錄是每次請求時才檢查，建置完不必重新啟動伺服器。"""

    async def check_config(self) -> None:  # 目錄可能還不存在（還沒建置）：不在啟動時報錯
        return None

    async def get_response(self, path: str, scope) -> Response:
        path = path.replace(os.sep, "/")
        if path == "api" or path.startswith("api/"):
            allowed = _api_methods("/" + path)
            if allowed:  # 有這個 API 端點，只是方法不對：回 405（不要被前端的掛載點吃成 404）
                raise StarletteHTTPException(status_code=405, headers={"Allow": ", ".join(sorted(allowed))})
            raise StarletteHTTPException(status_code=404)
        index = Path(self.directory) / "index.html"
        if not index.is_file():
            if path in (".", "", "index.html"):
                return HTMLResponse(_BUILD_HELP, headers={"Cache-Control": "no-cache"})
            raise StarletteHTTPException(status_code=404)
        if path not in (".", "", "index.html"):
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as e:
                if e.status_code != 404 or path.startswith("assets/"):
                    raise
            else:
                if response.status_code < 400:
                    response.headers["Cache-Control"] = _LONG_CACHE if path.startswith("assets/") else "public, max-age=3600"
                    if path.endswith(".html"):
                        response.headers["Content-Security-Policy"] = _CSP
                return response
        if scope["method"] not in ("GET", "HEAD"):
            raise StarletteHTTPException(status_code=405)
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache", "Content-Security-Policy": _CSP})


def _api_methods(path: str) -> set[str]:
    """這個路徑符合哪些 API 端點的哪些方法（路徑符合、方法不符時，Starlette 的比對結果是 PARTIAL）。"""
    scope = {"type": "http", "path": path, "root_path": "", "method": "__none__"}
    allowed: set[str] = set()
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if methods and route.matches(scope)[0] == Match.PARTIAL:
            allowed |= set(methods)
    return allowed


# 一定要放在所有 API 路由之後：路由依登記順序比對，API 先比對，剩下的才交給前端
app.mount("/", _Frontend(directory=str(WEB_DIST), check_dir=False), name="web")
