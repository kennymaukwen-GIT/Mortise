"""描圖向量化：把已經變成像素的向量圖（標誌、圖示、插畫、圖表）重新描成乾淨、好編輯的向量路徑。

對外介面（名稱固定）：
  vectorize(image, mask=None, params=None) -> VectorResult
  render(result, scale=1.0) -> RGBA             自家的點陣化，填色規則與 SVG、PDF 一致，用來自我驗證
  to_vector_paths(result, origin_xy, units_per_px) -> list[VectorPath]   像素座標換成頁面相對座標

流程（各步驟的細節在 vector_segment、vector_fit、vector_stroke、vector_raster）：
  1. 分割：量雜訊 → 平滑像素連通成核心 → 純色／線性漸層／放射漸層模型 → 邊緣像素解釋成兩色混合（覆蓋率）
     → 解釋不了的細線與小細節另立區域 → 相鄰同色合併。
  2. 分層：洞裡的東西在上面（洞補起來）、細線在上面、看得到的邊界能擬合出完整的基本幾何就補全（被遮住的在下面）；
     其餘相鄰區域大的在下，下層在上層底下多墊一點，重畫時才不會有接縫。補不出來的就是互相挖洞的拼圖。
  3. 描邊：每個區域的覆蓋率場取 0.5 等值線（次像素），先試基本幾何，再試筆畫（中心線加線寬），最後才是
     直線、圓弧、三次貝茲的一般擬合。
  4. 驗證：用 render 畫回來與原圖比色差（ΔE）與覆蓋範圍（IoU）；成塊的誤差表示漏了東西，就放細門檻重描，
     直到達標或到上限。照片之類不適合的內容回報 accepted=False 並說明原因。
  0. 早期判斷（在 1 之前）：取樣幾十個方塊，平滑像素太少、或平坦處平滑後仍有成片的明暗起伏，就直接回報不適合
     （大張照片原本要好幾秒到一分鐘才會被判定不適合；見 vector_segment.photo_precheck）。
     原圖與重畫兩邊都有邊緣的地方不算成塊誤差（模糊、縮放過的原圖邊緣比向量寬）；有遮罩時，路徑裁在遮罩內。
整個過程不使用大型 AI，也不用隨機；同樣的輸入一定得到同樣的輸出。所有數值都是量出來的。
"""

from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from . import vector_fit as vf
from . import vector_raster as vr
from .types import RGB, TracedGradient, TracedPath, TracedPrimitive, VectorResult
from .vector_segment import RegionModel, Segmentation, photo_precheck, segment

# 預設參數。設定檔 [recognize] 裡以 vector_ 開頭的值會蓋過這裡（去掉前綴後同名）。
DEFAULTS: dict = {
    # ---- 分割
    "denoise_lap": 3.0,  # Laplacian 的中位數超過此值（色階）就先做保邊平滑再分割
    "smooth_lap": 4.0,  # 二階差分小於此值（色階）的像素算平滑；雜訊大時改用 smooth_lap_k × 量到的雜訊
    "smooth_lap_k": 5.0,
    "smooth_grad": 14.0,  # 梯度小於此值（色階／像素）才算平滑；再陡就當成邊緣
    "min_core_px": 8,  # 核心至少幾個像素
    "min_core_fraction": 0.35,  # 平滑像素佔比低於此值，判為照片或材質
    "max_regions": 400,  # 區域數上限（另加每百萬像素 max_regions_per_mpx 個），超過判為照片
    "max_regions_per_mpx": 600,
    "flat_tol": 3.0,  # 純色區內顏色的容許偏差（色階）
    "flat_fraction": 0.92,  # 核心內有此比例的像素在容許偏差內，才算純色
    "gradients": True,  # 偵測線性與放射漸層
    "gradient_tol": 5.0,  # 漸層擬合的容許殘差（色階，第 95 百分位）
    "gradient_stop_tol": 2.0,  # 色標簡化的容許誤差（色階）
    "band_de": 5.0,  # 漸層擬合失敗時，分群切色帶的色差門檻（ΔE）
    "band_max": 8,
    "explain_tol": 9.0,  # 邊緣像素離「兩色連線」多遠以內算混色（色階）
    "explain_slack": 0.1,  # 另外容許兩色距離的這個比例（混色不完全線性、JPEG 的邊緣振鈴）
    "explain_radii": [2, 4],  # 往八個方向找鄰近核心的距離（像素）
    "chroma_weight": 0.4,  # 量得到雜訊時，混色判斷裡色度方向的權重（JPEG 的色度縮小取樣只抹寬色度，亮度仍準）
    "chroma_weight_sigma": 1.0,  # 雜訊（每通道標準差，色階）至少這麼大才降低色度權重
    "chroma_subsampled_below": 0.7,  # 色度邊緣的銳利程度不到亮度的這個比例，判定色度被縮小取樣過（JPEG 4:2:0）
    "overshoot": 0.2,  # 量得到雜訊時，容許邊緣像素超出兩色連線兩端的比例（壓縮的過衝）
    "overshoot_clean": 0.12,  # 沒量到雜訊時容許的過衝比例（雙三次放大的過衝約一成）
    "ring_fraction": 0.22,  # 量得到雜訊時，貼著邊緣、與相鄰區域色差在「邊緣兩側色差 × 此值」以內的小塊當成振鈴
    "min_feature_px": 2,  # 細節至少幾個像素
    "feature_contrast": 24.0,  # 細節與底色的最小色差（色階）
    "feature_colors": 6,  # 一塊細節最多拆成幾種顏色（圖表的軸、格線、資料線常常連成一塊）
    "max_features": 600,
    "max_features_per_mpx": 1500,
    "merge_de": 2.5,  # 相鄰純色區域的色差小於此值（ΔE）就合併
    "merge_de_thin": 6.0,  # 其中一方是細節時的合併門檻（細線顏色量不準，放寬）
    # ---- 擬合
    "fit_tol_px": 0.25,  # 貝茲與圓弧的容許誤差（像素）；會乘上雜訊係數
    "line_tol_px": 0.25,  # 直線的容許誤差
    "prim_tol_px": 0.3,  # 基本幾何的容許誤差
    "snap_tol_px": 0.3,  # 扶正（水平、垂直）與規則化容許移動的距離，不超過量測誤差
    "min_line_px": 6.0,  # 直線段的最短長度
    "corner_deg": 28.0,  # 轉角超過此角度才是角點
    "min_region_px": 3,  # 小於此面積的區域不輸出
    # ---- 分層
    "layered": True,  # 分層輸出（關掉則全部是互相挖洞的拼圖）
    "trap_px": 1.0,  # 下層在上層底下多墊的寬度（像素）
    "close_px": 4.0,  # 被細線切開的同一個形狀，縫寬在此值兩倍以內就接回去
    "see_through_de": 1.5,  # 洞裡的顏色與外面相同（ΔE 在此值內，雜訊大時放寬成 2σ）就當成真的洞；近白的形狀（250）與白底仍分得開
    "background_border_fraction": 0.5,  # 沒給遮罩時：佔影像邊框此比例以上的純色區域當成底色（透明）
    "completion": True,  # 被遮住的基本幾何補全
    "completion_max_tries": 60,
    # ---- 筆畫
    "strokes": True,
    "stroke_max_width_px": 8.0,  # 寬度在此值以內、而且寬度一致的細長區域還原成筆畫
    "stroke_width_spread": 0.15,  # 寬度的相對變動在此值以內才算等寬
    "stroke_min_aspect": 2.5,  # 長度至少是寬度的幾倍
    "stroke_min_iou": 0.8,  # 筆畫重畫後與原區域的重疊率至少要有這麼多，否則退回填色輪廓
    # ---- 驗證
    "accept_delta_e": 4.0,  # 重畫的平均色差（ΔE）在此值以內才接受；雜訊大時自動放寬
    "accept_p95": 22.0,  # 色差第 95 百分位的上限
    "accept_iou": 0.9,  # 覆蓋範圍的交集 ÷ 聯集下限
    "accept_blob_fraction": 0.01,  # 成塊的誤差（漏掉的東西）面積佔比上限
    "blob_delta_e": 14.0,  # 色差超過此值的像素才算進成塊誤差
    "max_refine_rounds": 2,  # 誤差成塊時最多放細重描幾輪
    "max_paths": 1500,  # 路徑數上限，超過視為不適合向量化
    "refine_max_blob_fraction": 0.04,  # 成塊誤差超過此比例就不再放細重描（補不回來；評測裡需要重描的第一輪最多 0.014）
    "max_seconds": 30.0,  # 描圖的時間上限（秒）＝此值＋下一項 × 百萬像素；超過就回報不適合（安全網；0 表示不限）
    "max_seconds_per_mpx": 10.0,
    "max_megapixels": 16.0,  # 影像大於此值先縮小再描（座標會換算回原尺寸）
    # ---- 早期判斷：明顯像照片或材質的輸入，取樣幾十個方塊就回傳不適合（見 vector_segment.photo_precheck）
    "photo_check": True,
    "photo_check_side": 560,  # 第二個尺度：影像（面積平均）縮到最長邊這麼多像素再取樣；同一張照片在不同解析度下才量到一樣的東西（0 表示不看）
    "photo_check_texture_de_small": 3.5,  # 第二個尺度的殘差門檻（ΔE）：量到插畫最多 3.35（放大四倍的雜訊插畫）、風景照 ≥ 4.38
    "photo_check_tile_px": 40,  # 取樣方塊的邊長（像素）
    "photo_check_tiles": 64,  # 最多取樣幾塊
    "photo_check_min_tiles": 8,  # 平坦方塊至少要有幾塊才下判斷（小圖、全是線條的圖表不判斷，交給完整流程）
    "photo_check_min_share": 0.3,  # 平坦方塊至少要佔取樣的這個比例
    "photo_check_smooth": 0.15,  # 平滑像素不到此比例 → 像照片（完整流程的門檻是 min_core_fraction，這裡只擋明顯的）
    "photo_check_texture_de": 3.0,  # 平坦方塊平滑後對平面的殘差（ΔE），各塊中位數超過此值 → 像照片（量到：插畫含放大三倍的雜訊圖 ≤ 2.7、照片 ≥ 3.5）
}

# 每一輪「放細」時覆蓋的參數（偵測更弱的邊、更小的區域、更低的對比）
_REFINE_LEVELS: list[dict] = [
    {},
    {"smooth_lap": 2.5, "min_core_px": 5, "feature_contrast": 14.0, "explain_tol": 6.0, "merge_de": 1.5, "band_de": 3.0, "flat_tol": 2.0},
    {"smooth_lap": 1.8, "min_core_px": 3, "feature_contrast": 9.0, "explain_tol": 4.5, "merge_de": 1.0, "band_de": 2.0, "flat_tol": 1.5, "min_feature_px": 1},
]


def _params(params: Optional[dict]) -> dict:
    P = dict(DEFAULTS)
    for k, v in (params or {}).items():
        key = k[7:] if k.startswith("vector_") else k
        if key in P:
            P[key] = v
    return P


def _rgb(c) -> RGB:
    return (int(np.clip(round(float(c[0])), 0, 255)), int(np.clip(round(float(c[1])), 0, 255)), int(np.clip(round(float(c[2])), 0, 255)))


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.ascontiguousarray(img, dtype=np.float32) / 255.0, cv2.COLOR_RGB2Lab)


# ---------------------------------------------------------------- 對外介面


def render(result: VectorResult, scale: float = 1.0) -> np.ndarray:
    """把描圖結果畫回 H×W×4 的 RGBA（uint8）。覆蓋率用精確面積，填色與筆畫的規則與 SVG、PDF 一致。"""
    return vr.render_paths(result.paths, result.size, scale)


def to_vector_paths(result: VectorResult, origin_xy: tuple[float, float], units_per_px: float) -> list:
    """像素座標換成頁面相對座標：頁面座標 = origin_xy + 像素座標 × units_per_px。回傳 artifacts.VectorPath 的清單（由下到上）。"""
    from ..artifacts import Color, Gradient, GradientStop, VectorPath, VectorPrimitive

    ox, oy = float(origin_xy[0]), float(origin_xy[1])
    s = float(units_per_px)

    def pt(x, y):
        return ox + float(x) * s, oy + float(y) * s

    def col(c):
        return Color(r=int(c[0]), g=int(c[1]), b=int(c[2]))

    out = []
    for p in result.paths:
        segs: list[list[float]] = []
        for seg in p.path:
            k = int(seg[0])
            if k == 3:
                segs.append([3])
            else:
                vals: list[float] = [k]
                for i in range(1, len(seg), 2):
                    vals.extend(pt(seg[i], seg[i + 1]))
                segs.append(vals)
        grad = None
        if p.gradient is not None:
            g = p.gradient
            if g.kind == "radial":
                coords = [*pt(g.coords[0], g.coords[1]), float(g.coords[2]) * s]
            else:
                coords = [*pt(g.coords[0], g.coords[1]), *pt(g.coords[2], g.coords[3])]
            grad = Gradient(kind=g.kind, coords=coords, stops=[GradientStop(offset=float(o), color=col(c)) for o, c in g.stops])
        prim = None
        if p.primitive is not None:
            q = p.primitive
            cx, cy = pt(q.cx, q.cy)
            prim = VectorPrimitive(kind=q.kind, cx=cx, cy=cy, w=q.w * s, h=q.h * s, angle_deg=q.angle_deg, corner_radius=q.corner_radius * s, points=[list(pt(x, y)) for x, y in q.points])
        out.append(VectorPath(
            path=segs, fill=col(p.fill) if p.fill is not None else None, fill_rule=p.fill_rule, gradient=grad,
            stroke=col(p.stroke) if p.stroke is not None else None, stroke_width=(p.stroke_width_px * s) if p.stroke_width_px else None,
            stroke_cap=p.stroke_cap, stroke_join=p.stroke_join, primitive=prim, fit_error=(p.fit_error_px * s) if p.fit_error_px is not None else None,
        ))
    return out


def vectorize(image: np.ndarray, mask: Optional[np.ndarray] = None, params: Optional[dict] = None) -> VectorResult:
    """把點陣圖描成向量路徑。

    image：H×W×3 的 uint8 RGB。mask：H×W 的 bool，True 表示屬於這個圖形；None 表示整張，
    這時會自己判斷底色（佔滿邊框的純色）並把它當成透明。回傳的路徑由下到上，座標是像素。
    """
    t_start = time.perf_counter()
    P = _params(params)
    raw = np.asarray(image)[..., :3]
    if raw.dtype != np.uint8:  # 浮點數輸入先四捨五入並限制在 0 到 255（直接轉型會繞回去：256 變成 0）
        raw = np.clip(np.rint(raw), 0, 255)
    img = np.ascontiguousarray(raw, dtype=np.uint8)
    H, W = img.shape[:2]
    msk = None if mask is None else np.asarray(mask).astype(bool)
    if H < 2 or W < 2:
        return _empty((W, H), "影像太小，無法描圖。")
    # 太大的影像先縮小（面積平均），描完再把座標換算回來
    scale = 1.0
    if H * W > float(P["max_megapixels"]) * 1e6:
        scale = math.sqrt(float(P["max_megapixels"]) * 1e6 / (H * W))
        w2, h2 = max(2, int(round(W * scale))), max(2, int(round(H * scale)))
        work = cv2.resize(img, (w2, h2), interpolation=cv2.INTER_AREA)
        wmask = None if msk is None else cv2.resize(msk.astype(np.uint8), (w2, h2), interpolation=cv2.INTER_NEAREST).astype(bool)
    else:
        work, wmask = img, msk
    if P["photo_check"]:
        why, pre = photo_precheck(work, wmask, P)
        if why is not None:
            pre["seconds"] = round(time.perf_counter() - t_start, 4)
            return _empty((W, H), why, pre)
    best: Optional[VectorResult] = None
    rounds = 0
    history = []
    # 時間上限（安全網）：完整流程擋不下、又被切成幾百塊的照片，擬合與重畫可能要好幾分鐘。超過就回報不適合。
    # 這是唯一和機器快慢有關的判斷；正常的插畫（評測每張最多約 2 秒）遠低於上限。
    budget = float(P["max_seconds"]) + float(P["max_seconds_per_mpx"]) * H * W / 1e6 if float(P["max_seconds"]) > 0 else 0.0
    deadline = t_start + budget if budget > 0 else None
    for level in range(1 + int(P["max_refine_rounds"])):
        Pl = dict(P)
        Pl.update(_REFINE_LEVELS[min(level, len(_REFINE_LEVELS) - 1)])
        Pl["_deadline"] = deadline
        res = _trace_once(work, wmask, Pl)
        rounds += 1
        history.append({"level": level, "render_error": round(res.render_error, 3), "paths": len(res.paths), "blob_fraction": res.stats.get("blob_fraction"), "accepted": res.accepted})
        if best is None or _better(res, best):
            best = res
        if res.accepted or res.stats.get("rejected"):
            break
        blob = res.stats.get("blob_fraction") or 0.0
        if blob <= 0.0:
            break  # 誤差不是成塊的（沒有漏掉的東西），放細也不會更好
        if blob > float(P["refine_max_blob_fraction"]):
            # 成塊的誤差遠超過上限（照片的明暗、大片漏掉的東西）：放細門檻只會切出更多碎塊，補不回來（評測裡需要重描的最多 1.4%）
            res.notes.append(f"成塊對不上的面積（{blob:.1%}）遠超過上限，放細門檻也補不回來，不再重描。")
            break
        if deadline is not None and time.perf_counter() > deadline:
            break
    assert best is not None
    best.stats["rounds"] = rounds
    best.stats["history"] = history
    if rounds > 1:
        best.notes.append(f"第一次描完誤差偏高，放細門檻重描了 {rounds - 1} 輪。")
    if scale != 1.0:
        _rescale(best, 1.0 / scale, (W, H))
        best.notes.append(f"影像超過 {P['max_megapixels']:g} 百萬像素，先縮小到 {scale:.2f} 倍再描。")
    best.stats["seconds"] = round(time.perf_counter() - t_start, 4)
    return best


def _better(a: VectorResult, b: VectorResult) -> bool:
    if a.accepted != b.accepted:
        return a.accepted
    if bool(a.stats.get("rejected")) != bool(b.stats.get("rejected")):
        return not a.stats.get("rejected")
    return a.render_error < b.render_error * 0.9


def _empty(size: tuple[int, int], why: str, stats: Optional[dict] = None) -> VectorResult:
    st = dict(stats or {})
    st["rejected"] = True
    return VectorResult(paths=[], size=size, background=None, render_error=float("inf"), error_p95=float("inf"), iou=0.0, node_count=0, accepted=False, confidence=0.0, notes=[why], stats=st)


def _rescale(res: VectorResult, k: float, size: tuple[int, int]) -> None:
    for p in res.paths:
        p.path = [[seg[0]] + [float(v) * k for v in seg[1:]] for seg in p.path]
        if p.stroke_width_px:
            p.stroke_width_px *= k
        if p.fit_error_px is not None:
            p.fit_error_px *= k
        if p.gradient is not None:
            p.gradient.coords = [float(v) * k for v in p.gradient.coords]
        if p.primitive is not None:
            q = p.primitive
            q.cx, q.cy, q.w, q.h, q.corner_radius = q.cx * k, q.cy * k, q.w * k, q.h * k, q.corner_radius * k
            q.points = [[x * k, y * k] for x, y in q.points]
    res.size = size


# ---------------------------------------------------------------- 一輪描圖


@dataclass
class _Reg:
    rid: int
    model: RegionModel
    contours: list[vf.Contour] = field(default_factory=list)  # 自己的次像素輪廓（整張圖的座標）
    neighbors: dict[int, int] = field(default_factory=dict)  # 相鄰區域 → 共用邊界的點數（-2 是影像邊框）
    holes: list[tuple[np.ndarray, set[int]]] = field(default_factory=list)  # （洞的遮罩位置、洞裡的區域）
    completion: Optional[tuple[vf.PrimFit, set[int]]] = None  # 補全用的基本幾何與遮住它的區域
    prim_full: Optional[vf.PrimFit] = None
    is_bg: bool = False


def _own_field(seg: Segmentation, rid: int, pad: int = 4) -> tuple[np.ndarray, int, int]:
    H, W = seg.labels.shape
    x0, y0, x1, y1 = seg.regions[rid].bbox
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    lab = seg.labels[Y0:Y1, X0:X1]
    sec = seg.second[Y0:Y1, X0:X1]
    al = seg.alpha[Y0:Y1, X0:X1]
    c = np.where(lab == rid, al, np.where(sec == rid, 1.0 - al, 0.0)).astype(np.float32)
    if seg.clip is not None:
        c = c * seg.clip[Y0:Y1, X0:X1]
    return c, X0, Y0


def _trace_region(c: np.ndarray, X0: int, Y0: int, seg: Segmentation, k: int, sat_eps: float) -> list[vf.Contour]:
    """覆蓋率場的次像素輪廓（整張圖的座標），每個點附上外側是哪個區域（-2 是影像外）。
    三個區域交會的像素混了三種顏色，兩色模型在那裡量不準，所以外側標籤換手處前後的點會被拿掉。"""
    H, W = seg.labels.shape
    out = []
    for ct in vf.trace_coverage(c, sat_eps):
        ct.points = ct.points + np.array([X0, Y0], dtype=np.float64)
        ct.inside = ct.inside + np.array([Y0, X0])
        ct.outside = ct.outside + np.array([Y0, X0])
        oy, ox = ct.outside[:, 0], ct.outside[:, 1]
        inb = (oy >= 0) & (oy < H) & (ox >= 0) & (ox < W)
        lab_out = np.full(len(oy), -2, dtype=np.int64)
        lab_out[inb] = seg.labels[oy[inb], ox[inb]]
        same = lab_out == k
        if same.any():
            lab_out[same] = seg.second[np.clip(oy, 0, H - 1), np.clip(ox, 0, W - 1)][same]
        if len(lab_out) >= 24:
            chg = lab_out != np.roll(lab_out, 1)
            if chg.any() and not chg.all():
                drop = chg | np.roll(chg, -1) | np.roll(chg, 1) | np.roll(chg, -2)
                if (~drop).sum() >= 12:
                    keep = ~drop
                    ct.points, ct.inside, ct.outside, lab_out = ct.points[keep], ct.inside[keep], ct.outside[keep], lab_out[keep]
        ct.out_label = lab_out  # type: ignore[attr-defined]
        out.append(ct)
    return out


def _relabel(seg: Segmentation, src: int, dst: int) -> None:
    """把區域 src 併入 dst（標籤圖直接改寫；src 的面積歸零）。"""
    m = seg.labels == src
    seg.labels[m] = dst
    s2 = seg.second == src
    seg.second[s2] = dst
    same = (seg.second == seg.labels)
    seg.second[same] = -1
    seg.alpha[same] = 1.0
    a, b = seg.regions[src], seg.regions[dst]
    if b.area == 0:
        b.bbox = a.bbox
    else:
        b.bbox = (min(a.bbox[0], b.bbox[0]), min(a.bbox[1], b.bbox[1]), max(a.bbox[2], b.bbox[2]), max(a.bbox[3], b.bbox[3]))
    b.area += a.area
    a.area = 0


def _fill_holes(m: np.ndarray) -> np.ndarray:
    from scipy.ndimage import binary_fill_holes

    return binary_fill_holes(m)


def _similar(seg: Segmentation, img: np.ndarray, a: int, b: int, de: float) -> bool:
    """區域 a 的顏色是否就是區域 b 的模型（b 是純色就比顏色；b 是漸層就看它在 a 的位置預測得準不準）。"""
    ra, rb = seg.regions[a], seg.regions[b]
    if rb.kind == "flat":
        if ra.kind != "flat":
            return False
        la = _lab(np.asarray([[ra.color]], dtype=np.float32))[0, 0]
        lb = _lab(np.asarray([[rb.color]], dtype=np.float32))[0, 0]
        return float(np.sqrt(((la - lb) ** 2).sum())) <= de
    x0, y0, x1, y1 = ra.bbox
    m = (seg.labels[y0:y1, x0:x1] == a) & (seg.alpha[y0:y1, x0:x1] >= 0.999)
    ys, xs = np.nonzero(m)
    if len(ys) < 4:
        return False
    step = max(1, len(ys) // 2000)
    ys, xs = ys[::step] + y0, xs[::step] + x0
    pred = rb.predict(xs, ys)
    return float(np.mean(np.abs(img[ys, xs].astype(np.float32) - pred).max(axis=1))) <= max(6.0, 4.0 * seg.sigma)


def _late(P: dict) -> bool:
    d = P.get("_deadline")
    return d is not None and time.perf_counter() > d


def _late_note(P: dict, where: str) -> str:
    return (f"描圖超過時間上限（{float(P['max_seconds']):g} 秒＋每百萬像素 {float(P['max_seconds_per_mpx']):g} 秒）仍未完成（{where}），"
            "內容太複雜，不適合向量化。這是時間上的安全網，機器很忙時結果可能不同。")


def _trace_once(img: np.ndarray, mask: Optional[np.ndarray], P: dict) -> VectorResult:
    H, W = img.shape[:2]
    timing: dict = {}
    t0 = time.perf_counter()
    seg = segment(img, P)
    if mask is not None:
        # 遮罩外不屬於這個圖形：每個區域的覆蓋率都乘上遮罩（邊緣稍微柔化，等值線才不會是階梯狀）
        seg.clip = np.clip(cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 0.5) * 2.0 - 0.5, 0.0, 1.0) if mask.any() else mask.astype(np.float32)
    timing["segment"] = round(time.perf_counter() - t0, 4)
    stats = dict(seg.stats)
    stats["timing"] = timing
    if seg.reject:
        return _empty((W, H), seg.reject, stats)
    notes: list[str] = []
    noise_k = 1.0 + min(1.5, seg.sigma / 4.0) + min(1.0, seg.blur_px * 0.5)
    tol = float(P["fit_tol_px"]) * noise_k
    tol_prim = float(P["prim_tol_px"]) * noise_k
    stats["tolerance_px"] = round(tol, 3)
    stats["blur_px"] = round(seg.blur_px, 3)
    n = len(seg.regions)

    # ---------- 底色
    bg_ids: set[int] = set()
    background: Optional[RGB] = None
    if mask is None:
        border = np.concatenate([seg.labels[0, :], seg.labels[-1, :], seg.labels[:, 0], seg.labels[:, -1]])
        cnt = np.bincount(border, minlength=n)
        k = int(np.argmax(cnt))
        if cnt[k] >= float(P["background_border_fraction"]) * len(border) and seg.regions[k].kind == "flat":
            bg_ids.add(k)
            background = _rgb(seg.regions[k].color)
    else:
        inside = np.bincount(seg.labels[mask], minlength=n)
        total = np.bincount(seg.labels.ravel(), minlength=n)
        for k in range(n):
            if total[k] > 0 and inside[k] < 0.5 * total[k]:
                bg_ids.add(k)

    # ---------- 洞裡同色的區域：是真的洞（看得到後面），併回外面的區域
    t1 = time.perf_counter()
    if P["layered"]:
        _merge_see_through(seg, img, bg_ids, P)
        _merge_across_thin(seg, bg_ids, P)
    live = [k for k in range(n) if seg.regions[k].area > 0]
    regs: dict[int, _Reg] = {k: _Reg(rid=k, model=seg.regions[k], is_bg=k in bg_ids) for k in live}
    timing["merge"] = round(time.perf_counter() - t1, 4)

    # ---------- 每個區域自己的次像素輪廓、鄰居、洞
    t1 = time.perf_counter()
    sat_eps = float(np.clip(0.02 + seg.sigma / 40.0 + 0.05 * min(seg.blur_px, 1.0), 0.02, 0.15))
    hole_min = max(4, 2 * int(P["min_region_px"]))
    for k in live:
        r = regs[k]
        c, X0, Y0 = _own_field(seg, k)
        for ct in _trace_region(c, X0, Y0, seg, k, sat_eps):
            r.contours.append(ct)
            vals, counts = np.unique(ct.out_label, return_counts=True)  # type: ignore[attr-defined]
            for v, cn in zip(vals, counts):
                if v != k:
                    r.neighbors[int(v)] = r.neighbors.get(int(v), 0) + int(cn)
        # 洞（遮罩層級）：洞裡有哪些區域
        m = c >= 0.5
        filled = _fill_holes(m)
        hole = filled & ~m
        if hole.any():
            nh, hcc = cv2.connectedComponents(hole.astype(np.uint8), connectivity=4)
            lab_c = seg.labels[Y0 : Y0 + c.shape[0], X0 : X0 + c.shape[1]]
            for h in range(1, nh):
                hm = hcc == h
                if int(hm.sum()) < hole_min:
                    continue  # 一兩個像素的洞是壓縮或雜訊的斑點，不拿來決定上下（排序預設就是小的在上）
                inner = set(int(v) for v in np.unique(lab_c[hm])) - {k}
                r.holes.append(((np.nonzero(hm)[0] + Y0, np.nonzero(hm)[1] + X0), inner))
    timing["contours"] = round(time.perf_counter() - t1, 4)
    stats["contour_points"] = int(sum(len(ct.points) for k in live for ct in regs[k].contours))
    stats["contour_density"] = round(stats["contour_points"] / float(max(1, H * W)), 4)

    # ---------- 圖層順序
    t1 = time.perf_counter()
    below: dict[int, set[int]] = {k: set() for k in live}  # below[a]：在 a 上面的區域
    fg = [k for k in live if not regs[k].is_bg]
    area = {k: seg.regions[k].area for k in live}
    if P["layered"]:
        for k in fg:
            r = regs[k]
            for _pos, inner in r.holes:
                for w in inner:
                    if w in regs and not regs[w].is_bg:
                        if r.model.thin:
                            below[w].add(k)  # 細線框住的東西在細線下面（筆畫壓在填色上）
                        else:
                            below[k].add(w)  # 洞裡的東西在上面
        for k in fg:
            if regs[k].model.thin:
                for j in regs[k].neighbors:
                    if j in regs and not regs[j].is_bg and not regs[j].model.thin and k not in below[k] and j not in below[k]:
                        below[j].add(k)  # 細線壓在相鄰的色塊上
        if P["completion"]:
            _completion_evidence(regs, fg, below, tol_prim, P, seg)
    order = _topo_order(fg, below, area, regs)
    rank = {k: i for i, k in enumerate(order)}
    timing["order"] = round(time.perf_counter() - t1, 4)
    if _late(P):
        stats["rejected"] = True
        return _empty((W, H), _late_note(P, "還沒開始擬合"), stats)

    # ---------- 每個區域的外形與擬合
    t1 = time.perf_counter()
    paths: list[TracedPath] = []
    fit_opts = {"tol_line": float(P["line_tol_px"]) * noise_k, "min_line": float(P["min_line_px"]), "corner_deg": float(P["corner_deg"]),
                "corner_trim": 1.5 + seg.blur_px, "snap_tol": float(P["snap_tol_px"]), "k2": int(round(4 + 2 * min(seg.blur_px, 2.0)))}
    prim_opts = {"snap_tol": float(P["snap_tol_px"]), "corner_blur": 1.0 + seg.blur_px}
    counts = {"primitive": 0, "stroke": 0, "gradient": 0, "completed": 0, "generic": 0, "holes_filled": 0}
    for k in order:
        r = regs[k]
        if r.model.area < int(P["min_region_px"]):
            continue
        above = {j for j in fg if rank[j] > rank[k]}
        new_paths = _region_paths(seg, r, regs, above, tol, tol_prim, fit_opts, prim_opts, P, counts)
        paths.extend(new_paths)
        if len(paths) > int(P["max_paths"]):
            stats["rejected"] = True
            return _empty((W, H), f"路徑超過 {P['max_paths']} 條，內容太複雜，不適合向量化。", stats)
        if _late(P):
            stats["rejected"] = True
            return _empty((W, H), _late_note(P, f"已擬合 {len(paths)} 條路徑"), stats)
    timing["fit"] = round(time.perf_counter() - t1, 4)
    stats["counts"] = counts

    # ---------- 重畫驗證
    t1 = time.perf_counter()
    res = VectorResult(paths=paths, size=(W, H), background=background, render_error=0.0, error_p95=0.0, iou=0.0,
                       node_count=sum(vr.path_node_count(p.path) for p in paths), accepted=False, confidence=0.0, notes=notes, stats=stats)
    # 前景的覆蓋率（1 減掉底色佔的比例）
    fg_true = np.ones((H, W), dtype=np.float32)
    if bg_ids:
        ids = list(bg_ids)
        bg_cov = np.where(np.isin(seg.labels, ids), seg.alpha, 0.0) + np.where(np.isin(seg.second, ids), 1.0 - seg.alpha, 0.0)
        fg_true = (1.0 - bg_cov).astype(np.float32)
    if mask is not None:
        fg_true = fg_true * mask
    _verify(res, img, mask, fg_true, seg, P)
    timing["verify"] = round(time.perf_counter() - t1, 4)
    return res


# ---------------------------------------------------------------- 合併


def _merge_see_through(seg: Segmentation, img: np.ndarray, bg_ids: set[int], P: dict) -> None:
    """洞裡的區域如果與洞外的區域同色，就是真的洞（看得到後面的東西）：把它併回外面的區域。"""
    H, W = seg.labels.shape
    de = max(float(P["see_through_de"]), 2.0 * seg.sigma)
    for _ in range(3):
        changed = False
        for k, rk in enumerate(seg.regions):
            if rk.area == 0:
                continue
            x0, y0, x1, y1 = rk.bbox
            X0, Y0, X1, Y1 = max(0, x0 - 2), max(0, y0 - 2), min(W, x1 + 2), min(H, y1 + 2)
            lab = seg.labels[Y0:Y1, X0:X1]
            m = np.pad(lab == k, 1)
            filled = _fill_holes(m)
            hole = (filled & ~m)[1:-1, 1:-1]
            if not hole.any():
                continue
            # 洞外是誰：外圈往外一圈的像素裡最多的區域
            ring = cv2.dilate(filled.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)[1:-1, 1:-1] & ~filled[1:-1, 1:-1]
            if not ring.any():
                parent = next(iter(bg_ids)) if len(bg_ids) == 1 else -1
            else:
                vals, cnt = np.unique(lab[ring], return_counts=True)
                parent = int(vals[np.argmax(cnt)])
            if parent < 0 or parent == k:
                continue
            inner, cnt = np.unique(lab[hole], return_counts=True)
            for w, c in zip(inner, cnt):
                w = int(w)
                if w == k or w == parent or seg.regions[w].area != int(c):
                    continue  # 只處理整個落在洞裡的區域
                if _similar(seg, img, w, parent, de):
                    _relabel(seg, w, parent)
                    if w in bg_ids:
                        bg_ids.discard(w)
                    changed = True
        if not changed:
            break


def _merge_across_thin(seg: Segmentation, bg_ids: set[int], P: dict) -> None:
    """同一個純色形狀被細線切成兩塊（例如線條橫過矩形）：兩塊隔著細線相鄰、顏色相同，就合回同一個區域。"""
    H, W = seg.labels.shape
    de = max(float(P["see_through_de"]), 2.0 * seg.sigma)
    r_close = int(math.ceil(float(P["close_px"])))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_close + 1, 2 * r_close + 1))
    lab_tab = _lab(np.stack([r.color for r in seg.regions])[None].astype(np.float32))[0]
    for t, rt in enumerate(seg.regions):
        if rt.area == 0 or not rt.thin:
            continue
        x0, y0, x1, y1 = rt.bbox
        X0, Y0, X1, Y1 = max(0, x0 - r_close - 1), max(0, y0 - r_close - 1), min(W, x1 + r_close + 1), min(H, y1 + r_close + 1)
        lab = seg.labels[Y0:Y1, X0:X1]
        near = cv2.dilate((lab == t).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        ids = [int(v) for v in np.unique(lab[near]) if v != t and seg.regions[int(v)].kind == "flat" and not seg.regions[int(v)].thin]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if seg.regions[a].area == 0 or seg.regions[b].area == 0:
                    continue
                if float(np.sqrt(((lab_tab[a] - lab_tab[b]) ** 2).sum())) > de:
                    continue
                da = cv2.dilate((lab == a).astype(np.uint8), ker).astype(bool)
                if (da & (lab == b)).any():
                    keep, drop = (a, b) if seg.regions[a].area >= seg.regions[b].area else (b, a)
                    if drop in bg_ids and keep not in bg_ids:
                        keep, drop = drop, keep
                    _relabel(seg, drop, keep)
                    ids[i] = ids[j] = keep


# ---------------------------------------------------------------- 圖層順序


def _visible_points(r: _Reg, exclude: set[int]) -> tuple[Optional[np.ndarray], float]:
    """區域最大外圈上「不與 exclude 相鄰」的點，以及被排除的比例。"""
    outer = [c for c in r.contours if c.area > 0]
    if not outer:
        return None, 0.0
    ct = max(outer, key=lambda c: c.area)
    lab = ct.out_label  # type: ignore[attr-defined]
    occ = np.isin(lab, list(exclude)) if exclude else np.zeros(len(lab), dtype=bool)
    # 遮擋邊界兩端各多去掉兩點（交界處的點同時受兩個區域影響）
    if occ.any():
        occ = occ | np.roll(occ, 1) | np.roll(occ, -1) | np.roll(occ, 2) | np.roll(occ, -2)
    return ct.points[~occ], float(occ.mean())


def _completion_evidence(regs: dict[int, _Reg], fg: list[int], below: dict[int, set[int]], tol_prim: float, P: dict, seg: Segmentation) -> None:
    """遮擋的證據：某個區域自己的外形不是基本幾何，但去掉與鄰居 j 相接的那一段邊界後，剩下的點擬合得出
    完整的基本幾何（而且每一邊都有點支持）——表示它被 j 蓋住了一塊，它在 j 下面，可以補成完整的形狀。"""
    tries = 0
    prim_opts = {"snap_tol": float(P["snap_tol_px"]), "corner_blur": 1.0 + seg.blur_px}
    for k in sorted(fg, key=lambda i: -seg.regions[i].area):
        r = regs[k]
        if r.model.thin or r.model.area < 60:
            continue
        outer = sorted([c for c in r.contours if c.area > 0], key=lambda c: -c.area)
        # 只有一塊（壓縮雜訊造成的零星小點不算：面積不到主體的 2%）
        if not outer or sum(c.area for c in outer[1:]) > 0.02 * outer[0].area:
            continue
        pts_all = outer[0].points
        if len(pts_all) < 24:
            continue
        known_above = set(below[k])
        full_pts, frac0 = _visible_points(r, known_above)
        if full_pts is None:
            continue
        if frac0 == 0.0:
            r.prim_full = vf.fit_primitive(pts_all, tol_prim, prim_opts, full=True)
            if r.prim_full is not None:
                continue  # 自己就是完整的基本幾何，沒有被遮住
        cands = [j for j, cnt in sorted(r.neighbors.items(), key=lambda t: -t[1]) if j in regs and not regs[j].is_bg and j != k and cnt >= 4 and k not in below[j]]
        if not cands:
            continue
        trials: list[set[int]] = [known_above | set(cands)]
        trials += [known_above | {j} for j in cands[:3] if len(cands) > 1]
        for ex in trials:
            if tries >= int(P["completion_max_tries"]):
                return
            vis, frac = _visible_points(r, ex)
            if vis is None or frac <= 0.02 or frac > 0.6 or len(vis) < 20:
                continue
            tries += 1
            pf = vf.fit_primitive(vis, tol_prim, prim_opts, full=False)
            if pf is None:
                continue
            occluders = _check_completion(seg, r, pf, ex)
            if occluders is None:
                continue
            r.completion = (pf, occluders)
            for j in occluders:
                below[k].add(j)
            break


def _check_completion(seg: Segmentation, r: _Reg, pf: vf.PrimFit, allowed: set[int]) -> Optional[set[int]]:
    """補全的形狀必須蓋住自己、只多蓋到允許的（在上面的）區域；自己的洞不算。回傳實際被蓋到的區域；不合格回傳 None。"""
    H, W = seg.labels.shape
    k = r.rid
    pts = vf.path_points(pf.path, 2.0)
    X0, Y0 = max(0, int(math.floor(pts[:, 0].min())) - 2), max(0, int(math.floor(pts[:, 1].min())) - 2)
    X1, Y1 = min(W, int(math.ceil(pts[:, 0].max())) + 2), min(H, int(math.ceil(pts[:, 1].max())) + 2)
    bx0, by0, bx1, by1 = r.model.bbox
    X0, Y0, X1, Y1 = min(X0, bx0), min(Y0, by0), max(X1, bx1), max(Y1, by1)
    local = [[seg_[0]] + [float(v) - (X0 if i % 2 == 0 else Y0) for i, v in enumerate(seg_[1:])] for seg_ in pf.path]
    cov = vr.path_coverage(local, X1 - X0, Y1 - Y0) >= 0.5
    lab = seg.labels[Y0:Y1, X0:X1]
    own = lab == k
    area = int(own.sum())
    missing = int((own & ~cov).sum())
    extra = cov & ~own
    for (hy, hx), inner in r.holes:
        if not (inner and inner <= allowed):  # 真的洞（看得到後面）保留，不算多蓋
            ok = (hy >= Y0) & (hy < Y1) & (hx >= X0) & (hx < X1)
            extra[hy[ok] - Y0, hx[ok] - X0] = False
    if missing > max(4, 0.02 * area):
        return None
    labs, cnt = np.unique(lab[extra], return_counts=True)
    occluders: set[int] = set()
    bad = 0
    for lb, c in zip(labs, cnt):
        lb = int(lb)
        if lb in allowed:
            if c >= 3:
                occluders.add(lb)
        else:
            bad += int(c)
    perim = max(1.0, math.sqrt(area) * 4)
    if bad > max(6, 0.6 * perim):  # 邊界上本來就會有半像素的出入
        return None
    if not occluders or int(extra.sum()) - bad < max(6, 0.01 * area):
        return None
    return occluders


def _topo_order(fg: list[int], below: dict[int, set[int]], area: dict[int, int], regs: dict[int, _Reg]) -> list[int]:
    """由下到上排序：先滿足「誰在誰下面」的關係，其餘大的在下、細線最後。關係有循環時，放掉擋路的那一條。"""
    indeg = {k: 0 for k in fg}
    for a in fg:
        below[a] = {b for b in below[a] if b in indeg and b != a}
        for b in below[a]:
            indeg[b] += 1

    def key(k):
        return (1 if regs[k].model.thin else 0, -area[k], k)

    heap = [(key(k), k) for k in fg if indeg[k] == 0]
    heapq.heapify(heap)
    out: list[int] = []
    done: set[int] = set()
    while len(out) < len(fg):
        if not heap:
            rest = [k for k in fg if k not in done]
            k = min(rest, key=key)  # 循環：挑最該在下面的，強制放下
            indeg[k] = 0
            heapq.heappush(heap, (key(k), k))
        _, k = heapq.heappop(heap)
        if k in done:
            continue
        done.add(k)
        out.append(k)
        for b in below[k]:
            if b in done:
                continue
            indeg[b] -= 1
            if indeg[b] <= 0:
                heapq.heappush(heap, (key(b), b))
    return out


# ---------------------------------------------------------------- 區域 → 路徑


def _paint(model: RegionModel) -> tuple[RGB, Optional[TracedGradient]]:
    if model.kind == "flat":
        return _rgb(model.color), None
    stops = [(float(o), _rgb(c)) for o, c in model.stops]
    return _rgb(model.color), TracedGradient(model.kind, [float(v) for v in model.coords], stops)


def _fit_err(points: np.ndarray, path: list[list[float]]) -> float:
    """次像素邊界點到擬合路徑的平均距離。"""
    from scipy.spatial import cKDTree

    if len(points) == 0:
        return 0.0
    samples = vf.path_points(path, 0.05)
    if len(samples) == 0:
        return 0.0
    step = max(1, len(points) // 1500)
    d, _ = cKDTree(samples).query(points[::step])
    return float(np.mean(d))


def _region_paths(seg: Segmentation, r: _Reg, regs: dict[int, _Reg], above: set[int], tol: float, tol_prim: float,
                  fit_opts: dict, prim_opts: dict, P: dict, counts: dict) -> list[TracedPath]:
    k = r.rid
    fill, grad = _paint(r.model)
    if grad is not None:
        counts["gradient"] += 1
    # ---- 補全：看得到的邊界擬合出完整的基本幾何，遮住它的區域都在上面
    if r.completion is not None and r.completion[1] <= above:
        pf = r.completion[0]
        vis, _ = _visible_points(r, r.completion[1])
        counts["completed"] += 1
        path, prim, rule = list(pf.path), pf.prim, "nonzero"
        # 真的洞（洞裡不是上層的東西）保留成複合路徑
        keep_holes = [pos for pos, inner in r.holes if not (inner and inner <= above)]
        if keep_holes:
            hole_px = set()
            for hy, hx in keep_holes:
                hole_px.update(zip(hy.tolist(), hx.tolist()))
            for hc in r.contours:
                if hc.area < 0 and (int(hc.outside[0][0]), int(hc.outside[0][1])) in hole_px:
                    hp = vf.fit_primitive(hc.points[::-1], tol_prim, prim_opts, full=True) if len(hc.points) >= 16 else None
                    path = path + (list(hp.path) if hp is not None else vf.fit_contour(hc.points, tol, fit_opts)[0])
                    prim, rule = None, "evenodd"
        if prim is not None:
            counts["primitive"] += 1
        return [TracedPath(path=path, fill=fill, fill_rule=rule, gradient=grad, primitive=prim, fit_error_px=_fit_err(vis, pf.path) if vis is not None else None,
                           completed=True, notes=["被上層遮住的部分依看得到的邊界補成完整的" + _KIND_ZH.get(pf.prim.kind, "形狀")] + pf.notes)]
    H, W = seg.labels.shape
    c, X0, Y0 = _own_field(seg, k)
    lab = seg.labels[Y0 : Y0 + c.shape[0], X0 : X0 + c.shape[1]]
    completed = False
    # ---- 筆畫：等寬的細長區域還原成中心線加線寬
    if P["strokes"] and r.model.kind == "flat":
        from . import vector_stroke as vs

        sp = vs.fit_strokes(c, (X0, Y0), fill, tol, fit_opts, prim_opts, P)
        if sp:
            counts["stroke"] += len(sp)
            return sp
    field_ = c
    if P["layered"] and above:
        up = np.isin(lab, list(above))
        if up.any():
            m = c >= 0.5
            field_ = c.copy()
            # 洞裡全是上層的東西：補起來
            for (hy, hx), inner in r.holes:
                if inner and inner <= above and not r.model.thin:
                    field_[hy - Y0, hx - X0] = 1.0
                    counts["holes_filled"] += 1
                    completed = True
            if not r.model.thin:
                # 被上層的細線切開的縫接回去；其餘在上層底下多墊一點，重畫時才不會露出接縫
                rc = int(math.ceil(float(P["close_px"])))
                ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rc + 1, 2 * rc + 1))
                mp = np.pad(m.astype(np.uint8), rc)
                closed = cv2.erode(cv2.dilate(mp, ker), ker)[rc:-rc, rc:-rc].astype(np.float32)
                bridged = cv2.GaussianBlur(closed, (0, 0), 0.8)
                # 模糊後的遮罩在邊界外 trap_px 處的值正規化成 0.5，等值線就落在那裡（平滑，不會是階梯狀）
                level = 0.5 * math.erfc(max(float(P["trap_px"]), 0.1) / (1.5 * math.sqrt(2.0)))
                trap = np.clip(cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.5) / (2.0 * level), 0.0, 1.0)
                extra = np.maximum(bridged, trap)
                field_ = np.where(up, np.maximum(field_, extra), field_).astype(np.float32)
                if (up & (closed > 0.5) & ~m).sum() > (up & (trap >= 0.5) & ~m).sum() + 4:
                    completed = True
    if field_ is c:
        contours = r.contours
    else:
        contours = _trace_region(field_, X0, Y0, seg, k, 0.1)
    outers = [ct for ct in contours if ct.area > 0]
    holes = [ct for ct in contours if ct.area < 0]
    out: list[TracedPath] = []
    min_area = float(P["min_region_px"])
    for oc in sorted(outers, key=lambda ct: -ct.area):
        if oc.area < min_area:
            continue
        opts = oc.points
        mine = []
        if holes:
            poly = opts.astype(np.float32)
            for hc in holes:
                if abs(hc.area) >= 1.0 and cv2.pointPolygonTest(poly, (float(hc.points[0][0]), float(hc.points[0][1])), False) > 0:
                    mine.append(hc)
        prim = None
        notes: list[str] = []
        err_pts = [opts]
        pf = vf.fit_primitive(opts, tol_prim, prim_opts, full=True)
        if not mine:
            if pf is not None:
                path, prim, notes = pf.path, pf.prim, list(pf.notes)
                counts["primitive"] += 1
            else:
                path, info = vf.fit_contour(opts, tol, fit_opts)
                if info.polygon is not None and len(info.polygon) >= 3:
                    prim = vf.polygon_primitive(info.polygon)
                    if prim is not None:
                        counts["primitive"] += 1
                if prim is None:
                    counts["generic"] += 1
            rule = "nonzero"
        else:
            path = list(pf.path) if pf is not None else vf.fit_contour(opts, tol, fit_opts)[0]
            for hc in mine:
                hpts = hc.points
                hp = vf.fit_primitive(hpts[::-1], tol_prim, prim_opts, full=True) if len(hpts) >= 16 else None
                path = path + (list(hp.path) if hp is not None else vf.fit_contour(hpts, tol, fit_opts)[0])
                err_pts.append(hpts)
            rule = "evenodd"
            counts["generic"] += 1
        out.append(TracedPath(path=path, fill=fill, fill_rule=rule, gradient=grad, primitive=prim, fit_error_px=_fit_err(np.vstack(err_pts), path) if not completed else None,
                              completed=completed, notes=notes))
    return out


_KIND_ZH = {"rect": "矩形", "rounded_rect": "圓角矩形", "ellipse": "橢圓", "circle": "圓", "line": "直線", "polygon": "多邊形"}


# ---------------------------------------------------------------- 驗證


def _edge_mag(img: np.ndarray) -> np.ndarray:
    """每個像素的顏色梯度（三個通道取大，色階／像素）。"""
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    return np.sqrt(gx * gx + gy * gy).max(axis=2)


def _verify(res: VectorResult, img: np.ndarray, mask: Optional[np.ndarray], fg_true: np.ndarray, seg: Segmentation, P: dict) -> None:
    H, W = img.shape[:2]
    rgba = vr.render_paths(res.paths, res.size, 1.0)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    if res.background is not None:
        under = np.broadcast_to(np.asarray(res.background, dtype=np.float32), img.shape)
    else:
        under = img.astype(np.float32)  # 沒有底色時，沒畫到的地方不算色差（覆蓋範圍另由 IoU 檢查）
    comp = rgba[..., :3].astype(np.float32) * a + under * (1.0 - a)
    de = np.sqrt(((_lab(comp) - _lab(img.astype(np.float32))) ** 2).sum(axis=2))
    region = np.ones((H, W), dtype=bool) if mask is None else mask
    if res.background is None and mask is not None:
        # 遮罩內沒畫到的像素：算成漏畫（色差以黑白間的距離計）。遮罩最外一圈不算：遮罩本身是逐像素的，
        # 描出來的平滑邊界只蓋住那一圈的一部分，沒蓋到的地方本來就該是透明
        # 遮罩內被判成底色（大部分在遮罩外的區域，例如裁切時多帶到的白紙）的像素本來就不畫，也不算
        solid = cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        de = np.where(solid & (fg_true >= 0.5) & (rgba[..., 3] < 128), 100.0, de)
    vals = de[region]
    if vals.size == 0:
        res.render_error, res.error_p95, res.iou = float("inf"), float("inf"), 0.0
        return
    res.render_error = float(vals.mean())
    res.error_p95 = float(np.percentile(vals, 95))
    # 覆蓋範圍用覆蓋率本身比（不取門檻：細線的邊緣剛好落在 0.5 上，取門檻會忽大忽小）
    drawn = (rgba[..., 3].astype(np.float32) / 255.0) * region
    truth = fg_true * region
    inter = float(np.minimum(drawn, truth).sum())
    union = float(np.maximum(drawn, truth).sum())
    soft = inter / union if union > 0 else 1.0
    # 模糊過的原圖邊緣是一道寬的漸變，向量的邊緣是銳利的：逐像素比覆蓋率，兩側的漸變尾巴都會算成不重疊
    # （σ 1 像素的模糊在 8 像素寬的筆畫上就只剩八成多）。所以另外比「覆蓋率過半」的範圍（0.5 等值線不受對稱模糊影響），
    # 兩者取較好的那個：細線靠前者、模糊的粗形狀靠後者。
    dh, th = drawn >= 0.5, truth >= 0.5
    un_h = int((dh | th).sum())
    hard = float((dh & th).sum()) / un_h if un_h > 0 else 1.0
    res.iou = max(soft, hard)
    res.stats["iou_soft"] = round(soft, 4)
    # 成塊的誤差：色差大的像素先做開運算去掉沿邊的細絲，剩下成塊的才是漏掉的東西。
    # 原圖與重畫「兩邊都有邊緣」的地方不算（模糊或縮放過的原圖邊緣比向量寬，沿邊會有一兩像素寬的色差帶，那不是漏畫）
    bad = ((de > max(float(P["blob_delta_e"]), 6.0 * seg.sigma)) & region)
    if bad.any():
        k3 = np.ones((3, 3), np.uint8)
        e_in = cv2.dilate((_edge_mag(img.astype(np.float32)) > 12.0).astype(np.uint8), k3)
        e_out = cv2.dilate((_edge_mag(comp) > 12.0).astype(np.uint8), k3)
        bad &= ~((e_in > 0) & (e_out > 0))
    bad = bad.astype(np.uint8)
    opened = cv2.morphologyEx(bad, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    blob_frac = float(opened.sum()) / float(max(1, region.sum()))
    res.stats["blob_fraction"] = round(blob_frac, 5)
    noise_de = 1.2 * seg.sigma  # 雜訊本身造成的色差（粗估）
    thr = float(P["accept_delta_e"]) + noise_de
    ok = True
    reasons = []
    if not res.paths:
        ok = False
        reasons.append("沒有描出任何路徑")
    if res.render_error > thr:
        ok = False
        reasons.append(f"重畫的平均色差 {res.render_error:.1f} 超過 {thr:.1f}")
    if res.error_p95 > float(P["accept_p95"]) + 3.0 * noise_de:
        ok = False
        reasons.append(f"色差的第 95 百分位 {res.error_p95:.1f} 偏高")
    if res.iou < float(P["accept_iou"]):
        ok = False
        reasons.append(f"覆蓋範圍只有 {res.iou:.0%} 重疊")
    if blob_frac > float(P["accept_blob_fraction"]):
        ok = False
        reasons.append(f"有 {blob_frac:.1%} 的面積成塊地對不上（可能漏了細節或有明暗變化）")
    res.accepted = ok
    if not ok:
        res.notes.append("重畫驗證未通過：" + "；".join(reasons) + "。")
    q_err = max(0.0, 1.0 - res.render_error / max(thr * 1.5, 1e-6))
    q_iou = max(0.0, (res.iou - 0.8) / 0.2) if res.iou < 1.0 else 1.0
    q_blob = max(0.0, 1.0 - blob_frac / max(float(P["accept_blob_fraction"]) * 2, 1e-9))
    conf = (0.5 * q_err + 0.25 * q_iou + 0.25 * q_blob)
    res.confidence = float(np.clip(conf if ok else min(conf, 0.45), 0.0, 1.0))
