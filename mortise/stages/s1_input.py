"""① 輸入：收使用者的檔案，交出來源文件 SourceDoc。不用 AI。

步驟：
  s1.sniff      以檔案內容（不是副檔名）判斷類型，檢查大小上限
  s1.open       開啟檔案；有密碼或損壞就回報
  s1.classify   逐頁判斷輸入類型（原生 PDF／掃描 PDF／混合 PDF／截圖／照片）
  s1.fake_native 假原生初判（隱藏的 OCR 文字層、缺 Unicode 對照、文字轉外框）
  s1.metadata   擷取 Producer、Creator 等中繼資料
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ..artifacts import FakeNativeFlags, InputType, Intent, PageRef, Provenance, SourceDoc, measured
from ..config import Config
from ..pipeline.store import Store, sha256_bytes
from ..tools import pdfium as pdf

NAME = "s1"
VERSION = "1"


class InputError(Exception):
    """使用者的檔案無法處理（訊息會直接顯示給使用者）。"""


def _sniff(data: bytes) -> str:
    head = data[:1024]
    if b"%PDF-" in head:
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise InputError("不支援的檔案類型。目前接受 PDF、PNG、JPEG。")


def run(path: Path, filename: str, intent: Intent, cfg: Config, store: Store) -> SourceDoc:
    c = cfg.section("input")
    data = Path(path).read_bytes()
    if len(data) > c["max_file_mb"] * 1024 * 1024:
        raise InputError(f"檔案超過 {c['max_file_mb']} MB 上限。")
    media_type = _sniff(data)
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[media_type]
    blob = store.put_blob(data, media_type, ext)
    doc_id = sha256_bytes(data)
    if media_type == "application/pdf":
        pages, metadata, warnings = _pdf_pages(store.blob_path(blob), c)
    else:
        pages, metadata, warnings = _image_pages(data, media_type, c)
    return SourceDoc(doc_id=doc_id, filename=filename, media_type=media_type, source=blob, metadata=metadata, intent=intent, pages=pages, warnings=warnings)


def _pdf_pages(path: Path, c: dict) -> tuple[list[PageRef], dict[str, str], list[str]]:
    try:
        doc = pdf.open_document(str(path))
    except pdf.PdfOpenError as e:
        if "password" in str(e).lower():
            raise InputError("這份 PDF 有密碼保護，請先解除密碼再上傳。") from e
        raise InputError("PDF 無法開啟，檔案可能已損壞。") from e
    n = len(doc)
    if n == 0:
        raise InputError("這份 PDF 沒有任何頁面。")
    if n > c["max_pages"]:
        raise InputError(f"頁數超過 {c['max_pages']} 頁上限（這份有 {n} 頁）。")
    pages: list[PageRef] = []
    warnings: list[str] = []
    for i in range(n):
        reader = pdf.PageReader(doc, i)
        try:
            chars = [ch for ch in reader.chars() if not ch.generated and ch.text.strip()]
            total = len(chars)
            hidden = sum(1 for ch in chars if ch.render_mode == pdf.RENDER_MODE_INVISIBLE or (ch.fill is not None and ch.fill[3] == 0))
            bad = sum(1 for ch in chars if ch.map_error)
            counts = reader.object_counts()
            boxes = reader.boxes()
            l, b, r, t = boxes.get("crop", boxes["media"])
            page_area = max((r - l) * (t - b), 1e-6)
            coverage = 0.0
            for im in reader.images():
                xs = [p[0] for p in im.corners]
                ys = [p[1] for p in im.corners]
                w = max(0.0, min(max(xs), r) - max(min(xs), l))
                h = max(0.0, min(max(ys), t) - max(min(ys), b))
                coverage = max(coverage, w * h / page_area)
        finally:
            reader.close()
        hidden_ratio = hidden / total if total else 0.0
        bad_ratio = bad / total if total else 0.0
        visible = total - hidden
        outlined = visible == 0 and counts.get("path", 0) > 200 and coverage < c["scan_image_coverage"]
        flags = FakeNativeFlags(hidden_text_ratio=hidden_ratio, unicode_error_ratio=bad_ratio, outlined_text_suspected=outlined)
        big_image = coverage >= c["scan_image_coverage"]
        if visible >= c["native_min_chars"] and bad_ratio < c["unicode_error_ratio"]:
            kind, conf = (InputType.MIXED_PDF, 0.6) if big_image else (InputType.NATIVE_PDF, 0.95)
        elif big_image:
            kind, conf = InputType.SCANNED_PDF, 0.9 if hidden_ratio >= c["hidden_text_ratio"] or total == 0 else 0.7
        elif bad_ratio >= c["unicode_error_ratio"] and visible > 0:
            kind, conf = InputType.SCANNED_PDF, 0.6  # 文字讀不出正確內容：改走影像產線
            warnings.append(f"第 {i + 1} 頁的文字缺少 Unicode 對照，改用影像辨識。")
        else:
            kind, conf = InputType.NATIVE_PDF, 0.5 if outlined else 0.8
            if outlined:
                warnings.append(f"第 {i + 1} 頁沒有文字但有大量路徑，文字可能已轉成外框。")
        pages.append(
            PageRef(
                page_index=i, input_type=measured(kind, conf, Provenance.INFERRED, f"{NAME}.classify@{VERSION}"),
                char_count=total, image_count=counts.get("image", 0), path_count=counts.get("path", 0),
                largest_image_coverage=coverage, fake_native=flags,
            )
        )
    metadata = pdf.document_metadata(doc)
    doc.close()
    return pages, metadata, warnings


def _image_pages(data: bytes, media_type: str, c: dict) -> tuple[list[PageRef], dict[str, str], list[str]]:
    try:
        Image.MAX_IMAGE_PIXELS = int(c["max_image_megapixels"] * 1_000_000)
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # 壞檔、解壓縮炸彈
        raise InputError("影像無法開啟，檔案可能已損壞或尺寸過大。") from e
    exif = im.getexif()
    camera = bool(exif.get(271) or exif.get(272))  # 相機廠牌或型號
    orientation = exif.get(274, 1)
    w, h = im.size
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    dpi = im.info.get("dpi")
    dpi_hint = float(dpi[0]) if dpi and dpi[0] and dpi[0] > 1 else None
    if camera:
        kind, conf = InputType.PHOTO, 0.9
    elif media_type == "image/png":
        kind, conf = InputType.SCREENSHOT, 0.6
    else:
        kind, conf = InputType.PHOTO, 0.5
    meta = {"format": media_type}
    if camera:
        meta["camera"] = f"{exif.get(271, '')} {exif.get(272, '')}".strip()
    page = PageRef(page_index=0, input_type=measured(kind, conf, Provenance.INFERRED, f"{NAME}.classify@{VERSION}"), pixel_size=(w, h), dpi_hint=dpi_hint)
    return [page], meta, []
