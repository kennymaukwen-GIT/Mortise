"""對外 API（FastAPI）。前端頁面只透過這裡和流水線溝通。

啟動：
    uvicorn mortise.api.app:app --host 0.0.0.0 --port 8000
環境變數：
    MORTISE_JOBS      工作目錄的根目錄（預設 ./jobs）
    MORTISE_WORKERS   同時處理幾份檔案（預設 2）
    MORTISE_CORS      允許的前端來源，逗號分隔（預設不開放跨來源）
    MORTISE_RETENTION_DAYS  檔案保留天數（預設 30）

端點一覽（詳細說明見各函數）：
    GET  /api/health                         服務狀態
    GET  /api/formats                        可選的輸出格式
    POST /api/jobs                           上傳檔案，建立工作
    GET  /api/jobs/{id}                      工作狀態與進度
    GET  /api/jobs/{id}/events               進度串流（Server-Sent Events）
    GET  /api/jobs/{id}/report               還原度報告
    GET  /api/jobs/{id}/pages                各頁尺寸與預覽圖網址
    GET  /api/jobs/{id}/pages/{n}/image      預覽圖：original（原圖）、result（輸出檔渲染）、overlay（疊圖）
    GET  /api/jobs/{id}/pages/{n}/elements   可修正的元素清單（文字框、表格、圖片、形狀，含信心值與問題）
    POST /api/jobs/{id}/corrections          送出修正，只從受影響的階段重跑
    GET  /api/jobs/{id}/download/{format}    下載輸出檔（format 為輸出格式或 report）
    POST /api/jobs/{id}/feedback             回饋（評分、意見）
    DELETE /api/jobs/{id}                    立即刪除這份工作的所有檔案
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from ..artifacts import FidelityReport, Intent, Layout, OutputFile, OutputFormat, PageSpec, PhysicalSize, SourceDoc, StyledDoc, UsageMode
from ..config import Config
from ..pipeline.runner import user_correction
from ..stages import s6_export
from ..tools import pdfium as pdf
from .jobs import JobManager

CFG = Config.load()
JOBS = JobManager(
    Path(os.environ.get("MORTISE_JOBS", "jobs")),
    workers=int(os.environ.get("MORTISE_WORKERS", "2")),
    retention_days=float(os.environ.get("MORTISE_RETENTION_DAYS", "30")),
    cfg=CFG,
)

@asynccontextmanager
async def _lifespan(_app):
    JOBS.purge_expired()  # 啟動時清掉超過保留期限的工作
    yield


app = FastAPI(title="Mortise API", version="0.1.0", description="把不可編輯的文件還原成可編輯、排版一致的檔案", lifespan=_lifespan)
_origins = [o.strip() for o in os.environ.get("MORTISE_CORS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])

FORMAT_LABELS = {
    OutputFormat.PPTX: "PowerPoint（.pptx）",
    OutputFormat.GOOGLE_SLIDES: "Google 簡報（匯入用的 .pptx）",
    OutputFormat.DOCX_FIXED: "Word 版面固定（.docx）",
    OutputFormat.DOCX_FLOW: "Word 可重排（.docx）",
    OutputFormat.XLSX: "Excel 表格（.xlsx）",
    OutputFormat.AI_PDF: "Illustrator 可編輯 PDF（.pdf）",
    OutputFormat.IDML: "InDesign（.idml）",
    OutputFormat.TEMPLATE: "版面模板（.pptx）",
}

# 修正欄位 → 送給哪個階段
FIELD_STAGE = {"size": 2, "role": 4, "align": 5, "font_family": 5, "font_size_pt": 5, "color": 5, "text": 5}


def _job(job_id: str):
    try:
        return JOBS.pipeline(job_id)
    except KeyError:
        raise HTTPException(404, "找不到這份工作（可能已過保留期限而刪除）。")


def _load(pipe, name: str, cls):
    if not pipe.store.has(name):
        raise HTTPException(409, "這份工作還沒處理到這一步。")
    return pipe.store.load(name, cls)


# ====================================================================== 基本資訊


@app.get("/api/health")
def health() -> dict[str, Any]:
    from ..tools import libreoffice

    return {"ok": True, "verify_engine": libreoffice.version() if libreoffice.available() else None, "formats": [f.value for f in s6_export.available()]}


@app.get("/api/formats")
def formats() -> list[dict[str, Any]]:
    ready = set(s6_export.available())
    return [{"format": f.value, "label": FORMAT_LABELS[f], "available": f in ready} for f in OutputFormat]


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
) -> dict[str, Any]:
    try:
        wanted = [OutputFormat(x.strip()) for x in outputs.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "不認得的輸出格式。可用的格式見 /api/formats。")
    ready = set(s6_export.available())
    missing = [f.value for f in wanted if f not in ready]
    if missing:
        raise HTTPException(422, f"這些輸出格式還沒有開放：{', '.join(missing)}")
    size = PhysicalSize(width=width, height=height, unit=unit) if width and height else None
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
        job_id = JOBS.create(Path(tmp.name), Path(file.filename or "upload").name, Intent(outputs=wanted or [OutputFormat.PPTX], mode=UsageMode(mode), known_size=size), allow_training)
    finally:
        os.unlink(tmp.name)
    return JOBS.status(job_id)


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
        sent = 0
        idle = 0
        while True:
            events = JOBS.events_since(job_id, sent)
            for e in events:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            sent += len(events)
            status = JOBS.status(job_id)["status"]
            if status in ("done", "failed") and not events:
                yield f"data: {json.dumps({'status': status, 'final': True}, ensure_ascii=False)}\n\n"
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
    return data


@app.get("/api/jobs/{job_id}/pages")
def job_pages(job_id: str) -> list[dict[str, Any]]:
    pipe = _job(job_id)
    doc = _load(pipe, "s1/source_doc", SourceDoc)
    styled = pipe.store.load("s5/styled_doc", StyledDoc) if pipe.store.has("s5/styled_doc") else None
    out = []
    for ref in doc.pages:
        n = ref.page_index + 1
        item: dict[str, Any] = {"page": n, "input_type": ref.input_type.value.value, "input_type_confidence": ref.input_type.confidence}
        if pipe.store.has(f"s2/page-{n:03d}"):
            spec = pipe.store.load(f"s2/page-{n:03d}", PageSpec)
            item.update({"width_pt": spec.page_width_pt.value, "height_pt": spec.page_height_pt, "size_source": spec.page_width_pt.source.value, "size_confidence": spec.page_width_pt.confidence, "notes": spec.notes})
        if styled and ref.page_index < len(styled.pages):
            sp = styled.pages[ref.page_index]
            item.update({"width_pt": sp.width_pt, "height_pt": sp.height_pt})
        base = f"/api/jobs/{job_id}/pages/{n}/image"
        item["images"] = {k: f"{base}?kind={k}" for k in ("original", "result", "overlay")}
        item["elements"] = f"/api/jobs/{job_id}/pages/{n}/elements"
        out.append(item)
    return out


def _png(im: Image.Image) -> Response:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-cache"})


def _result_image(pipe, report: FidelityReport, page_index: int, width_px: int) -> Optional[Image.Image]:
    if report.target_render is None:
        return None
    path = pipe.store.blob_path(report.target_render)
    doc = pdf.open_document(str(path))
    try:
        if page_index >= len(doc):
            return None
        reader = pdf.PageReader(doc, page_index)
        try:
            bx = reader.boxes()
            l, b, r, t = bx.get("crop", bx["media"])
            return reader.render(72.0 * width_px / max(r - l, 1.0))
        finally:
            reader.close()
    finally:
        doc.close()


@app.get("/api/jobs/{job_id}/pages/{n}/image")
def page_image(job_id: str, n: int, kind: Literal["original", "result", "overlay"] = "original", width: int = Query(1200, ge=100, le=4000)) -> Response:
    """original：原圖（攤平後的工作影像）；result：輸出檔用驗證引擎渲染的樣子；
    overlay：兩者半透明疊在一起，並用紅框標出低信心或有問題的元素。"""
    pipe = _job(job_id)
    spec = _load(pipe, f"s2/page-{n:03d}", PageSpec)
    original = Image.open(pipe.store.blob_path(spec.working_image)).convert("RGB")
    h = round(width * original.size[1] / original.size[0])
    original = original.resize((width, h), Image.LANCZOS)
    if kind == "original":
        return _png(original)
    report = _load(pipe, "s7/fidelity_report", FidelityReport)
    result = _result_image(pipe, report, n - 1, width)
    if result is None:
        raise HTTPException(409, "沒有輸出檔的渲染結果（驗證引擎未執行）。")
    result = result.resize((width, h), Image.LANCZOS)
    if kind == "result":
        return _png(result)
    over = Image.blend(original, result, 0.5)
    styled = _load(pipe, "s5/styled_doc", StyledDoc)
    sp = styled.pages[n - 1]
    scale = width / sp.width_pt
    flagged = _flagged(report, n - 1)
    draw = ImageDraw.Draw(over)
    for item in list(sp.text_boxes) + list(sp.tables) + list(sp.images):
        if item.id in flagged:
            b = item.box
            draw.rectangle([b.x * scale, b.y * scale, (b.x + b.w) * scale, (b.y + b.h) * scale], outline=(220, 30, 30), width=3)
    return _png(over)


def _flagged(report: FidelityReport, page_index: int) -> dict[str, list[str]]:
    """低信心或驗證有問題的元素 → 問題說明。"""
    low = CFG.section("verify").get("low_confidence", 0.7)
    out: dict[str, list[str]] = {}
    page = report.pages[page_index]
    for e in page.elements:
        if e.confidence < low:
            out.setdefault(e.element_id, []).append(f"信心值 {e.confidence:.2f} 偏低")
    for i in page.issues:
        if i.element_id and i.severity.value in ("warning", "error"):
            out.setdefault(i.element_id, []).append(i.message)
    return out


@app.get("/api/jobs/{job_id}/pages/{n}/elements")
def page_elements(job_id: str, n: int) -> dict[str, Any]:
    """前端修正畫面用：每個元素的位置（pt，原點左上）、內容、樣式、信心值與問題。"""
    pipe = _job(job_id)
    styled = _load(pipe, "s5/styled_doc", StyledDoc)
    if not 1 <= n <= len(styled.pages):
        raise HTTPException(404, "沒有這一頁。")
    report = pipe.store.load("s7/fidelity_report", FidelityReport) if pipe.store.has("s7/fidelity_report") else None
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
                               "value": c.value, "number_format": c.number_format} for c in t.cells], **meta(t.id)} for t in sp.tables],
        "images": [{"id": i.id, "box": box(i.box), "z": i.z, "url": None, **meta(i.id)} for i in sp.images],
        "shapes": [{"id": s.id, "box": box(s.box), "z": s.z, "geometry": s.geometry, "fill": "#" + s.fill.hex if s.fill else None, **meta(s.id)} for s in sp.shapes],
        "styles": [{"id": s.id, "name": s.name, "font": s.font_family, "size_pt": s.size_pt, "bold": s.bold, "italic": s.italic, "color": "#" + s.color.hex} for s in styled.styles],
    }


# ====================================================================== 修正與回饋


class CorrectionIn(BaseModel):
    field: Literal["size", "text", "role", "align", "font_family", "font_size_pt", "color"]
    value: Any = Field(..., description="size 為 {width, height, unit}；color 為 #RRGGBB；其他為字串或數字")
    element_id: Optional[str] = Field(None, description="文字框、段落或文字行的編號；size 不需要")
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
        return Response(report.model_dump_json(indent=1, exclude={"target_render"}), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    try:
        of = OutputFormat(fmt)
    except ValueError:
        raise HTTPException(404, "不認得的格式。")
    out = _load(pipe, f"s6/{of.value}", OutputFile)
    return FileResponse(pipe.store.blob_path(out.file), media_type=out.file.media_type, filename=out.filename)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> Response:
    _job(job_id)
    JOBS.delete(job_id)
    return Response(status_code=204)


# ====================================================================== 開發用的簡易頁面（正式前端另做）


@app.get("/", response_class=HTMLResponse)
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
