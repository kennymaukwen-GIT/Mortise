"""描圖向量化的分割：把影像拆成「區域」，並算出每個像素被哪兩個區域以什麼比例混色。

做法（全部是量測，沒有學習模型，也不用隨機）：
  1. 雜訊：由 Laplacian 的中位數量出平坦區的雜訊；雜訊大時先做保邊平滑再分割（量顏色與覆蓋率仍用原圖）。
  2. 核心：二階差分與梯度都小的像素連通成「核心」。純色區、平滑漸層區都會成為核心；邊緣（含反鋸齒的混色帶）不會。
  3. 模型：每個核心擬合成純色、線性漸層或放射漸層；都不合才用分群切成幾個純色塊。
  4. 混色：非核心像素解釋成附近兩個核心顏色的混合，投影在兩色連線上的位置就是覆蓋率（邊界的次像素資訊）。
  5. 細節：解釋不了的像素（細線、小圖案，兩側都沒有自己的核心）另立區域，顏色取離底色最遠的一群。
  6. 合併：相鄰而顏色相同的區域合成一個。
結果是「兩標籤覆蓋率圖」：每個像素記主要區域、次要區域、主要區域的覆蓋率（0.5 到 1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np


@dataclass
class RegionModel:
    kind: str  # "flat"、"linear"、"radial"
    color: np.ndarray  # 純色，或漸層的平均色（float，0 到 255）
    coords: Optional[list[float]] = None  # linear：[x0, y0, x1, y1]；radial：[cx, cy, r]
    stops: Optional[list[tuple[float, tuple[float, float, float]]]] = None
    thin: bool = False  # 由「解釋不了的像素」建立的細線或小細節（沒有自己的核心）
    area: int = 0
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # x0, y0, x1, y1（右下不含）
    residual: float = 0.0  # 模型對核心像素的殘差（第 95 百分位，0 到 255 的色階）
    banded: bool = False  # 漸層擬合失敗、由分群切出來的色帶

    def predict(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """模型在像素 (xs, ys)（整數序號，取像素中心）的顏色。"""
        if self.kind == "flat":
            return np.broadcast_to(self.color.astype(np.float32), (len(xs), 3))
        from .vector_raster import gradient_colors

        return gradient_colors(self.kind, self.coords, self.stops, xs + 0.5, ys + 0.5)


@dataclass
class Segmentation:
    labels: np.ndarray  # int32 (H, W)：主要區域
    second: np.ndarray  # int32 (H, W)：次要區域，沒有則為 -1
    alpha: np.ndarray  # float32 (H, W)：主要區域的覆蓋率（0.5 到 1）
    regions: list[RegionModel]
    sigma: float  # 平坦區的雜訊（每通道標準差，色階）
    blur_px: float  # 邊緣過渡帶的寬度估計（高斯 σ，像素）
    reject: Optional[str] = None  # 不適合向量化的原因
    stats: dict = field(default_factory=dict)


def _lap_grad(F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lap = np.abs(cv2.Laplacian(F, cv2.CV_32F, ksize=1, borderType=cv2.BORDER_REPLICATE)).max(axis=2)
    k = np.array([[-0.5, 0.0, 0.5]], dtype=np.float32)
    gx = cv2.filter2D(F, cv2.CV_32F, k, borderType=cv2.BORDER_REPLICATE)
    gy = cv2.filter2D(F, cv2.CV_32F, k.T, borderType=cv2.BORDER_REPLICATE)
    grad = np.sqrt(gx * gx + gy * gy).max(axis=2)
    return lap, grad


def _ring_level(lap: np.ndarray, grad: np.ndarray, t_grad: float) -> float:
    """壓縮振鈴的大小（換算成與全圖 Laplacian 中位數同一個尺度的色階）。

    取「離強邊緣 1.5 到 4.5 像素」那一圈的 Laplacian 第 75 百分位，減掉遠離邊緣處的同一個統計量
    （漸層本身的色階、均勻的雜訊兩邊都有，會互相抵銷），剩下的就是只出現在邊緣旁邊的起伏。沒有強邊緣回傳 0。
    """
    edge = grad > t_grad
    n_edge = int(edge.sum())
    if n_edge < 16 or n_edge > 0.5 * edge.size:
        return 0.0
    dist = cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, 3)
    band = (dist >= 1.5) & (dist <= 4.5)
    if int(band.sum()) < 64:
        return 0.0
    far = dist > 6.5
    base = float(np.percentile(lap[far], 75)) if int(far.sum()) >= 64 else 0.0
    return _RING_K * max(0.0, float(np.percentile(lap[band], 75)) - base)


_RING_K = 1.5


def _rdp_profile(s: np.ndarray, c: np.ndarray, tol: float) -> list[int]:
    """色彩剖面（位置 s、顏色 c）的折線簡化，回傳保留的序號。"""
    keep = [0, len(s) - 1]
    stack = [(0, len(s) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        t = (s[a + 1 : b] - s[a]) / max(s[b] - s[a], 1e-9)
        interp = c[a][None, :] + t[:, None] * (c[b] - c[a])[None, :]
        d = np.abs(c[a + 1 : b] - interp).max(axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            keep.append(m)
            stack.append((a, m))
            stack.append((m, b))
    return sorted(set(keep))


def _profile(s: np.ndarray, col: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """沿一維座標 s 分箱取平均色。回傳（箱中心、平均色、每個像素依剖面內插的預測色）。"""
    nb = int(np.clip(round((hi - lo) / 2.0), 6, 96))
    idx = np.clip(((s - lo) / max(hi - lo, 1e-9) * nb).astype(np.int64), 0, nb - 1)
    cnt = np.bincount(idx, minlength=nb).astype(np.float64)
    ok = cnt >= 3
    if ok.sum() < 3:
        ok = cnt >= 1
    cen = lo + (np.arange(nb) + 0.5) * (hi - lo) / nb
    mean = np.stack([np.bincount(idx, weights=col[:, c], minlength=nb) / np.maximum(cnt, 1) for c in range(3)], axis=1)
    cen, mean = cen[ok], mean[ok]
    pred = np.stack([np.interp(s, cen, mean[:, c]) for c in range(3)], axis=1)
    return cen, mean, pred


def _fit_gradient(xs: np.ndarray, ys: np.ndarray, col: np.ndarray, tol: float, stop_tol: float) -> Optional[RegionModel]:
    """核心像素擬合線性或放射漸層。回傳模型，或 None（兩種都不合）。"""
    n = len(xs)
    if n < 40:
        return None
    x = xs.astype(np.float64) + 0.5
    y = ys.astype(np.float64) + 0.5
    c = col.astype(np.float64)
    mx, my = x.mean(), y.mean()
    A = np.stack([x - mx, y - my, np.ones(n)], axis=1)
    coef, *_ = np.linalg.lstsq(A, c, rcond=None)  # (3, 3)：每通道的 x、y 斜率與常數
    G = coef[:2].T  # (3, 2)
    _, sv, vt = np.linalg.svd(G, full_matrices=False)
    cand: list[tuple[float, RegionModel]] = []
    if sv[0] > 1e-6:
        v = vt[0]
        s = (x - mx) * v[0] + (y - my) * v[1]
        lo, hi = float(s.min()), float(s.max())
        if hi - lo >= 6.0:
            cen, mean, pred = _profile(s, c, lo, hi)
            res = np.abs(c - pred).max(axis=1)
            p95 = float(np.percentile(res, 95))
            if p95 <= tol and len(cen) >= 3:
                # 兩端往外推 3 像素（邊緣像素落在核心外面，顏色要能外插）
                ext = 3.0
                s_all = np.concatenate([[lo - ext], cen, [hi + ext]])
                c0 = mean[0] + (mean[1] - mean[0]) * ((lo - ext - cen[0]) / max(cen[1] - cen[0], 1e-9))
                c1 = mean[-1] + (mean[-1] - mean[-2]) * ((hi + ext - cen[-1]) / max(cen[-1] - cen[-2], 1e-9))
                c_all = np.clip(np.vstack([c0, mean, c1]), 0, 255)
                keep = _rdp_profile(s_all, c_all, stop_tol)
                span = s_all[-1] - s_all[0]
                stops = [(float((s_all[k] - s_all[0]) / span), tuple(float(t) for t in c_all[k])) for k in keep]
                p0 = (mx + v[0] * s_all[0], my + v[1] * s_all[0])
                p1 = (mx + v[0] * s_all[-1], my + v[1] * s_all[-1])
                cand.append((p95, RegionModel("linear", c.mean(axis=0), [float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1])], stops, residual=p95)))
    if cand and cand[0][0] <= 0.6 * tol:
        return cand[0][1]
    # ---- 放射：先用主成分上的等向拋物面估中心，再微調到剖面殘差最小
    cm = c - c.mean(axis=0)
    _, _, pv = np.linalg.svd(cm[:: max(1, n // 4000)], full_matrices=False)
    f = cm @ pv[0]
    B = np.stack([np.ones(n), x - mx, y - my, (x - mx) ** 2 + (y - my) ** 2], axis=1)
    q, *_ = np.linalg.lstsq(B, f, rcond=None)
    if abs(q[3]) > 1e-9:
        cx0, cy0 = mx - q[1] / (2 * q[3]), my - q[2] / (2 * q[3])
        ext_xy = max(float(x.max() - x.min()), float(y.max() - y.min()))
        if abs(cx0 - mx) < 3 * ext_xy and abs(cy0 - my) < 3 * ext_xy:

            def cost(p):
                r = np.hypot(x - p[0], y - p[1])
                _, _, pred = _profile(r, c, 0.0, float(r.max()))
                return float(np.mean(np.abs(c - pred)))

            from scipy.optimize import minimize

            sub = slice(None, None, max(1, n // 6000))
            xs_, ys_, cs_ = x[sub], y[sub], c[sub]

            def cost_sub(p):
                r = np.hypot(xs_ - p[0], ys_ - p[1])
                _, _, pred = _profile(r, cs_, 0.0, float(r.max()))
                return float(np.mean(np.abs(cs_ - pred)))

            try:
                opt = minimize(cost_sub, [cx0, cy0], method="Nelder-Mead", options={"xatol": 0.05, "fatol": 1e-3, "maxiter": 80})
                cx, cy = float(opt.x[0]), float(opt.x[1])
            except Exception:
                cx, cy = cx0, cy0
            r = np.hypot(x - cx, y - cy)
            rmax = float(r.max())
            cen, mean, pred = _profile(r, c, 0.0, rmax)
            res = np.abs(c - pred).max(axis=1)
            p95 = float(np.percentile(res, 95))
            if p95 <= tol and len(cen) >= 3 and rmax >= 4.0:
                ext = 3.0
                r_all = np.concatenate([[0.0], cen, [rmax + ext]])
                c1 = mean[-1] + (mean[-1] - mean[-2]) * ((rmax + ext - cen[-1]) / max(cen[-1] - cen[-2], 1e-9))
                c_all = np.clip(np.vstack([mean[0], mean, c1]), 0, 255)
                keep = _rdp_profile(r_all, c_all, stop_tol)
                R = r_all[-1]
                stops = [(float(r_all[k] / R), tuple(float(t) for t in c_all[k])) for k in keep]
                cand.append((p95, RegionModel("radial", c.mean(axis=0), [cx, cy, float(R)], stops, residual=p95)))
    if not cand:
        return None
    cand.sort(key=lambda t: t[0])
    return cand[0][1]


def _seg_dist(I: np.ndarray, C1: np.ndarray, C2: np.ndarray, over: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """像素色 I 到「C1 到 C2 的連線」的距離、投影位置 t（0 在 C1、1 在 C2）、兩色的距離。

    over > 0 時連線兩端各延長兩色距離的 over 倍再量距離（壓縮過的圖在邊緣有過衝：比兩側都更亮或更暗），
    回傳的 t 仍限制在 0 到 1（過衝的像素當成完全屬於靠近的那一側）。"""
    v = C2 - C1
    w = I - C1
    vv = np.einsum("ij,ij->i", v, v)
    t = np.clip(np.einsum("ij,ij->i", w, v) / np.maximum(vv, 1e-6), -over, 1.0 + over)
    d = w - t[:, None] * v
    return np.sqrt(np.einsum("ij,ij->i", d, d)), np.clip(t, 0.0, 1.0), np.sqrt(vv)


def _metric(wc: float) -> np.ndarray:
    """混色判斷用的色彩距離：沿亮度方向不變，垂直於亮度的色度方向乘上 wc。

    第一個軸是 JPEG 的亮度方向（0.299, 0.587, 0.114），另兩軸與它正交；wc = 1 時就是一般的 RGB 距離。
    JPEG 的色度縮小取樣（4:2:0）只改變色度、不改變亮度，所以它在邊緣造成的色差全部落在後兩軸上，
    縮小 wc 就能讓那些像素仍被解釋成兩色混合（覆蓋率由亮度決定，亮度是全解析度的）。
    """
    y = np.array([0.299, 0.587, 0.114], dtype=np.float64)
    y /= np.linalg.norm(y)
    a = np.cross(y, [0.0, 0.0, 1.0])
    a /= np.linalg.norm(a)
    b = np.cross(y, a)
    return (np.diag([1.0, wc, wc]) @ np.stack([y, a, b])).astype(np.float32)


def _chroma_sharpness(I: np.ndarray) -> Optional[float]:
    """色度邊緣相對於亮度邊緣的銳利程度（約 1 表示一樣銳利）。

    JPEG 的 4:2:0 色度縮小取樣把色度的解析度減半，色度邊緣比亮度邊緣寬一倍：在「亮度與色度都在變」的邊緣上，
    二階差分÷一階差分（越銳利越大）色度只有亮度的四到五成；乾淨、縮放、模糊、加雜訊的圖都在 1 附近（兩者一起變）。
    這種邊緣太少（例如只有黑白灰）時回傳 None。"""
    Y = I @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    C = np.stack([I @ np.array([-0.1687, -0.3313, 0.5], dtype=np.float32), I @ np.array([0.5, -0.4187, -0.0813], dtype=np.float32)], axis=-1)
    k = np.array([[-0.5, 0.0, 0.5]], dtype=np.float32)

    def grad_lap(F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        gx = cv2.filter2D(F, cv2.CV_32F, k, borderType=cv2.BORDER_REPLICATE)
        gy = cv2.filter2D(F, cv2.CV_32F, k.T, borderType=cv2.BORDER_REPLICATE)
        lp = cv2.Laplacian(F, cv2.CV_32F, ksize=1, borderType=cv2.BORDER_REPLICATE)
        if F.ndim == 2:
            return np.sqrt(gx * gx + gy * gy), np.abs(lp)
        return np.sqrt((gx * gx + gy * gy).sum(axis=-1)), np.sqrt((lp * lp).sum(axis=-1))

    gY, lY = grad_lap(np.ascontiguousarray(Y))
    gC, lC = grad_lap(np.ascontiguousarray(C))
    m = (gY > 8.0) & (gC > 8.0)
    if int(m.sum()) < 50:
        return None
    rY = float(np.median(lY[m] / gY[m]))
    return float(np.median(lC[m] / gC[m])) / max(rY, 1e-6)


def segment(image: np.ndarray, P: dict) -> Segmentation:
    H, W = image.shape[:2]
    I = np.ascontiguousarray(image[..., :3]).astype(np.float32)
    stats: dict = {}
    # ---------- 1. 雜訊
    lap, grad = _lap_grad(I)
    s_lap = float(np.median(lap))
    # 壓縮振鈴（JPEG）：整張圖大多是完全平坦的，雜訊只出現在邊緣旁邊幾個像素內，全圖的中位數量不到。
    # 所以另外只看「離強邊緣 1.5 到 4.5 像素」的那一圈：乾淨、縮放、模糊過的圖在那裡是平的，壓縮過的才會起伏。
    s_ring = _ring_level(lap, grad, float(P["smooth_grad"]))
    stats["ring"] = round(s_ring, 3)
    s_lap = max(s_lap, s_ring)
    sigma = s_lap / 4.5
    F = I
    if s_lap > float(P["denoise_lap"]):
        sc = float(np.clip(6.0 * sigma, 10.0, 60.0))
        F = cv2.bilateralFilter(I, 5, sc, 1.6)
        F = cv2.bilateralFilter(F, 5, sc, 1.6)
        lap, grad = _lap_grad(F)
        s_lap = float(np.median(lap))
    stats["sigma"] = round(sigma, 3)
    stats["denoised"] = F is not I
    t_lap = max(float(P["smooth_lap"]), float(P["smooth_lap_k"]) * s_lap)
    t_grad = float(P["smooth_grad"])
    smooth = (lap <= t_lap) & (grad <= t_grad)
    # ---------- 2. 核心
    n_cc, cc, cc_stats, _ = cv2.connectedComponentsWithStats(smooth.astype(np.uint8), connectivity=4)
    min_core = int(P["min_core_px"])
    big = np.nonzero(cc_stats[:, cv2.CC_STAT_AREA] >= min_core)[0]
    big = big[big != 0]
    stats["cores"] = int(len(big))
    core_frac = float(cc_stats[big, cv2.CC_STAT_AREA].sum()) / float(H * W)
    stats["core_fraction"] = round(core_frac, 4)
    max_regions = int(P["max_regions"] + P["max_regions_per_mpx"] * H * W / 1e6)
    if len(big) > max_regions:
        return _rejected(H, W, sigma, stats, f"色塊太多（{len(big)} 個平滑區塊，上限 {max_regions}），像照片或材質，不適合向量化。")
    if core_frac < float(P["min_core_fraction"]):
        return _rejected(H, W, sigma, stats, f"平滑區域只佔 {core_frac:.0%}，像照片或材質，不適合向量化。")
    regions: list[RegionModel] = []
    core = np.full((H, W), -1, dtype=np.int32)
    t_flat = max(float(P["flat_tol"]), 3.5 * sigma)
    t_gradfit = max(float(P["gradient_tol"]), 4.0 * sigma)
    stop_tol = max(float(P["gradient_stop_tol"]), 1.5 * sigma)
    n_banded = 0
    for k in big:
        x0, y0, w, h = (int(cc_stats[k, i]) for i in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        sub = cc[y0 : y0 + h, x0 : x0 + w] == k
        ys, xs = np.nonzero(sub)
        col = I[y0 : y0 + h, x0 : x0 + w][sub]
        med = np.median(col, axis=0)
        dev = np.abs(col - med).max(axis=1)
        within = dev <= t_flat
        if float(within.mean()) >= float(P["flat_fraction"]):
            rid = len(regions)
            regions.append(RegionModel("flat", col[within].mean(axis=0).astype(np.float64), residual=float(np.percentile(dev, 95))))
            core[y0 : y0 + h, x0 : x0 + w][sub] = rid
            continue
        step = max(1, len(xs) // 20000)
        model = _fit_gradient(xs[::step] + x0, ys[::step] + y0, col[::step], t_gradfit, stop_tol) if P["gradients"] else None
        if model is not None:
            # 色標之間的差距還不到純色的容許偏差：不是漸層，是有雜訊或壓縮痕跡的純色（例如 JPEG 的白底）
            sc = np.array([c for _, c in model.stops], dtype=np.float64)
            if float(np.abs(sc - sc.mean(axis=0)).max()) <= 2.0 * t_flat:
                model = RegionModel("flat", np.median(col, axis=0).astype(np.float64), residual=float(np.percentile(dev, 95)))
            rid = len(regions)
            regions.append(model)
            core[y0 : y0 + h, x0 : x0 + w][sub] = rid
            continue
        # 純色與漸層都不合：分群切成色帶（相近而不同的兩色被連在一起，或是複雜的明暗）
        n_banded += 1
        lab_px = cv2.cvtColor((col[None] / 255.0).astype(np.float32), cv2.COLOR_RGB2Lab)[0]
        assigned = _split_colors(lab_px, float(P["band_de"]), int(P["band_max"]))
        local = np.full(sub.shape, -1, dtype=np.int32)
        local[ys, xs] = assigned
        for b in range(int(assigned.max()) + 1):
            nb, cb, sb, _ = cv2.connectedComponentsWithStats((local == b).astype(np.uint8), connectivity=4)
            for j in range(1, nb):
                if sb[j, cv2.CC_STAT_AREA] < min_core:
                    continue
                m = cb == j
                rid = len(regions)
                cj = I[y0 : y0 + h, x0 : x0 + w][m]
                regions.append(RegionModel("flat", cj.mean(axis=0).astype(np.float64), banded=True, residual=float(np.percentile(np.abs(cj - cj.mean(axis=0)).max(axis=1), 95))))
                core[y0 : y0 + h, x0 : x0 + w][m] = rid
        if len(regions) > max_regions:
            return _rejected(H, W, sigma, stats, f"色塊太多（超過 {max_regions} 個），像照片或有複雜明暗，不適合向量化。")
    stats["banded_cores"] = n_banded
    if not regions:
        return _rejected(H, W, sigma, stats, "找不到平滑的色塊，不適合向量化。")
    # ---------- 3. 非核心像素：最近的核心與預測色
    is_core = core >= 0
    _, near = cv2.distanceTransformWithLabels((~is_core).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    core_idx = np.flatnonzero(is_core.ravel())
    L1 = core.ravel()[core_idx[near.ravel() - 1]].reshape(H, W)
    pred = np.empty((H, W, 3), dtype=np.float32)
    flat_tab = np.stack([r.color for r in regions]).astype(np.float32)
    pred[:] = flat_tab[L1]
    for rid, r in enumerate(regions):
        if r.kind != "flat":
            ys, xs = np.nonzero(L1 == rid)
            pred[ys, xs] = r.predict(xs, ys)
    labels = L1.copy()
    second = np.full((H, W), -1, dtype=np.int32)
    alpha = np.ones((H, W), dtype=np.float32)
    nys, nxs = np.nonzero(~is_core)
    # 色度被縮小取樣過（JPEG）或量得到明顯的雜訊時，混色判斷降低色度的權重（見 _metric）；乾淨的圖照一般的 RGB 距離
    csharp = _chroma_sharpness(I)
    subsampled = csharp is not None and csharp < float(P["chroma_subsampled_below"])
    stats["chroma_sharpness"] = None if csharp is None else round(csharp, 3)
    wc = float(P["chroma_weight"]) if (subsampled or sigma >= float(P["chroma_weight_sigma"])) else 1.0
    stats["chroma_weight"] = wc
    # 過衝：壓縮、雙三次放大、銳化都會讓邊緣旁邊比兩側更亮或更暗。沒量到雜訊時只容許小幅過衝（放大造成的約一成）
    over = float(P["overshoot"]) if wc < 1.0 else float(P["overshoot_clean"])
    M = _metric(wc)
    Iw = (I.reshape(-1, 3) @ M.T).reshape(H, W, 3)
    predw = (pred.reshape(-1, 3) @ M.T).reshape(H, W, 3)
    flat_w = flat_tab @ M.T
    tau0 = max(float(P["explain_tol"]), 4.0 * sigma)
    if len(nys):
        Ip = Iw[nys, nxs]
        C1 = predw[nys, nxs]
        d0 = np.sqrt(((Ip - C1) ** 2).sum(axis=1))
        best_d = np.full(len(nys), np.inf, dtype=np.float32)
        best_t = np.zeros(len(nys), dtype=np.float32)
        best_j = np.full(len(nys), -1, dtype=np.int32)
        l1 = L1[nys, nxs]
        radii = [int(r) for r in P["explain_radii"]]
        for r in radii:
            dg = max(1, int(round(r * 0.7071)))
            for dy, dx in ((0, r), (0, -r), (r, 0), (-r, 0), (dg, dg), (dg, -dg), (-dg, dg), (-dg, -dg)):
                yy = np.clip(nys + dy, 0, H - 1)
                xx = np.clip(nxs + dx, 0, W - 1)
                j = L1[yy, xx]
                diff = j != l1
                if not diff.any():
                    continue
                Cj = predw[yy, xx]
                d, t, vv = _seg_dist(Ip, C1, Cj, over)
                d = np.where(diff & (vv > 2.0 * tau0), d - float(P["explain_slack"]) * vv * np.minimum(t, 1 - t) * 2.0, np.inf)
                better = d < best_d
                best_d = np.where(better, d, best_d)
                best_t = np.where(better, t, best_t)
                best_j = np.where(better, j, best_j)
        single = d0 <= tau0
        two = (~single) & (best_d <= tau0)
        unexplained = ~(single | two)
        # 第二輪：上面只看「最近的核心」，斜的細線常常只有零星幾段核心（線的兩側每隔一段才換一列），
        # 兩段核心中間的邊緣像素四周的最近核心全是底色，找不到另一種顏色。這裡改看鄰近像素「已經解釋好」的區域
        # （純色像素已貼上自己的區域），再試一次兩色混合；仍解釋不了的才當成細節。
        if unexplained.any():
            lab_now = L1.copy()
            ok1 = single | two
            lab_now[nys[ok1], nxs[ok1]] = np.where(two[ok1] & (best_t[ok1] > 0.5), best_j[ok1], l1[ok1])
            lab_now[nys[unexplained], nxs[unexplained]] = -1
            ui = np.nonzero(unexplained)[0]
            uy, ux = nys[ui], nxs[ui]
            Iu, Cu, lu = Ip[ui], C1[ui], l1[ui]
            bd = np.full(len(ui), np.inf, dtype=np.float32)
            bt = np.zeros(len(ui), dtype=np.float32)
            bj = np.full(len(ui), -1, dtype=np.int32)
            for r in (1, 2):
                for dy, dx in ((0, r), (0, -r), (r, 0), (-r, 0), (r, r), (r, -r), (-r, r), (-r, -r)):
                    yy = np.clip(uy + dy, 0, H - 1)
                    xx = np.clip(ux + dx, 0, W - 1)
                    j = lab_now[yy, xx]
                    diff = (j >= 0) & (j != lu)
                    if not diff.any():
                        continue
                    Cj = flat_w[np.maximum(j, 0)]
                    d, t, vv = _seg_dist(Iu, Cu, Cj, over)
                    d = np.where(diff & (vv > 2.0 * tau0), d - float(P["explain_slack"]) * vv * np.minimum(t, 1 - t) * 2.0, np.inf)
                    better = d < bd
                    bd = np.where(better, d, bd)
                    bt = np.where(better, t, bt)
                    bj = np.where(better, j, bj)
            fix = bd <= tau0
            if fix.any():
                two[ui[fix]] = True
                best_t[ui[fix]] = bt[fix]
                best_j[ui[fix]] = bj[fix]
                unexplained = ~(single | two)
        t2 = best_t[two]
        swap = t2 > 0.5
        ty, tx = nys[two], nxs[two]
        labels[ty, tx] = np.where(swap, best_j[two], l1[two])
        second[ty, tx] = np.where(swap, l1[two], best_j[two])
        alpha[ty, tx] = np.where(swap, t2, 1.0 - t2)
        # ---------- 4. 解釋不了的像素：細線與小細節
        U = np.zeros((H, W), dtype=np.uint8)
        U[nys[unexplained], nxs[unexplained]] = 1
        stats["unexplained_fraction"] = round(float(U.mean()), 4)
        n_u, ucc, ustats, _ = cv2.connectedComponentsWithStats(U, connectivity=8)
        max_feat = int(P["max_features"] + P["max_features_per_mpx"] * H * W / 1e6)
        cand = [k for k in range(1, n_u) if ustats[k, cv2.CC_STAT_AREA] >= int(P["min_feature_px"])]
        if len(cand) > max_feat:
            return _rejected(H, W, sigma, stats, f"零碎的細節太多（{len(cand)} 處，上限 {max_feat}），像照片或材質，不適合向量化。")
        contrast_min = max(float(P["feature_contrast"]), 8.0 * sigma)
        leftover = 0
        absorbed = 0
        er = 1
        ker_e = np.ones((2 * er + 1, 2 * er + 1), np.uint8)
        n_base = len(regions)
        for k in cand:
            x0, y0, w, h = (int(ustats[k, i]) for i in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
            # 連同外圍一圈像素一起看：那一圈常是細線蓋住不到一半的像素（已被解釋成底色，但其實帶一點細線的顏色）
            X0, Y0, X1, Y1 = max(0, x0 - 1), max(0, y0 - 1), min(W, x0 + w + 1), min(H, y0 + h + 1)
            m = ucc[Y0:Y1, X0:X1] == k
            # 緊貼著某個相鄰區域、顏色又與它相近（對比不到細節門檻）的一小塊：是壓縮的振鈴或過衝，不是細節，
            # 直接歸給最像的那個相鄰區域（最近的核心可能是另一側的顏色，所以要看實際相鄰的區域）
            if wc < 1.0:
                around = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~m
                ids = np.unique(labels[Y0:Y1, X0:X1][around])
                ids = ids[(ids >= 0) & (ids < n_base)]
                if len(ids):
                    ys_m, xs_m = np.nonzero(m)
                    px = Iw[ys_m + Y0, xs_m + X0]
                    dist = np.sqrt(((px[:, None, :] - flat_w[ids][None, :, :]) ** 2).sum(axis=2))
                    # 振鈴的大小與邊緣兩側的色差成正比：門檻取細節門檻與「相鄰區域之間最大色差 × ring_fraction」的較大者
                    cw = flat_w[ids]
                    local = float(np.sqrt(((cw[:, None, :] - cw[None, :, :]) ** 2).sum(axis=2)).max()) if len(ids) > 1 else 0.0
                    if float(dist.min(axis=1).max()) < max(contrast_min, float(P["ring_fraction"]) * local):
                        jj = ids[np.argmin(dist, axis=1)]
                        labels[ys_m + Y0, xs_m + X0] = jj
                        second[ys_m + Y0, xs_m + X0] = -1
                        alpha[ys_m + Y0, xs_m + X0] = 1.0
                        absorbed += len(ys_m)
                        continue
            ring = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~m & ~is_core[Y0:Y1, X0:X1] & (second[Y0:Y1, X0:X1] < 0)
            ys_m, xs_m = np.nonzero(m)
            ys_r, xs_r = np.nonzero(ring)
            ys_a = np.concatenate([ys_m, ys_r]) + Y0
            xs_a = np.concatenate([xs_m, xs_r]) + X0
            n_m = len(ys_m)
            Ia = I[ys_a, xs_a]
            # 細節的顏色歸屬用一般的 RGB 距離（不降色度權重）：降了以後，彩色細線的邊緣會被當成「深灰色細線蓋住一部分」
            Iaw = Ia
            Caw = pred[ys_a, xs_a]
            mag = np.sqrt(((Iaw - Caw) ** 2).sum(axis=1))
            remaining = np.ones(len(ys_a), dtype=bool)
            remaining[n_m:] = False  # 外圈像素不當種子
            is_ring = np.arange(len(ys_a)) >= n_m
            got = False
            # 每個像素目前歸給哪個細節（-1 表示還沒有）與它在那個細節裡的覆蓋率
            own = np.full(len(ys_a), -1, dtype=np.int64)
            own_t = np.zeros(len(ys_a), dtype=np.float32)
            made: list[tuple[int, np.ndarray]] = []  # 這一塊已找到的細節（區域編號、在混色度量下的顏色）
            for _round in range(int(P["feature_colors"])):
                if not remaining.any():
                    break
                mm = np.where(remaining, mag, -1.0)
                seed = int(np.argmax(mm))
                if mm[seed] < contrast_min:
                    break
                near_seed = (np.sqrt(((Iaw - Iaw[seed]) ** 2).sum(axis=1)) <= max(0.25 * mm[seed], 2.0 * tau0)) & ~is_ring & remaining
                Ct = Ia[near_seed].mean(axis=0)
                Ctw = Iaw[near_seed].mean(axis=0)
                if wc < 1.0 and made:
                    # 壓縮過的圖：這個顏色若只是「底色與前面已找到的細節」混色後色度被抹開的結果（彩色細線旁的淡色光暈），
                    # 就不另立細節，這些像素改算成那條細節蓋住一部分
                    hw = Iw[ys_a, xs_a]
                    bw = predw[ys_a, xs_a]
                    hit = False
                    for prid, pcol_w in made:
                        # 光暈一定緊貼著那條細節（兩像素內）；離得遠的同色系像素是另一條線（例如淡灰格線與深灰軸線）
                        loc = np.zeros((Y1 - Y0 + 4, X1 - X0 + 4), dtype=np.uint8)
                        sel_p = (own == prid) & (own_t >= 0.5)
                        loc[ys_a[sel_p] - Y0 + 2, xs_a[sel_p] - X0 + 2] = 1
                        close = cv2.dilate(loc, np.ones((5, 5), np.uint8))[ys_a - Y0 + 2, xs_a - X0 + 2].astype(bool)
                        if not close[seed]:
                            continue
                        dd, tt_, vv_ = _seg_dist((Ct.astype(np.float32)[None, :] @ M.T), bw[seed : seed + 1], pcol_w[None, :], over)
                        if float(dd[0]) <= tau0 + float(P["explain_slack"]) * float(vv_[0]):
                            d2, t2_, _ = _seg_dist(hw, bw, np.broadcast_to(pcol_w, hw.shape), over)
                            take = near_seed & close & (own < 0) & (d2 <= tau0 + float(P["explain_slack"]) * vv_[0])
                            take[seed] = True
                            own[take] = prid
                            own_t[take] = t2_[take]
                            remaining &= ~take
                            hit = True
                            break
                    if hit:
                        continue
                d, t, vv = _seg_dist(Iaw, Caw, np.broadcast_to(Ctw, Iaw.shape))
                ok = (d <= tau0 + float(P["explain_slack"]) * vv) & (vv > contrast_min * 0.5) & (t > 0.04)
                # 已經歸給別的細節的像素：只有在那邊只是淡淡的邊緣、在這邊蓋過一半時才改歸這邊
                # （淡色細線接在深色線旁邊時，接點的像素是「整格淡色」而不是「一點點深色」）
                ok &= (own < 0) | ((own_t < 0.5) & (t >= 0.5))
                strong = ok & (t >= 0.5)
                if int(strong.sum()) < 1:
                    remaining[seed] = False
                    continue
                # 覆蓋不到一半的像素只有緊貼著「蓋過一半的像素」時才是這條線的邊緣；
                # 離得遠的是另一條更淡或更細的線（顏色在同一條連線上），留給下一輪
                loc = np.zeros((Y1 - Y0 + 2 * er, X1 - X0 + 2 * er), dtype=np.uint8)
                loc[ys_a[strong] - Y0 + er, xs_a[strong] - X0 + er] = 1
                near_strong = cv2.dilate(loc, ker_e)[ys_a - Y0 + er, xs_a - X0 + er].astype(bool)
                acc = ok & near_strong
                # 模糊或放大過的圖，邊緣的漸變不只一個像素寬：再往外長兩圈，但覆蓋率必須一路遞減
                # （中間隔著一格底色、再變深的是另一條線，不會被接過來）
                tl = np.full((Y1 - Y0 + 2, X1 - X0 + 2), -1.0, dtype=np.float32)
                for _grow in range(2):
                    tl[:] = -1.0
                    tl[ys_a[acc] - Y0 + 1, xs_a[acc] - X0 + 1] = t[acc]
                    nb_max = cv2.dilate(tl, np.ones((3, 3), np.uint8))[ys_a - Y0 + 1, xs_a - X0 + 1]
                    more = ok & ~acc & (t < 0.5) & (nb_max >= t)
                    if not more.any():
                        break
                    acc |= more
                ok = acc
                rid = len(regions)
                regions.append(RegionModel("flat", Ct.astype(np.float64), thin=True))
                made.append((rid, (Ct.astype(np.float32)[None, :] @ M.T)[0]))
                own[ok] = rid
                own_t[ok] = t[ok]
                remaining &= ~ok
                got = True
            sel = own >= 0
            if sel.any():
                yy, xx, tt, rr = ys_a[sel], xs_a[sel], own_t[sel], own[sel].astype(np.int32)
                hi = tt >= 0.5
                prev = L1[yy, xx]
                labels[yy[hi], xx[hi]] = rr[hi]
                second[yy[hi], xx[hi]] = prev[hi]
                alpha[yy[hi], xx[hi]] = tt[hi]
                lo_ = ~hi
                labels[yy[lo_], xx[lo_]] = prev[lo_]
                second[yy[lo_], xx[lo_]] = rr[lo_]
                alpha[yy[lo_], xx[lo_]] = 1.0 - tt[lo_]
            leftover += int(remaining[:n_m].sum()) if got else n_m
        stats["leftover_px"] = leftover
        stats["absorbed_px"] = absorbed
        stats["features"] = int(sum(1 for r in regions if r.thin))
    # ---------- 5. 相鄰同色合併
    labels, second, regions = _merge_same(labels, second, regions, max(float(P["merge_de"]), 2.5 * sigma * 0.6), P)
    alpha = np.where(second < 0, np.float32(1.0), alpha).astype(np.float32)
    # ---------- 6. 區域統計與邊緣寬度
    n_reg = len(regions)
    area = np.bincount(labels.ravel(), minlength=n_reg)
    ys_all, xs_all = np.nonzero(np.ones((H, W), dtype=bool))
    order = np.argsort(labels.ravel(), kind="stable")
    bounds = np.concatenate([[0], np.cumsum(area)])
    flat_lab = labels.ravel()
    for rid, r in enumerate(regions):
        r.area = int(area[rid])
        if r.area == 0:
            continue
        idx = order[bounds[rid] : bounds[rid + 1]]
        yy, xx = idx // W, idx % W
        r.bbox = (int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1)
    del ys_all, xs_all, flat_lab
    trans = int((labels[:, 1:] != labels[:, :-1]).sum() + (labels[1:, :] != labels[:-1, :]).sum())
    partial = int(((second >= 0) & (alpha < 0.9)).sum())
    ratio = partial / max(trans, 1)
    blur = max(0.0, ratio - 1.1) / 2.5
    stats["edge_ratio"] = round(ratio, 3)
    stats["regions"] = n_reg
    return Segmentation(labels=labels, second=second, alpha=alpha, regions=regions, sigma=sigma, blur_px=blur, stats=stats)


def _rejected(H: int, W: int, sigma: float, stats: dict, why: str) -> Segmentation:
    z = np.zeros((H, W), dtype=np.int32)
    return Segmentation(labels=z, second=z - 1, alpha=np.ones((H, W), dtype=np.float32), regions=[], sigma=sigma, blur_px=0.0, reject=why, stats=stats)


def _split_colors(lab_px: np.ndarray, de: float, kmax: int) -> np.ndarray:
    """決定性的色彩分群：從整體平均色開始，每次把散得最開的一群沿主軸切成兩半，直到每群內的色差夠小。"""
    assign = np.zeros(len(lab_px), dtype=np.int32)
    k = 1
    while k < kmax:
        worst, worst_spread = -1, de
        for b in range(k):
            m = assign == b
            if m.sum() < 8:
                continue
            px = lab_px[m]
            spread = float(np.percentile(np.sqrt(((px - px.mean(axis=0)) ** 2).sum(axis=1)), 90))
            if spread > worst_spread:
                worst, worst_spread = b, spread
        if worst < 0:
            break
        m = np.nonzero(assign == worst)[0]
        px = lab_px[m].astype(np.float64)
        c = px.mean(axis=0)
        _, _, vt = np.linalg.svd((px - c)[:: max(1, len(px) // 5000)], full_matrices=False)
        proj = (px - c) @ vt[0]
        # 一維的兩群 k-means（由中位數起步，固定迭代次數）
        thr = float(np.median(proj))
        for _ in range(8):
            a, b2 = proj[proj <= thr], proj[proj > thr]
            if len(a) == 0 or len(b2) == 0:
                break
            thr = 0.5 * (float(a.mean()) + float(b2.mean()))
        assign[m[proj > thr]] = k
        k += 1
    return assign


def _merge_same(labels: np.ndarray, second: np.ndarray, regions: list[RegionModel], de: float, P: dict):
    """相鄰而顏色相同（ΔE 在容許內）的純色區域合併；漸層區域不參與。回傳重新編號後的結果。"""
    n = len(regions)
    if n < 2:
        return labels, second, regions
    a = np.concatenate([labels[:, 1:].ravel(), labels[1:, :].ravel()])
    b = np.concatenate([labels[:, :-1].ravel(), labels[:-1, :].ravel()])
    diff = a != b
    pairs = np.unique(np.minimum(a[diff], b[diff]).astype(np.int64) * n + np.maximum(a[diff], b[diff]))
    lab_tab = cv2.cvtColor((np.stack([r.color for r in regions])[None] / 255.0).astype(np.float32), cv2.COLOR_RGB2Lab)[0]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    area = np.bincount(labels.ravel(), minlength=n)
    for p in pairs:
        i, j = int(p // n), int(p % n)
        ri, rj = regions[i], regions[j]
        if ri.kind != "flat" or rj.kind != "flat":
            continue
        tol = de if not (ri.thin or rj.thin) else max(de, float(P["merge_de_thin"]))
        if float(np.sqrt(((lab_tab[i] - lab_tab[j]) ** 2).sum())) <= tol:
            a_, b_ = find(i), find(j)
            if a_ != b_:
                keep, drop = (a_, b_) if area[a_] >= area[b_] else (b_, a_)
                parent[drop] = keep
    roots = sorted({find(i) for i in range(n)})
    if len(roots) == n:
        return labels, second, regions
    root_index = {r: k for k, r in enumerate(roots)}
    remap = np.array([root_index[find(i)] for i in range(n)], dtype=np.int32)
    new_regions = [regions[r] for r in roots]
    # 合併後的區域：顏色取面積最大的那一個；只有成員全是細節時才仍算細節
    all_thin = [True] * len(roots)
    for i in range(n):
        if not regions[i].thin:
            all_thin[remap[i]] = False
    for k, r in enumerate(new_regions):
        r.thin = all_thin[k]
    labels = remap[labels]
    sec = second.copy()
    has = sec >= 0
    sec[has] = remap[sec[has]]
    sec[has & (sec == labels)] = -1
    return labels, sec, new_regions
