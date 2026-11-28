"""② 頁面：收來源文件，交出頁面規格 PageSpec（四角、長寬比、比例尺、工作影像）。

步驟：
  s2.box        原生 PDF：選頁框（裁切框與媒體框的交集）、套用旋轉
  s2.corners    照片：recognition/page_geometry.find_page_quad 找頁面四角，連同狀態（ok、low_contrast、curved_edge、multiple_pages、
                cut_off、not_found）、各角誤差、其他候選（第二張紙、三條邊＋畫面邊）與彎曲外框寫進 PageSpec 的 quad_* 欄位；
                找不到就以整張圖為頁面（紙被畫面切掉、三條邊都量到時預選「三條邊＋畫面邊」）。截圖與掃描以整張圖為頁面。
                使用者拉過四角（修正單 corners，原圖像素）時一律用使用者的四角，不再自動找；紙是彎的照樣攤平（四角釘住不動）。
  s2.rectify    透視攤平，建立頁面座標系（頁寬為 1）。照片另試彎曲攤平（recognition/dewarp.py；設定 dewarp_photos）：
                判定彎曲、驗證比透視好才採用，逐像素對應圖存進 dewarp_map。找不到四角時採用攤平量出的外框四角（需確認）。
  s2.review     要不要請使用者確認四角：status 不是 ok、信心值低於 quad_review_confidence、任一角誤差超過
                quad_max_corner_err_frac × 對角線、或攤平建議確認外框（quad_needs_review、quad_review_reason）。
  s2.enhance    去陰影、白平衡，產生工作影像（只對照片模式、像白紙、又不是平塗數位畫面的頁面做；找不到紙張四邊又光照均勻的照片也不做；
                未增強的影像另存 color_image）；反光只標記（glare_frac），寫進備註提醒重拍
  s2.size       尺寸判斷：使用者輸入永遠優先，其次檔案內建，都沒有才依長寬比推論候選規格
  s2.working    產生工作影像，計算有效解析度
"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageOps

from ..artifacts import Correction, InputType, PageSpec, PhysicalSize, Point, Provenance, SourceDoc, by_user, exact, measured
from ..config import Config
from ..pipeline.store import Store
from ..tools import pdfium as pdf
from ..tools.units import to_pt

NAME = "s2"
VERSION = "6"


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _user_size(doc: SourceDoc, corrections: list[Correction]) -> Optional[PhysicalSize]:
    """使用者指定的尺寸：修正單優先於上傳時填的。"""
    for c in reversed(corrections):
        if c.field == "size" and c.new_value:
            return PhysicalSize(**c.new_value)
    return doc.intent.known_size


def pdf_matrix(box: tuple[float, float, float, float], rotation: int) -> tuple[list[float], float, float]:
    """PDF 使用者空間（pt，y 向上）→ 頁面相對座標（頁寬為 1，y 向下）。回傳（矩陣、顯示寬 pt、顯示高 pt）。"""
    l, b, r, t = box
    w, h = r - l, t - b
    rot = rotation % 360
    if rot == 0:
        dw, dh = w, h
        m = [1 / dw, 0, -l / dw, 0, -1 / dw, t / dw, 0, 0, 1]
    elif rot == 90:
        dw, dh = h, w
        m = [0, 1 / dw, -b / dw, 1 / dw, 0, -l / dw, 0, 0, 1]
    elif rot == 180:
        dw, dh = w, h
        m = [-1 / dw, 0, r / dw, 0, 1 / dw, -b / dw, 0, 0, 1]
    else:  # 270
        dw, dh = h, w
        m = [0, -1 / dw, t / dw, -1 / dw, 0, r / dw, 0, 0, 1]
    return m, dw, dh


def run(doc: SourceDoc, page_index: int, cfg: Config, corrections: list[Correction], store: Store) -> PageSpec:
    c = cfg.section("page")
    ref = doc.pages[page_index]
    user = _user_size(doc, corrections)
    notes: list[str] = []
    w_step = f"{NAME}.size@{VERSION}"

    if doc.media_type == "application/pdf":
        pdoc = pdf.open_document(str(store.blob_path(doc.source)))
        reader = pdf.PageReader(pdoc, page_index)
        try:
            boxes = reader.boxes()
            media = boxes["media"]
            crop = boxes.get("crop", media)
            box = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
            if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
                box = media
            box_used = "crop" if "crop" in boxes and tuple(crop) != tuple(media) else "media"
            rotation = reader.rotation()
            matrix, dw, dh = pdf_matrix(box, rotation)
            dpi = c["working_dpi"]
            if dw * dh * (dpi / 72) ** 2 > c["max_working_pixels"]:
                dpi = 72 * (c["max_working_pixels"] / (dw * dh)) ** 0.5
                notes.append(f"頁面很大，工作影像解析度降為 {dpi:.0f} dpi。")
            image = reader.render(dpi)
        finally:
            reader.close()
            pdoc.close()
        l, b, r, t = box
        corners = {0: [(l, t), (r, t), (r, b), (l, b)], 90: [(l, b), (l, t), (r, t), (r, b)], 180: [(r, b), (l, b), (l, t), (r, t)], 270: [(r, t), (r, b), (l, b), (l, t)]}[rotation % 360]
        if ref.input_type.value == InputType.SCANNED_PDF:
            # 掃描 PDF 的頁面尺寸只是掃描器設定，只當強參考
            file_width = measured(dw, 0.7, Provenance.INFERRED, w_step)
        else:
            file_width = exact(dw, w_step)
        aspect = dh / dw
    else:
        image, color, matrix, corners, aspect, quad_method, quad_conf, geo_notes, geo = _image_geometry(doc, ref, c, corrections, store)
        notes += geo_notes
        rotation, box_used = 0, None
        pw = image.size[0]
        size_candidates = _size_candidates(aspect, ref, image.size, cfg, c, _ASPECT_ERR.pop(id(image), None))
        top = size_candidates[0] if size_candidates else None
        if top and top["confidence"] >= c.get("size_auto_accept", 0.6):
            file_width = measured(top["width_pt"], top["confidence"], Provenance.INFERRED, w_step)
            notes.append(f"尺寸推論為「{top['name']}」（{top['reason']}）。如果不對，請輸入實際尺寸。")
        elif top and top.get("verdict") == "ambiguous" and top["confidence"] > 0.2:
            # 長寬比對上了規格家族，但同家族的大小分不出來（單張照片的常態）：先預填第一名，請使用者確認
            file_width = measured(top["width_pt"], top["confidence"], Provenance.INFERRED, w_step)
            others = "、".join(x["name"] for x in size_candidates[1:3] if x["confidence"] > 0.05)
            maybe = f"（也可能是 {others}）" if others else ""
            notes.append(f"長寬比符合「{top['name']}」，但單張影像量不出實際大小{maybe}。已先用「{top['name']}」，請確認或輸入實際尺寸。")
        elif ref.dpi_hint and quad_method in ("full_image", ""):
            file_width = measured(pw * 72 / ref.dpi_hint, 0.4, Provenance.INFERRED, w_step)
            notes.append(f"尺寸依檔案標記的 {ref.dpi_hint:.0f} dpi 推算，可能不準，建議輸入實際尺寸。")
        else:
            file_width = measured(pw * 72 / c["assumed_image_dpi"], 0.2, Provenance.INFERRED, w_step)
            if size_candidates:
                names = "、".join(f"{x['name']}（{x['confidence']:.0%}）" for x in size_candidates[:3])
                notes.append(f"單張影像量不出實際大小。依長寬比最可能是：{names}。請選一個或輸入實際尺寸。")
            else:
                notes.append("單張影像量不出實際大小，長寬比也不符合常見規格，請輸入實際尺寸。")

    if user is not None:
        uw, uh = to_pt(user.width, user.unit), to_pt(user.height, user.unit)
        if abs((uh / uw) / aspect - 1) > 0.02:
            notes.append(f"輸入的尺寸長寬比（{uh / uw:.3f}）與頁面（{aspect:.3f}）不符，已以寬度為準。")
        width = by_user(uw, w_step)
    else:
        width = file_width

    blob = store.put_blob(_png(image), "image/png", "png")
    extra = {}
    if doc.media_type != "application/pdf":
        extra = {
            "color_image": store.put_blob(_png(color), "image/png", "png") if color is not image else None,
            "size_candidates": size_candidates, "quad_method": quad_method, "quad_confidence": quad_conf,
            "enhanced": color is not image, **geo,
        }
    else:
        extra = {"quad_method": "pdf_box", "quad_confidence": 1.0}
    return PageSpec(
        page_index=page_index,
        source_corners=[Point(x=x, y=y) for x, y in corners],
        aspect_ratio=aspect, source_to_page=matrix, rotation_applied=rotation, box_used=box_used,
        working_image=blob, working_image_size=image.size,
        page_width_pt=width, effective_resolution=image.size[0] / width.value, notes=notes, **extra,
    )


# ---------- 影像輸入：s2.corners、s2.rectify、s2.enhance ----------


_ASPECT_ERR: dict[int, Optional[float]] = {}  # 工作影像 → 透視反推的長寬比誤差（給尺寸判斷用）


def _user_corners(corrections: list[Correction], size: Optional[tuple[int, int]] = None, notes: Optional[list[str]] = None):
    """最新一張四角修正單（原圖像素，左上、右上、右下、左下）。格式不對、四邊形自我交叉或面積太小的修正單略過（寫進備註），
    改用前一張；點的順序不是順時針時自動排成左上、右上、右下、左下。角落可以在畫面外（紙超出畫面）。"""
    import math

    import numpy as np

    for c in reversed(corrections):
        if c.field != "corners" or not c.new_value:
            continue
        try:
            try:
                pts = np.array([[float(v) for v in pt] for pt in c.new_value], dtype=np.float64)
            except (TypeError, ValueError):
                raise ValueError("要四個 [x, y] 點，依左上、右上、右下、左下")
            if pts.shape != (4, 2) or not np.all(np.isfinite(pts)):
                raise ValueError("要四個 [x, y] 點，依左上、右上、右下、左下")
            from ..recognition import page_geometry as pg

            if not _clockwise_convex(pts):  # 照順序不是順時針的凸四邊形：重新排序（順時針時保留使用者的起點，可用來轉正頁面）
                pts = pg._order_corners(pts)
            area = pg._poly_area(pts)
            if size is not None:
                w, h = size
                if area < 0.002 * w * h or not _clockwise_convex(pts) or np.abs(pts).max() > 4 * math.hypot(w, h):
                    raise ValueError("四邊形自我交叉、太小或超出畫面太遠")
            return [tuple(map(float, pt)) for pt in pts]
        except (TypeError, ValueError) as e:
            if notes is not None:
                notes.append(f"有一張四角修正單的格式不對（{e}），已略過。")
    return None


def _clockwise_convex(q) -> bool:
    """四點依序是影像座標（y 向下）裡順時針的凸四邊形。"""
    cross = [(q[(i + 1) % 4][0] - q[i][0]) * (q[(i + 2) % 4][1] - q[(i + 1) % 4][1]) - (q[(i + 1) % 4][1] - q[i][1]) * (q[(i + 2) % 4][0] - q[(i + 1) % 4][0]) for i in range(4)]
    return all(v > 0 for v in cross)


def _image_geometry(doc: SourceDoc, ref, c: dict, corrections: list[Correction], store: Store):
    """找頁面四角、攤平（透視；彎曲的紙另做攤平）、增強。照片才找四角；截圖與一般影像以整張圖為頁面。
    回傳（工作影像、未增強的影像、來源到頁面的矩陣、四角、長寬比、四角的來源、信心值、備註、要寫進 PageSpec 的頁面確認欄位）。

    照片：find_page_quad 找四角（status、alternatives、各角誤差、彎曲外框一併交出）→ rectify（開 dewarp_photos 時先試攤平，
    判定彎曲、驗證比透視好才採用，對應圖存進 dewarp_map）→ 去陰影（反光只標記、寫進備註）。
    只有「整張圖＋紙被畫面切掉（cut_off）」時預選 alternatives[0]（三條量到的紙邊＋畫面邊）；找不到（not_found）不預選。
    找不到四角、但攤平時由紙邊量出外框四角（至少三邊有量測支持）時，用那組四角，標成需要確認。
    使用者拉過四角（修正單 corners）時一律用使用者的四角（method "user"、信心值 1.0）。照片照樣試攤平：四角釘住不動，
    只在四角之內攤平，同樣要判定彎曲、驗證比透視好才採用（使用者只是按「確認」時，彎紙也不會因此變成只做透視）。"""
    import numpy as np

    raw = ImageOps.exif_transpose(Image.open(store.blob_path(doc.source))).convert("RGB")
    pw, ph = raw.size
    notes: list[str] = []
    aspect_err = None
    full = ([1 / pw, 0, 0, 0, 1 / pw, 0, 0, 0, 1], [(0, 0), (pw, 0), (pw, ph), (0, ph)], ph / pw)
    user = _user_corners(corrections, (pw, ph), notes)
    kind = ref.input_type.value
    geo: dict = {"source_size": (pw, ph), "quad_status": "not_photo" if kind != InputType.PHOTO else "ok"}
    if user is not None:
        geo["quad_status"] = "user"
    try:
        from ..recognition import page_geometry as pg
        from ..recognition.types import PageQuad
    except Exception:  # 模組還沒有或壞掉：以整張圖為頁面
        return raw, raw, *full, "full_image", 0.5, ["頁面幾何模組無法載入，以整張圖為頁面。"], _review(geo, True, "頁面幾何模組無法載入，請確認頁面四角。")
    arr = np.asarray(raw)
    diag = float(np.hypot(pw, ph))
    try:
        quad = None
        if user is not None:
            corners = np.array(user, dtype=np.float64)
            try:
                est, amethod, focal = pg.estimate_aspect(corners, (pw, ph), np.full(4, 2.0))
                a_user = float(est.value)
            except Exception:  # 透視反推失敗：退回邊長比
                est, amethod, focal, a_user = None, "side_ratio", None, _aspect_from_corners(corners)
            quad = PageQuad(corners=corners, confidence=1.0, method="user", aspect_ratio=a_user, aspect=est, aspect_method=amethod,
                            focal_px=focal, corner_error_px=np.zeros(4), status="user")
        elif kind == InputType.PHOTO:
            quad = pg.find_page_quad(arr, {"input_type": kind.value, "review_conf": float(c.get("quad_review_confidence", 0.6)),
                                           "max_corner_err_frac": float(c.get("quad_max_corner_err_frac", 0.01))})
        dres = None
        from_dres = False
        method, conf = "full_image", 1.0
        if quad is not None and user is None:
            geo.update(_quad_fields(quad, pw, ph))
            if quad.method == "full_image" and quad.status == "cut_off" and quad.alternatives:
                # 紙被畫面切掉、三條邊都量到：預選「三條邊＋畫面邊」的框當四角（仍需確認）
                alt0 = np.asarray(quad.alternatives[0], dtype=np.float64)
                est, amethod, focal = pg.estimate_aspect(alt0, (pw, ph), np.full(4, 0.01 * diag))
                quad = PageQuad(corners=alt0, confidence=float(quad.confidence), method="frame_edges", aspect_ratio=float(est.value), notes=list(quad.notes),
                                corner_error_px=np.full(4, 0.01 * diag), aspect=est, aspect_method=amethod, focal_px=focal, status="cut_off",
                                alternatives=list(quad.alternatives), alternative_scores=list(quad.alternative_scores))
                geo["quad_preselect"] = 0
            notes += list(getattr(quad, "notes", []))
        if quad is not None:
            # 攤平只給照片（使用者拉的四角也試：四角釘住不動）；find_page_quad 判定「整張圖就是頁面」（status ok 的整張圖，例如沒有雜訊的數位畫面）時不試
            whole_ok = quad.method == "full_image" and quad.status == "ok"
            params = {**c, "dewarp": bool(c.get("dewarp_photos", True)) and kind == InputType.PHOTO and not whole_ok}
            flat, H, dres = pg.rectify(arr, quad, params, return_dewarp=True)
            from_dres = dres is not None and dres.image is not None and flat is dres.image
            if from_dres and user is not None:
                corners_src = quad.corners  # 使用者的四角釘住不動（攤平只在四角之內）
                method, conf = "user", 1.0
            elif from_dres:
                corners_src = dres.corners if dres.corners is not None else (dres.pseudo_quad if dres.pseudo_quad is not None else quad.corners)
                if quad.method == "full_image":
                    method, conf = "outline", float(dres.confidence)
                    geo["quad_status"] = quad.status if quad.status != "ok" else "low_contrast"
                    notes.append("照片中找不到完整的頁面四角；已由紙邊量出外框的四角（至少三條邊有量測支持）先用，請確認四角。")
                else:
                    method, conf = quad.method, float(quad.confidence)
                    if np.max(np.linalg.norm(np.asarray(corners_src) - np.asarray(quad.corners), axis=1)) > 0.01 * diag:
                        geo.pop("quad_corner_error_px", None)  # 各角誤差是 find_page_quad 那組四角的，攤平量出的外框四角沒有逐角誤差
                        geo["quad_alternatives"] = [_pts(quad.corners)] + geo.get("quad_alternatives", [])
                        geo["quad_alternative_scores"] = [1.0] + geo.get("quad_alternative_scores", [])
                        geo["quad_alternative_labels"] = ["原本找到的四角"] + geo.get("quad_alternative_labels", [])
                        geo["quad_preselect"] = None  # 目前的四角是攤平時量出的外框，不是任何一組候選
            else:
                corners_src = quad.corners
                method, conf = quad.method, float(quad.confidence)
            if method == "full_image":
                image_arr = arr
                matrix, corners_l, aspect = full
                if user is None:
                    notes.append("照片中找不到清楚的頁面邊緣，以整張圖為頁面。可以手動拉出四個角。")
            else:
                image_arr = flat
                fw = flat.shape[1]
                Hn = np.diag([1 / fw, 1 / fw, 1.0]) @ np.asarray(H, dtype=np.float64)
                matrix = [float(v) for v in (Hn / Hn[2, 2]).ravel()]
                corners_l = [tuple(map(float, pt)) for pt in corners_src]
                aspect = flat.shape[0] / flat.shape[1]
                if getattr(quad, "aspect", None) is not None and method not in ("outline",):
                    aspect_err = float(quad.aspect.error)
                if method == "user":
                    notes.append("頁面四角使用你拉的位置，紙是彎的，已在這四角之內攤平。" if from_dres else "頁面四角使用你拉的位置，已透視攤平。")
                else:
                    notes.append(f"已找出頁面四角並透視攤平（{method}，信心值 {conf:.2f}）。")
            if dres is not None:
                geo.update(_dewarp_fields(dres, from_dres, store, geo, user_set=user is not None))
                if dres.applied or from_dres or dres.curved:
                    notes += list(dres.notes)
                elif any("UVDoc" in n and ("下載" in n or "無法取得" in n) for n in dres.notes):
                    notes += [n for n in dres.notes if "UVDoc" in n]
        else:
            image_arr = arr
            matrix, corners_l, aspect = full
        color = Image.fromarray(np.ascontiguousarray(image_arr))
        work = color
        if kind in (InputType.PHOTO, InputType.SCANNED_PDF) and c.get("enhance_photos", True) and _paper_like(image_arr, c):
            digital = _digital_content(image_arr, c)
            spread = _illumination_spread(image_arr) if kind == InputType.PHOTO and method == "full_image" else None
            if digital is not None:
                # 照片模式但畫面是平塗的數位內容（例如帶相機資訊的截圖、乾淨的掃描）：沒有光照不均可修，做了只會改掉原本的顏色
                notes.append(f"頁面是平塗的數位畫面（{digital}），不做去陰影與白平衡，顏色照原圖。")
            elif spread is not None and spread < float(c.get("enhance_uniform_max", 0.03)):
                # 判成照片、卻找不到紙張四邊，整頁的光照又幾乎完全均勻（底色亮度的落差不到 3%）：多半不是拍的照片，
                # 而是底色有細紋理的截圖（例如閱讀軟體的紙紋主題）或乾淨的掃描。沒有陰影可去；白平衡只會把米色、灰色的底改成白色
                notes.append(f"沒有找到紙張四邊，整頁的光照又很均勻（底色亮度落差 {spread:.1%}），不做去陰影與白平衡，顏色照原圖。")
            else:
                enhanced, info = pg.enhance(image_arr, c, return_info=True)
                work = Image.fromarray(np.ascontiguousarray(enhanced))
                geo["glare_frac"] = round(float(info.get("glare_frac", 0.0)), 5)
                notes.append("頁面像一般紙張，已去陰影與白平衡後再辨識；工作影像的顏色因此與原圖不同，未增強的原圖另外保留（color_image）。")
                notes += list(info.get("notes", []))
        _ASPECT_ERR[id(work)] = aspect_err
        if kind == InputType.PHOTO and user is None:
            _decide_review(geo, conf, diag, c, method, list(getattr(quad, "notes", []) or []) if quad is not None else [], from_dres)
        return work, color, matrix, corners_l, aspect, method, conf, notes, geo
    except Exception as e:  # 幾何處理失敗不中斷：退回整張圖
        geo = {"source_size": (pw, ph), "quad_status": "user" if user is not None else ("not_found" if kind == InputType.PHOTO else "not_photo")}
        return raw, raw, *full, "full_image", 0.3, [f"頁面幾何處理失敗（{e}），以整張圖為頁面。"], _review(geo, kind == InputType.PHOTO and user is None, "頁面幾何處理失敗，請確認頁面四角。")


def _pts(corners) -> list[Point]:
    return [Point(x=float(x), y=float(y)) for x, y in corners]


def _review(geo: dict, needs: bool, reason: str) -> dict:
    geo["quad_needs_review"] = bool(needs)
    geo["quad_review_reason"] = reason if needs else ""
    return geo


def _poly_iou(a, b) -> float:
    import cv2
    import numpy as np

    pa, pb = np.asarray(a, np.float32).reshape(-1, 1, 2), np.asarray(b, np.float32).reshape(-1, 1, 2)
    try:
        inter, _ = cv2.intersectConvexConvex(pa, pb)
    except cv2.error:
        return 0.0
    ua = abs(cv2.contourArea(pa)) + abs(cv2.contourArea(pb)) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _alt_label(alt, main, pw: int, ph: int, quad, index: int) -> str:
    """其他候選四角的白話短標籤（給介面列出讓使用者選）。"""
    import cv2
    import numpy as np

    a = np.asarray(alt, np.float64)
    tol = 0.01 * min(pw, ph)
    on = [(abs(a[k, 1]) <= tol and abs(a[(k + 1) % 4, 1]) <= tol) or (abs(a[k, 1] - ph) <= tol and abs(a[(k + 1) % 4, 1] - ph) <= tol)
          or (abs(a[k, 0]) <= tol and abs(a[(k + 1) % 4, 0]) <= tol) or (abs(a[k, 0] - pw) <= tol and abs(a[(k + 1) % 4, 0] - pw) <= tol) for k in range(4)]
    if quad.method == "full_image" and quad.status == "cut_off" and index == 0:
        return "三條邊＋畫面邊"
    if quad.method == "full_image":
        return "三條邊＋畫面邊" if sum(on) == 1 else "可能的紙張範圍"
    iou = _poly_iou(a, main)
    area_a = abs(cv2.contourArea(a.astype(np.float32)))
    area_m = abs(cv2.contourArea(np.asarray(main, np.float32)))
    # 先看和目前的四角的關係：比目前的框大很多（貼著畫面邊也一樣）多半是連旁邊的紙一起框
    if iou < 0.3:
        return "另一張紙"
    if area_a > 1.15 * area_m:
        return "較大的框（可能連旁邊的紙一起框）"
    if sum(on) == 1:
        return "三條邊＋畫面邊"
    if area_a < 0.85 * area_m:
        return "較小的框（可能是紙上的一塊）"
    return "另一種框法"


def _quad_fields(quad, pw: int, ph: int) -> dict:
    """find_page_quad 的結果 → PageSpec 的 quad_* 欄位（四角誤差、其他候選與標籤、彎曲外框）。"""
    import numpy as np

    out: dict = {"quad_status": quad.status or "ok"}
    if quad.corner_error_px is not None and quad.method != "full_image":
        out["quad_corner_error_px"] = [round(float(v), 2) for v in np.asarray(quad.corner_error_px).ravel()[:4]]
    alts = [np.asarray(a, np.float64) for a in (quad.alternatives or [])]
    scores = list(quad.alternative_scores or [])
    out["quad_alternatives"] = [_pts(a) for a in alts]
    out["quad_alternative_scores"] = [round(float(np.clip(scores[i] if i < len(scores) else 0.0, 0.0, 1.0)), 3) for i in range(len(alts))]
    out["quad_alternative_labels"] = [_alt_label(a, quad.corners, pw, ph, quad, i) for i, a in enumerate(alts)]
    if quad.boundary is not None and len(quad.boundary) >= 4:
        out["source_outline"] = _pts(np.asarray(quad.boundary, np.float64))
    return out


def _outline_from_sides(sides, per_side: int = 24) -> list:
    """四條外框曲線（上、右、下、左；上與下由左到右、左與右由上到下）→ 順時針一圈的點（左上 → 右上 → 右下 → 左下）。"""
    import numpy as np

    if not sides or len(sides) != 4:
        return []
    t, r, b, l = (np.asarray(s, np.float64) for s in sides)

    def sub(a):
        if len(a) <= per_side:
            return a
        idx = np.linspace(0, len(a) - 1, per_side).round().astype(int)
        return a[idx]
    ring = np.vstack([sub(t)[:-1], sub(r)[:-1], sub(b)[::-1][:-1], sub(l)[::-1][:-1]])
    return _pts(ring)


def _dewarp_fields(dres, used: bool, store: Store, geo: dict, user_set: bool = False) -> dict:
    """攤平結果 → PageSpec 的 dewarp_* 欄位（採用時存對應圖）；沒採用的外框候選放進其他候選。
    user_set：四角是使用者拉的（已經確認過，不再請使用者確認外框，也不列其他候選）。"""
    import numpy as np

    # 攤平建議確認外框，只在真的採用了它的結果（攤平、或它量出的四角）時才算；沒採用的外框只放進其他候選
    out: dict = {"dewarp_confidence": round(float(dres.confidence), 3), "dewarp_needs_review": bool(dres.needs_review) and used and not user_set}
    if dres.applied and dres.map_x is not None:
        out["dewarp_map"] = store.put_blob(dres.maps_npz(), "application/x-npz", "npz")
        out["dewarp_source"] = dres.source or "measured"
        out["dewarp_residual_px"] = None if dres.residual_px is None else round(float(dres.residual_px), 3)
        ring = _outline_from_sides(dres.boundary)
        if ring:
            out["source_outline"] = ring
    if user_set:
        return out
    alts = list(geo.get("quad_alternatives", []))
    scores = list(geo.get("quad_alternative_scores", []))
    labels = list(geo.get("quad_alternative_labels", []))
    if dres.pseudo_quad is not None and not used:
        alts.append(_pts(dres.pseudo_quad))
        scores.append(round(float(np.clip(dres.confidence, 0, 1)), 3))
        labels.append("由紙邊量出的外框")
    if dres.candidate_outline and len(dres.candidate_outline) == 4 and dres.corners is not None and not used:
        alts.append(_pts(dres.corners))
        scores.append(round(float(np.clip(np.mean(dres.support) if dres.support else 0.0, 0, 1)), 3))
        labels.append("模型推估的外框（未經量測）")
    out.update({"quad_alternatives": alts, "quad_alternative_scores": scores, "quad_alternative_labels": labels})
    return out


_STATUS_REASON = {
    "low_contrast": "系統對四角的位置沒有把握，四角可能不準",
    "curved_edge": "紙邊是彎的，四角與外框可能不準",
    "multiple_pages": "照片裡有不只一張紙，請確認要的是哪一張",
    "cut_off": "紙有一部分超出畫面，已先用量到的三條邊加畫面邊",
    "not_found": "找不到紙張的邊緣，目前以整張圖為頁面",
}

# low_contrast 是「信心值低或某角誤差大」的總稱：依 find_page_quad 備註裡實際扣分的原因，換成白話（依序取第一個對上的）
_LOW_REASONS = [
    ("頁面與背景對比低", "紙和背景的顏色太接近，紙邊不夠清楚"),
    ("部分邊緣被遮住", "有一段紙邊被遮住或看不清楚"),
    ("某條邊兩側顏色幾乎相同", "有一條邊可能是紙上的線，不是紙邊"),
    ("某條邊外側緊貼著還有字", "有一條邊外面還有字，紙可能比框到的大"),
    ("相鄰兩邊越過角落後仍有邊緣", "框到的可能是紙上的色塊或圖框"),
    ("影像模糊", "照片有點模糊，紙邊不夠清楚"),
    ("同一張紙的其他框法", "同一張紙有幾種差不多的框法，四角可能差幾個像素"),
]
_NEIGHBOR_LABELS = ("另一張紙", "較大的框（可能連旁邊的紙一起框）")


def _decide_review(geo: dict, conf: float, diag: float, c: dict, method: str, quad_notes: Optional[list] = None, dewarped: bool = False) -> None:
    """要不要請使用者確認四角：status 不是 ok、信心值低於 quad_review_confidence、任一角誤差超過 quad_max_corner_err_frac × 對角線、
    或攤平建議確認外框。理由寫成白話一句，依實際的原因寫（旁邊有分數接近的另一張紙、紙邊對比低、紙邊彎曲……），
    不寫信心值數字（紙邊彎曲時信心值本來就很低，那不代表四角錯）。"""
    reasons = []
    status = geo.get("quad_status", "ok")
    labels = list(geo.get("quad_alternative_labels") or [])
    scores = list(geo.get("quad_alternative_scores") or [])
    neighbor = any(lb in _NEIGHBOR_LABELS and (scores[i] if i < len(scores) else 0.0) >= 0.3 for i, lb in enumerate(labels))
    if method == "outline":
        reasons.append("照片中找不到完整的頁面四角，四角是由紙邊量出的外框先填上的")
    elif status == "cut_off" and method != "frame_edges":
        reasons.append("紙的一角超出畫面，那個角是由紙邊延長推出來的")
    elif status == "low_contrast":
        if neighbor:
            reasons.append("旁邊還有一張紙（或框到兩張紙），請確認要的是哪一張")
        joined = "；".join(quad_notes or [])
        reasons.append(next((r for key, r in _LOW_REASONS if key in joined), _STATUS_REASON["low_contrast"]))
    elif status == "curved_edge":
        reasons.append("紙是彎的，已經攤平；外框是量出來的，請確認" if dewarped else _STATUS_REASON["curved_edge"])
    elif status not in ("ok", "user", "not_photo"):
        reasons.append(_STATUS_REASON.get(status, "四角可能不準"))
    if not reasons and conf < float(c.get("quad_review_confidence", 0.6)) and method != "full_image":
        reasons.append("系統對四角的位置沒有把握")
    errs = geo.get("quad_corner_error_px") or []
    lim = float(c.get("quad_max_corner_err_frac", 0.01)) * diag
    if errs and max(errs) > lim:
        # 紙邊彎曲、紙超出畫面時，各角誤差是刻意放大的保守估計，理由已經寫了原因，不再報像素數（免得嚇人又不準）
        if status in ("ok", "low_contrast") and method not in ("outline", "frame_edges"):
            reasons.append(f"有一個角的位置可能差到約 {max(errs):.0f} 像素")
        elif not reasons:
            reasons.append("有一個角的位置可能不準")
    if geo.get("dewarp_needs_review"):
        reasons.append("彎曲紙張攤平的外框有推估的部分")
    # 同樣的理由只留一個，最多兩個串成一句
    seen, uniq = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    _review(geo, bool(uniq), ("；".join(uniq[:2]) + "：請拖曳四角確認。") if uniq else "")


def _paper_like(arr, c: dict) -> bool:
    """頁面是不是「白紙上印字」：最常見的顏色夠亮、幾乎沒有彩度。只有這種頁面才做白平衡與去陰影；
    彩色海報、深色投影片做了反而會破壞顏色與對比（白字壓在彩色底上會變得看不清楚）。"""
    import numpy as np

    small = arr[:: max(1, arr.shape[0] // 200), :: max(1, arr.shape[1] // 200)].reshape(-1, 3).astype(np.int32)
    q = (small // 16) * 16 + 8
    keys, counts = np.unique(q, axis=0, return_counts=True)
    dominant = keys[counts.argmax()]
    share = counts.max() / counts.sum()
    chroma = int(dominant.max() - dominant.min())
    return share >= c.get("paper_min_share", 0.25) and dominant.mean() >= c.get("paper_min_brightness", 150) and chroma <= c.get("paper_max_chroma", 40)


def _illumination_spread(arr) -> float:
    """頁面底色的亮度落差：縮圖上用形態學閉運算把字抹掉、只留底色，再取（第 95 百分位 − 第 5 百分位）÷ 中位數。
    拍照時光線、暗角一定讓底色有明暗變化（合成的手機照片實測 0.2 以上）；截圖與乾淨的掃描幾乎是 0。"""
    import cv2
    import numpy as np

    h, w = arr.shape[:2]
    small = cv2.resize(np.ascontiguousarray(arr[..., :3]), (256, max(8, round(256 * h / max(w, 1)))), interpolation=cv2.INTER_AREA).astype(np.float32)
    luma = small @ np.array([0.299, 0.587, 0.114], np.float32)
    bg = cv2.morphologyEx(luma, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    bg = cv2.GaussianBlur(bg, (0, 0), 8)
    p5, p50, p95 = np.percentile(bg, [5, 50, 95])
    return float((p95 - p5) / max(p50, 1.0))


def _digital_content(arr, c: dict) -> Optional[str]:
    """攤平後的頁面是不是平塗的數位畫面：有成片完全同色的區域（9×9 鄰域三個通道完全相同）。
    相機拍的影像到處都有感光雜訊與光照漸變，攤平時又重新取樣，不會有這種區域；截圖、軟體匯出的頁面、乾淨的掃描才有。
    是就回傳白話的量測值，否則回傳 None。①已依內容判斷類型（s1_input.classify_image），這裡是第二道防線：
    ①判成照片（例如檔案帶有相機資訊）而畫面其實是數位內容時，不要做白平衡與去光照而改掉原本的顏色。"""
    import numpy as np

    from .s1_input import _flat_map

    step = max(1, int(np.ceil(np.sqrt(arr.shape[0] * arr.shape[1] / 16e6))))
    sub = np.ascontiguousarray(arr[::step, ::step, :3])
    big = float(_flat_map(sub, int(c.get("enhance_flat_window", 9))).mean())
    if big >= float(c.get("enhance_max_flat", 0.25)):
        return f"成片完全同色的像素佔 {big:.0%}"
    return None


def _aspect_from_corners(corners) -> float:
    import math as _m

    top = _m.dist(corners[0], corners[1])
    bottom = _m.dist(corners[3], corners[2])
    left = _m.dist(corners[0], corners[3])
    right = _m.dist(corners[1], corners[2])
    return ((left + right) / 2) / max((top + bottom) / 2, 1e-6)


def _size_candidates(aspect: float, ref, pixel_size, cfg: Config, c: dict, aspect_err: Optional[float] = None) -> list[dict]:
    """依長寬比（與可用的尺度線索）列出尺寸候選，並依 judge() 判定：clear 可自動採用；
    ambiguous 要請使用者在前幾名中選；nonstandard 表示不是常見規格。dpi 只對掃描檔有意義，照片與截圖不傳。"""
    try:
        from ..recognition import page_size
    except Exception:
        return []
    sizes = {k: tuple(v) for k, v in cfg.section("page_sizes").items()}
    hints: dict = {"page_px": tuple(pixel_size)}
    if aspect_err:
        hints["aspect_error"] = aspect_err
    if ref.input_type.value == InputType.SCANNED_PDF and ref.dpi_hint:
        hints["dpi"] = ref.dpi_hint
    try:
        cands = page_size.infer_size(aspect, sizes, hints)
        verdict = page_size.judge(cands)
    except Exception:
        return []
    out = [{"name": x.name, "width_pt": x.width_pt, "height_pt": x.height_pt, "confidence": round(x.confidence, 3), "reason": x.reason, "family": getattr(x, "family", "")} for x in cands[:5]]
    if out:
        out[0]["verdict"] = verdict.get("verdict")
        if verdict.get("verdict") != "clear":
            out[0]["confidence"] = min(out[0]["confidence"], 0.5)  # 不能自動採用：請使用者選
    return out

def geometry_key(spec: PageSpec) -> str:
    """③只依賴頁面幾何與工作影像，不依賴比例尺：改尺寸不必重跑辨識。"""
    return "|".join([spec.working_image.sha256, repr(spec.source_to_page), repr(spec.aspect_ratio), repr(spec.rotation_applied)])
