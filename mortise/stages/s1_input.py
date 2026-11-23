"""① 輸入：收使用者的檔案，交出來源文件 SourceDoc。不用 AI。

步驟：
  s1.sniff      以檔案內容（不是副檔名）判斷類型，檢查大小上限
  s1.open       開啟檔案；有密碼或損壞就回報
  s1.classify   逐頁判斷輸入類型（原生 PDF／掃描 PDF／混合 PDF／截圖／照片）；
                影像依內容判斷（平塗像素比例、雜訊、有沒有透視變形的紙張四邊），不看檔案格式，相機資訊只當旁證
  s1.fake_native 假原生初判（隱藏的 OCR 文字層、缺 Unicode 對照、文字轉外框）
  s1.metadata   擷取 Producer、Creator 等中繼資料
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image

from ..artifacts import FakeNativeFlags, InputType, Intent, PageRef, Provenance, SourceDoc, measured
from ..config import Config
from ..pipeline.store import Store, sha256_bytes
from ..tools import pdfium as pdf

NAME = "s1"
VERSION = "3"


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
    warnings: list[str] = []
    try:
        feats = image_features(im.convert("RGB"), c)
        kind, conf, why = classify_image(feats, camera, c)
    except Exception as e:  # 量不出來（例如缺少影像套件）：不中斷，退回保守的判斷
        feats = None
        kind, conf, why = (InputType.PHOTO, 0.5, f"影像內容量不出來（{type(e).__name__}），先當照片處理")
    meta = {"format": media_type, "classify": why}
    if feats is not None:
        meta["classify_features"] = feats.summary()
    if camera:
        meta["camera"] = f"{exif.get(271, '')} {exif.get(272, '')}".strip()
        if kind != InputType.PHOTO:
            warnings.append(f"檔案帶有相機資訊（{meta['camera']}），但影像內容是平塗的數位畫面，依內容判為截圖。如果其實是照片，請手動拉出頁面四角。")
    page = PageRef(page_index=0, input_type=measured(kind, conf, Provenance.INFERRED, f"{NAME}.classify@{VERSION}"), pixel_size=(w, h), dpi_hint=dpi_hint)
    return [page], meta, warnings


# ---------- 影像輸入的類型：依影像內容判斷，不看檔案格式，相機資訊只當旁證 ----------


class ImageFeatures:
    """判斷截圖或照片用的影像內容量測。

    flat：3×3 鄰域內所有像素三個通道完全相同的比例（「平塗像素比例」）。
    flat_big：9×9 鄰域完全相同的比例（「大片平塗比例」）。螢幕畫面、軟體匯出的頁面有大片完全同色的底，
          JPEG 壓縮也保得住（整塊同色的 8×8 區塊解回來仍完全同色）；相機拍的影像到處都有感光雜訊與光照的漸變，
          就算雜訊被 JPEG 壓平，完全同色的範圍也只到一個 8×8 區塊為止，不會連成一大片。
    noise：平坦區的雜訊 σ（色階），與②頁面幾何判斷「數位影像」用的是同一個量法（只量影像中央）。
    quad：在縮圖上找到的紙張四邊（method、信心值、佔整張圖的面積比例）；quad_outside_flat 是四邊以外那一圈的大片平塗比例
          （照片裡四邊以外是桌面之類的真實場景；截圖裡斜放的色塊，四邊以外仍是平塗的畫面）。
    quad_persp_deg：四邊形兩組對邊的夾角取大的（度）。拍照的透視會讓對邊不平行；截圖裡的色塊、圖片就算旋轉了，對邊仍然平行。
    quad_outside_flat_mid：同 quad_outside_flat，但只算「不是純黑或純白」的像素。相機把暗處壓成純黑（例如在暗處拍螢幕、投影）
          或把亮處過曝成純白時，四邊以外也會是一大片完全同色，但那不是數位畫面；這時中間色調的像素幾乎都不平塗。
          中間色調的像素太少（不到四邊以外的 5%）時是 None。
    quad_tilt_deg：四邊形的邊離水平或垂直最多偏幾度。截圖裡的色塊、圖片幾乎都和畫面軸對齊（0°）；拍照很難拍得完全正。
    quad_outside_black：四邊以外純黑像素（三個通道都 ≤ 6）的比例。"""

    def __init__(self, flat: float, flat_big: float, noise: float, quad_method: str, quad_conf: float, quad_area: float, quad_outside_flat: Optional[float],
                 quad_persp_deg: float = 0.0, quad_outside_flat_mid: Optional[float] = None, quad_tilt_deg: float = 0.0,
                 quad_outside_black: float = 0.0):
        self.flat = flat
        self.flat_big = flat_big
        self.noise = noise
        self.quad_method = quad_method
        self.quad_conf = quad_conf
        self.quad_area = quad_area
        self.quad_outside_flat = quad_outside_flat
        self.quad_persp_deg = quad_persp_deg
        self.quad_outside_flat_mid = quad_outside_flat_mid
        self.quad_tilt_deg = quad_tilt_deg
        self.quad_outside_black = quad_outside_black

    def summary(self) -> str:
        out = f"平塗像素 {self.flat:.0%}（大片平塗 {self.flat_big:.0%}），雜訊 σ {self.noise:.2f}，紙張四邊 {self.quad_method}（信心值 {self.quad_conf:.2f}）"
        if self.quad_method != "full_image":
            out += f"，佔畫面 {self.quad_area:.0%}"
        if self.quad_outside_flat is not None:
            out += f"，四邊以外的大片平塗 {self.quad_outside_flat:.0%}"
            out += "（不算純黑純白：" + ("幾乎沒有中間色調" if self.quad_outside_flat_mid is None else f"{self.quad_outside_flat_mid:.0%}") + "）"
        if self.quad_method != "full_image":
            out += f"，對邊夾角 {self.quad_persp_deg:.1f}°、邊的傾斜 {self.quad_tilt_deg:.1f}°"
        return out


def _flat_map(arr, k: int):
    """每個像素的 k×k 鄰域是否三個通道都完全相同。"""
    import cv2
    import numpy as np

    ker = np.ones((k, k), np.uint8)
    spread = cv2.subtract(cv2.dilate(arr, ker), cv2.erode(arr, ker))  # 鄰域內最大減最小（uint8，不會是負的）
    return spread.max(axis=2) == 0


def image_features(im: Image.Image, c: dict) -> ImageFeatures:
    """量 ImageFeatures。im 是 RGB 影像（方向不影響結果，不必先轉正）。"""
    import cv2
    import numpy as np

    from ..recognition import page_geometry as pg

    arr = np.asarray(im)
    H, W = arr.shape[:2]
    # 平塗比例：影像太大時隔點取樣（平塗的底隔點看仍然平塗，雜訊隔點看仍然是雜訊）
    step = max(1, int(np.ceil(np.sqrt(H * W / (float(c.get("classify_max_megapixels", 16)) * 1e6)))))
    sub = np.ascontiguousarray(arr[::step, ::step])
    flat = float(_flat_map(sub, 3).mean())
    big = _flat_map(sub, int(c.get("classify_flat_window", 9)))
    cy, cx, r = H // 2, W // 2, 512  # 雜訊只量影像中央（與②相同），先切一塊再轉灰階，大影像才不必整張轉
    noise = float(pg._noise_sigma(cv2.cvtColor(np.ascontiguousarray(arr[max(0, cy - r):cy + r, max(0, cx - r):cx + r]), cv2.COLOR_RGB2GRAY)))
    # 紙張四邊：與②同一個模組，但在縮圖上找、不指定類型（只看有沒有透視變形的紙張）
    side = float(c.get("classify_max_side", 1200))
    s = min(1.0, side / max(H, W))
    small = cv2.resize(arr, (max(8, int(round(W * s))), max(8, int(round(H * s)))), interpolation=cv2.INTER_AREA) if s < 1 else arr
    quad = pg.find_page_quad(small, {"input_type": None})
    area, outside, outside_mid, persp, tilt, black = 1.0, None, None, 0.0, 0.0, 0.0
    if quad.method != "full_image":
        sh, sw = sub.shape[:2]
        k = np.array([sw / small.shape[1], sh / small.shape[0]])
        corners = np.asarray(quad.corners, dtype=np.float64)
        poly = np.round(corners * k).astype(np.int32)
        mask = np.zeros((sh, sw), np.uint8)
        cv2.fillPoly(mask, [poly], 1)
        area = float(mask.mean())
        out = mask == 0
        if out.mean() >= 0.03:
            outside = float(big[out].mean())
            dark = sub.max(axis=2) <= 6
            clipped = dark | (sub.min(axis=2) >= 249)  # 純黑或純白（相機壓暗、過曝）
            black = float(dark[out].mean())
            mid = out & ~clipped
            if mid.sum() >= 0.05 * out.sum():
                outside_mid = float(big[mid].mean())
        persp, tilt = _opposite_sides_angle(corners), _axis_tilt(corners)
    return ImageFeatures(flat, float(big.mean()), noise, quad.method, float(quad.confidence), area, outside, persp, outside_mid, tilt, black)


def _axis_tilt(corners) -> float:
    """四邊形各邊離水平或垂直最多偏幾度。"""
    import math

    c = [tuple(map(float, pt)) for pt in corners]
    worst = 0.0
    for a, b in zip(c, c[1:] + c[:1]):
        t = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0
        worst = max(worst, min(t, 90.0 - t))
    return worst


def _opposite_sides_angle(corners) -> float:
    """四邊形（左上、右上、右下、左下）兩組對邊的夾角，取大的（度）。對邊平行（長方形、平行四邊形）是 0。"""
    import math

    c = [tuple(map(float, pt)) for pt in corners]

    def ang(a, b) -> float:
        return math.atan2(b[1] - a[1], b[0] - a[0])

    def diff(a: float, b: float) -> float:
        d = abs(a - b) % math.pi
        return math.degrees(min(d, math.pi - d))

    top, bottom = ang(c[0], c[1]), ang(c[3], c[2])
    left, right = ang(c[0], c[3]), ang(c[1], c[2])
    return max(diff(top, bottom), diff(left, right))


def classify_image(f: ImageFeatures, camera: bool, c: dict) -> tuple[InputType, float, str]:
    """依影像內容判斷是截圖還是照片。回傳（類型、信心值、白話理由）。

    截圖（含軟體匯出、螢幕擷取、沒有雜訊的數位頁面）：整張圖就是頁面，不找四角、不做白平衡與去光照。
    照片（手機翻拍、掃描）：要找頁面四角並透視攤平；白紙類頁面另外做去陰影與白平衡才辨識。
    檔案格式（PNG 或 JPEG）不列入判斷：截圖常被聊天軟體轉成 JPEG。相機資訊只在內容分不出來時當旁證。
    依序：
      1. 有透視變形的紙張四邊 → 照片；除非四邊以外也是大片平塗、四邊形又只佔畫面一部分（截圖裡的色塊或圖片）。
         但四邊以外的平塗幾乎全是純黑、四邊形又是歪的（在暗處拍螢幕或投影）仍判照片。
      2. 大片平塗比例夠高 → 截圖（相機拍的影像不會有成片完全同色的區域）。
      3. 幾乎沒有雜訊、平塗像素又多 → 截圖（漸層多的畫面）。
      4. 其他 → 照片（整張圖就是頁面，或紙張邊緣沒拍到）。"""
    flat_big_min = float(c.get("classify_flat_big_screenshot", 0.25))
    flat_min = float(c.get("classify_flat_screenshot", 0.55))
    digital = f.noise < float(c.get("classify_noise_digital", 0.6))
    if f.quad_method in ("edge_fit", "contour") and f.quad_conf >= float(c.get("classify_quad_min_conf", 0.5)):
        if f.quad_outside_flat is not None and f.quad_outside_flat >= float(c.get("classify_outside_flat", 0.5)) and f.quad_area < float(c.get("classify_quad_max_area", 0.8)):
            # 四邊以外是一大片完全同色：多半是截圖裡的色塊或圖片。例外是在暗處拍螢幕、投影：相機把螢幕以外壓成純黑，
            # 也是一大片完全同色，但那不是數位畫面。分得出來的地方是：那片同色幾乎全是純黑、而四邊形不是和畫面軸對齊的長方形
            # （拍照很難拍得完全正，截圖裡的色塊、圖片則一定對齊）。純白不算這個例外：白底投影片上的圖片很常見。
            tilted = max(f.quad_tilt_deg, f.quad_persp_deg) > float(c.get("classify_quad_tilt_deg", 0.3))
            crushed = f.quad_outside_flat_mid is None and f.quad_outside_black >= float(c.get("classify_outside_black", 0.5))
            if tilted and crushed:
                conf = 0.85 if camera else 0.75
                return InputType.PHOTO, conf, (f"找到一個歪斜的四邊形（佔畫面 {f.quad_area:.0%}，邊的傾斜 {max(f.quad_tilt_deg, f.quad_persp_deg):.1f}°），"
                                               f"四邊以外幾乎全是純黑（{f.quad_outside_black:.0%}）：判為在暗處拍的螢幕或投影，不是截圖")
            return InputType.SCREENSHOT, 0.6, (f"找到一個像紙張的四邊形（佔畫面 {f.quad_area:.0%}），但四邊以外是大片平塗的數位畫面（{f.quad_outside_flat:.0%}），"
                                               "判為截圖裡的色塊或圖片，不是拍到的紙張")
        conf = 0.95 if camera else 0.9
        return InputType.PHOTO, conf, f"找到有透視變形的紙張四邊（信心值 {f.quad_conf:.2f}），判為照片"
    if f.flat_big >= flat_big_min:
        conf = 0.9 if f.flat_big >= 2 * flat_big_min else 0.75
        if camera:
            conf = 0.6
        return InputType.SCREENSHOT, conf, f"有大片完全同色的平塗區域（{f.flat_big:.0%}），判為截圖、軟體匯出的頁面或乾淨的掃描"
    if digital and f.flat >= flat_min:
        return InputType.SCREENSHOT, 0.6 if camera else 0.7, f"平塗像素多（{f.flat:.0%}）、幾乎沒有雜訊（σ {f.noise:.2f}），判為截圖或軟體匯出的頁面"
    if camera:
        return InputType.PHOTO, 0.8, "檔案帶有相機資訊，畫面也不是平塗的數位畫面，判為照片（整張圖就是頁面，或紙張邊緣沒拍到）"
    if not digital or f.flat < float(c.get("classify_flat_photo", 0.3)):
        return InputType.PHOTO, 0.7, (f"有感光雜訊（σ {f.noise:.2f}）或幾乎沒有平塗像素（{f.flat:.0%}），判為照片或掃描"
                                      "（沒有找到紙張四邊：整張圖就是頁面，或紙張邊緣沒拍到）")
    return InputType.PHOTO, 0.5, f"平塗像素 {f.flat:.0%}、雜訊 σ {f.noise:.2f}，介於截圖與照片之間，先當照片處理"
