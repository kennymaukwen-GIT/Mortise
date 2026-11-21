"""背景還原（③辨識 3B.13 用）：把文字、圖片、形狀擦掉之後，補回一張乾淨的底圖。

做法
    0. 前處理：remove_mask（True = 要擦掉）可再外擴 extra_dilate_px。未遮住的像素叫「已知像素」。
       色差一律用 CIELAB 的 ΔE76。雜訊 ΔE 由已知像素的 3×3 高通殘差（取低梯度的一半像素）以 MAD 估計。
       容許色差 tol = flat_tol_de + noise_tol_factor × 雜訊 ΔE（第 fit_percentile 百分位的殘差要在 tol 內）。
    1. 全頁模型判斷（對已知像素取樣，最多 max_fit_samples 點）：
       a. 純色（model="flat"）：各通道中位數；殘差在 tol 內 → 整張填這個顏色。
       b. 平滑漸層（model="gradient"）：以正規化座標 (u, v) ∈ [−1, 1] 的二元多項式（1 到 max_gradient_degree 次，
          取最低能過的次數）對 RGB 三通道做 Tukey 雙權重的迭代加權最小平方擬合（排除漏擦的殘跡）；
          殘差在 tol 內、且比純色至少好 gradient_min_gain_de → 整張用擬合曲面。參數記在 gradient。
       純色與漸層都是「量測」，不記入 generated_boxes。
    2. 都不符（紋理、照片、多塊不同底色）→ 分區處理：遮罩膨脹 group_px 後的連通區域為一組；
       每組取外圍 ring_px 寬的已知像素環：
       a. 環是純色 → 填環的中位色（局部純色，不算生成）；
       b. 環可用 local_degree 次多項式描述 → 填局部漸層（不算生成）；
       c. 否則 → 修補（算生成，記入 generated_boxes 與 generated_mask）。
       修補預設用 OpenCV 主程式庫 photo 模組的 cv2.inpaint（Telea 2004；可選 Navier–Stokes），
       先填完 a、b 再修補，讓修補看得到較多脈絡。修補區可加上與周圍同強度的雜訊（noise_match）。
       全部組都是 c → model="inpaint"；有 a／b 也有 c，或全頁不是單一模型但各組都是 a／b → model="mixed"。
    3. is_blank：輸出底圖對其中位色的第 99 百分位 ΔE ≤ blank_tol_de。
    4. 信心值：全頁模型依殘差／tol；分區時逐組計算（局部擬合依殘差；修補依遮罩半寬與周圍複雜度），
       以遮罩面積加權平均。生成面積比例記在 generated_fraction。

已知限制
    - OpenCV 的 Telea／NS 修補是由邊界往內擴散：大面積或有紋理的底會糊成一片（無法補出紋理、照片內容），
      本模組照實回報低信心並在 notes 標示「大面積修補」。
    - 規則花紋（格子、條紋）沒有做週期延伸。
    - 沒有做「擦除後再偵測殘留文字」（需要 OCR，由上層串接）。
    - 漸層模型是低次多項式：放射漸層在 3 次內近似；銳利的多段漸層會落到分區處理。
    - 不使用 PatchMatch（專利狀態不明）與擴散式模型（可能補出假文字）。

可單獨升級的點
    - 修補器：register_inpainter(name, fn)，fn(影像 RGB uint8, 遮罩 bool, params) -> 影像 RGB uint8，
      再以 params["inpaint_method"] = name 選用。預定接 MI-GAN（MIT）或法務確認後的 LaMa 的 ONNX 版，
      以 512 px 方塊、64 px 脈絡、羽化接縫處理（tile_size、tile_context 參數已預留）。
    - 背景分類可改用小模型（判斷紙張／照片／花紋）。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import cv2
import numpy as np

from .types import BackgroundResult

DEFAULT_PARAMS: dict = {
    # ---- 前處理 ----
    "extra_dilate_px": 0,  # 遮罩額外外擴（像素）；上游已略為膨脹時保持 0
    "min_known_frac": 0.01,  # 已知像素少於全頁的這個比例 → 無法判斷背景
    "max_fit_samples": 20000,  # 全頁擬合時最多取樣的已知像素數
    "seed": 0,  # 取樣與雜訊的亂數種子（結果可重現）
    # ---- 模型判斷 ----
    "flat_tol_de": 3.0,  # 判為純色或漸層的基本容許色差（ΔE76）
    "noise_tol_factor": 1.6,  # 容許色差另加「雜訊 ΔE × 這個係數」
    "fit_percentile": 90.0,  # 殘差取第幾百分位與容許值比較
    "max_gradient_degree": 3,  # 全頁漸層多項式的最高次數
    "gradient_min_gain_de": 1.0,  # 漸層的殘差至少要比純色小這麼多（ΔE）才採用漸層
    "tukey_c": 4.685,  # Tukey 雙權重常數（以 MAD 尺度為單位）
    "irls_iters": 6,  # 穩健擬合的迭代次數
    "radial_grid": 9,  # 放射漸層：中心點粗搜尋的格點數（每軸）
    "radial_refine_steps": 5,  # 放射漸層：中心點細搜尋的減半步數
    "simpler_model_slack_de": 0.3,  # 較簡單的漸層模型殘差只比最佳模型多這麼多（ΔE）時，選較簡單的
    # ---- 分區處理 ----
    "group_px": 4,  # 遮罩膨脹這麼多像素後相連的視為同一組
    "ring_px": 6,  # 每組外圍取多寬的已知像素環（像素）
    "ring_min_px": 24,  # 環上的已知像素少於這個 → 直接修補
    "local_tol_de": 2.6,  # 局部純色／漸層的基本容許色差（ΔE，量在平滑後的影像上）：比全頁嚴，局部擬合點少、容易把紋理誤當平滑
    "local_smooth_px": 5,  # 局部判斷前先對已知像素做這個邊長的方框平滑（像素），量「結構」而不是雜訊
    "local_noise_factor": 0.5,  # 局部容許色差另加「雜訊 ΔE × 這個係數」（平滑後殘留的雜訊）
    "local_degree": 2,  # 局部漸層的多項式次數上限
    "local_samples_per_term": 6,  # 局部擬合每個多項式項至少要有的環像素數
    # ---- 修補 ----
    "inpaint_method": "telea",  # "telea"、"ns"，或以 register_inpainter() 註冊的名稱
    "inpaint_radius": 5,  # OpenCV 修補的鄰域半徑（像素）
    "inpaint_max_side": 2600,  # 修補時影像長邊超過這個就先縮小修補再放大（速度考量）
    "tile_size": 512,  # 預留：模型修補的方塊邊長（像素）
    "tile_context": 64,  # 預留：模型修補方塊外帶的脈絡寬度（像素）
    "noise_match": True,  # 分區填補的像素是否加上與周圍同強度的雜訊
    "noise_match_min": 1.5,  # 周圍雜訊 σ（RGB 色階）低於這個就不加
    # ---- 判定與信心值 ----
    "blank_tol_de": 2.0,  # is_blank：輸出底圖對中位色的第 99 百分位 ΔE ≤ 這個
    "wide_mask_px": 10.0,  # 修補遮罩的半寬（像素）達到這個時，寬度項的信心值降為一半
    "large_inpaint_frac": 0.05,  # 修補面積超過全頁的這個比例就在 notes 標示大面積修補
}

_INPAINTERS: dict[str, Callable] = {}


def _cv_inpaint(flag: int) -> Callable:
    def run(img: np.ndarray, mask: np.ndarray, p: dict) -> np.ndarray:
        H, W = img.shape[:2]
        m8 = mask.astype(np.uint8) * 255
        s = min(1.0, p["inpaint_max_side"] / max(H, W))
        if s < 1.0:
            small = cv2.resize(img, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA)
            ms = cv2.resize(m8, (int(W * s), int(H * s)), interpolation=cv2.INTER_NEAREST)
            ms = cv2.dilate(ms, np.ones((3, 3), np.uint8))
            out = cv2.inpaint(small, ms, max(1.0, p["inpaint_radius"] * s), flag)
            out = cv2.resize(out, (W, H), interpolation=cv2.INTER_LINEAR)
            res = img.copy()
            res[mask] = out[mask]
            return res
        return cv2.inpaint(img, m8, float(p["inpaint_radius"]), flag)

    return run


_INPAINTERS["telea"] = _cv_inpaint(cv2.INPAINT_TELEA)
_INPAINTERS["ns"] = _cv_inpaint(cv2.INPAINT_NS)


def register_inpainter(name: str, fn: Callable) -> None:
    """替換點：註冊修補器 fn(影像 RGB uint8, 遮罩 bool, params) -> 影像 RGB uint8（例如 MI-GAN、LaMa 的 ONNX）。"""
    _INPAINTERS[name] = fn


def _p(params: dict | None) -> dict:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ============================================================ 小工具


def _lab(rgb: np.ndarray) -> np.ndarray:
    """RGB（0 到 255，任意形狀 (..., 3)）→ CIELAB（L 0 到 100）。"""
    a = np.asarray(rgb, dtype=np.float32).reshape(-1, 1, 3) / 255.0
    return cv2.cvtColor(np.clip(a, 0, 1), cv2.COLOR_RGB2Lab).reshape(np.shape(rgb))


def _basis(u: np.ndarray, v: np.ndarray, deg: int) -> np.ndarray:
    cols = [u**i * v**j for d in range(deg + 1) for i in range(d + 1) for j in [d - i]]
    return np.stack(cols, axis=1)


def _wls(A: np.ndarray, y: np.ndarray, w: Optional[np.ndarray] = None) -> np.ndarray:
    """加權最小平方（正規方程式，比 lstsq 的 SVD 快很多；欄位已正規化到 [−1, 1]，條件數可接受）。"""
    Aw = A if w is None else A * w[:, None]
    G = Aw.T @ A
    G += np.eye(G.shape[0]) * (1e-9 * np.trace(G) / G.shape[0] + 1e-12)
    return np.linalg.solve(G, Aw.T @ y)


def _robust_poly(u, v, rgb, deg, p) -> tuple[np.ndarray, np.ndarray]:
    """RGB 三通道的二元多項式 Tukey IRLS 擬合。回傳（係數 (k, 3)、擬合值 (N, 3)）。"""
    A = _basis(u, v, deg)
    w = np.ones(len(u))
    coef = np.zeros((A.shape[1], 3))
    for _ in range(p["irls_iters"]):
        coef = _wls(A, rgb, w)
        r = np.linalg.norm(rgb - A @ coef, axis=1)
        s = max(1.4826 * float(np.median(r)), 1.0)
        z = r / (p["tukey_c"] * s)
        w = np.where(z < 1, (1 - z * z) ** 2, 0.0) + 1e-6
    return coef, A @ coef


def _fit_radial(xs, ys, rgb, W, H, p) -> Optional[dict]:
    """放射漸層：顏色 = c0 + c1·r + c2·r²，r 是到中心的距離（÷ max(W, H)）。中心先粗格點搜尋再逐步減半細搜尋。"""
    D = float(max(W, H))
    X = (xs + 0.5) / D
    Y = (ys + 0.5) / D
    n = len(X)
    if n < 30:
        return None
    sub = np.linspace(0, n - 1, min(n, 4000)).astype(int)
    Xs, Ys, Cs = X[sub], Y[sub], rgb[sub]

    def cost(cx, cy, Xa, Ya, Ca):
        r = np.hypot(Xa - cx, Ya - cy)
        A = np.stack([np.ones_like(r), r, r * r], axis=1)
        coef = _wls(A, Ca)
        return float(np.abs(Ca - A @ coef).sum()), coef

    g = p["radial_grid"]
    wx, wy = W / D, H / D
    best = None
    for cx in np.linspace(-0.25 * wx, 1.25 * wx, g):
        for cy in np.linspace(-0.25 * wy, 1.25 * wy, g):
            c, _ = cost(cx, cy, Xs, Ys, Cs)
            if best is None or c < best[0]:
                best = (c, cx, cy)
    _, cx, cy = best
    step = 1.5 * max(wx, wy) / (g - 1)
    for _ in range(p["radial_refine_steps"]):
        step /= 2
        cand = [(cost(cx + dx, cy + dy, Xs, Ys, Cs)[0], cx + dx, cy + dy) for dx in (-step, 0, step) for dy in (-step, 0, step)]
        _, cx, cy = min(cand)
    # 最後以 Tukey IRLS 在全部樣本上擬合
    r = np.hypot(X - cx, Y - cy)
    A = np.stack([np.ones_like(r), r, r * r], axis=1)
    w = np.ones(n)
    coef = np.zeros((3, 3))
    for _ in range(p["irls_iters"]):
        coef = _wls(A, rgb, w)
        res = np.linalg.norm(rgb - A @ coef, axis=1)
        s_ = max(1.4826 * float(np.median(res)), 1.0)
        z = res / (p["tukey_c"] * s_)
        w = np.where(z < 1, (1 - z * z) ** 2, 0.0) + 1e-6
    return {"center": (float(cx), float(cy)), "coef": coef, "fit": A @ coef}


def _residual_de(lab_known: np.ndarray, fit_rgb: np.ndarray, pct: float) -> float:
    d = np.linalg.norm(lab_known - _lab(np.clip(fit_rgb, 0, 255)), axis=-1)
    return float(np.percentile(d, pct)) if d.size else 0.0


def _noise(img: np.ndarray, known: np.ndarray) -> tuple[float, np.ndarray]:
    """已知像素的雜訊：回傳（ΔE 尺度的雜訊、RGB 各通道 σ）。

    以「間隔 1 與間隔 2 的差分」分離雜訊與平滑紋理：對局部線性的訊號 g 加白雜訊 σ，
    Var(d1) = g² + 2σ²、Var(d2) = 4g² + 2σ²，所以 σ² = (4·Var(d1) − Var(d2)) / 6。變異數用 MAD 穩健估計。
    """
    def est(a: np.ndarray) -> np.ndarray:
        v1s, v2s = [], []
        for arr, kn in ((a, known), (np.swapaxes(a, 0, 1), known.T)):
            ok = kn[:, 2:] & kn[:, 1:-1] & kn[:, :-2]
            if ok.sum() < 50:
                continue
            d1 = (arr[:, 2:] - arr[:, 1:-1])[ok]
            d2 = (arr[:, 2:] - arr[:, :-2])[ok]
            v1s.append((1.4826 * np.median(np.abs(d1), axis=0)) ** 2)
            v2s.append((1.4826 * np.median(np.abs(d2), axis=0)) ** 2)
        if not v1s:
            return np.zeros(a.shape[2])
        v1, v2 = np.mean(v1s, axis=0), np.mean(v2s, axis=0)
        return np.sqrt(np.maximum(0.0, (4 * v1 - v2) / 6.0))

    sig_rgb = est(img.astype(np.float32))
    sig_lab = est(_lab(img))
    return float(np.linalg.norm(sig_lab)), sig_rgb.astype(np.float64)


def _is_blank(out: np.ndarray, p: dict) -> bool:
    lab = _lab(out[:: max(1, out.shape[0] // 400), :: max(1, out.shape[1] // 400)])
    med = np.median(lab.reshape(-1, 3), axis=0)
    d = np.linalg.norm(lab.reshape(-1, 3) - med, axis=1)
    return bool(np.percentile(d, 99) <= p["blank_tol_de"])


# ============================================================ 公開介面


def reconstruct_background(image: np.ndarray, remove_mask: np.ndarray, params: dict | None = None) -> BackgroundResult:
    """擦掉 remove_mask（True = 要擦掉）的像素並補回底圖。"""
    p = _p(params)
    img = np.ascontiguousarray(image[..., :3]).astype(np.uint8)
    H, W = img.shape[:2]
    mask = np.asarray(remove_mask, dtype=bool)
    if mask.shape != (H, W):
        raise ValueError(f"remove_mask 形狀 {mask.shape} 與影像 {(H, W)} 不符")
    if p["extra_dilate_px"] > 0:
        k = 2 * int(p["extra_dilate_px"]) + 1
        mask = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    known = ~mask
    notes: list[str] = []
    n_known = int(known.sum())
    rng = np.random.default_rng(p["seed"])

    if n_known < max(16, p["min_known_frac"] * H * W):
        if n_known > 0:
            col = tuple(int(v) for v in np.median(img[known], axis=0))
        else:
            col = (255, 255, 255)
        out = np.empty_like(img)
        out[:] = col
        return BackgroundResult(
            image=out, model="flat", flat_color=col, generated_boxes=[(0, 0, W, H)], is_blank=True,
            notes=["幾乎整頁都被遮住，看不到背景；以可見像素的中位色（或白色）填滿，此判斷不可靠"],
            confidence=0.05, generated_mask=np.ones((H, W), bool), generated_fraction=1.0, fit_residual=None,
        )

    noise_de, noise_rgb = _noise(img, known)
    tol = p["flat_tol_de"] + p["noise_tol_factor"] * noise_de
    tol_local = p["local_tol_de"] + p["local_noise_factor"] * noise_de
    ys, xs = np.nonzero(known)
    if len(ys) > p["max_fit_samples"]:
        sel = rng.choice(len(ys), p["max_fit_samples"], replace=False)
        ys, xs = ys[sel], xs[sel]
    rgb_k = img[ys, xs].astype(np.float64)
    lab_k = _lab(rgb_k)
    known_frac = n_known / float(H * W)
    f_known = min(1.0, known_frac / 0.2) ** 0.3

    # ---- 1a. 純色
    flat = np.median(rgb_k, axis=0)
    r_flat = _residual_de(lab_k, np.broadcast_to(flat, rgb_k.shape), p["fit_percentile"])
    if r_flat <= tol:
        col = tuple(int(round(v)) for v in flat)
        out = np.empty_like(img)
        out[:] = col
        conf = (1.0 - 0.5 * (r_flat / tol) ** 2) * f_known
        return BackgroundResult(
            image=out, model="flat", flat_color=col, generated_boxes=[], is_blank=True,
            notes=[f"純色底：殘差 ΔE {r_flat:.2f}（容許 {tol:.2f}，雜訊 ΔE {noise_de:.2f}）"],
            confidence=float(conf), generated_mask=np.zeros((H, W), bool), generated_fraction=0.0, fit_residual=r_flat,
        )

    # ---- 1b. 平滑漸層（多項式 1 到 max_gradient_degree 次、放射漸層；取能過的最簡單者）
    u = (xs + 0.5) / W * 2 - 1
    v = (ys + 0.5) / H * 2 - 1
    cands = []  # （複雜度, 殘差, 描述, 產生整張圖的函數, 參數）
    for deg in range(1, p["max_gradient_degree"] + 1):
        coef, fit = _robust_poly(u, v, rgb_k, deg, p)
        r = _residual_de(lab_k, fit, p["fit_percentile"])
        cplx = {1: 1, 2: 3, 3: 4}.get(deg, deg + 1)
        cands.append((cplx, r, f"{deg} 次多項式", ("poly", deg, coef)))
    rad = _fit_radial(xs, ys, rgb_k, W, H, p)
    if rad is not None:
        r = _residual_de(lab_k, rad["fit"], p["fit_percentile"])
        cands.append((2, r, "放射漸層", ("radial", rad)))
    ok = [c for c in cands if c[1] <= tol and r_flat - c[1] >= p["gradient_min_gain_de"]]
    if ok:
        best_r = min(c[1] for c in ok)
        cplx, r, desc, spec = min((c for c in ok if c[1] <= best_r + p["simpler_model_slack_de"]), key=lambda c: c[0])
        gy, gx = np.mgrid[0:H, 0:W]
        if spec[0] == "poly":
            deg, coef = spec[1], spec[2]
            uu = ((gx + 0.5) / W * 2 - 1).ravel()
            vv = ((gy + 0.5) / H * 2 - 1).ravel()
            vals = _basis(uu, vv, deg) @ coef
            grad = {"type": "polynomial", "degree": deg, "coeffs": coef.T.tolist(), "basis": "u^i·v^j（i+j ≤ degree，依總次數再依 i 遞增），u=2(x+0.5)/W−1、v=2(y+0.5)/H−1"}
        else:
            rd = spec[1]
            D = float(max(W, H))
            rr = np.hypot((gx + 0.5) / D - rd["center"][0], (gy + 0.5) / D - rd["center"][1]).ravel()
            vals = np.stack([np.ones_like(rr), rr, rr * rr], axis=1) @ rd["coef"]
            grad = {"type": "radial", "degree": 2, "center_px": [rd["center"][0] * D, rd["center"][1] * D], "coeffs": rd["coef"].T.tolist(), "basis": "1、r、r²；r = 到中心的距離 ÷ max(W, H)"}
        out = np.round(np.clip(vals, 0, 255)).reshape(H, W, 3).astype(np.uint8)
        conf = (1.0 - 0.5 * (r / tol) ** 2) * f_known
        blank = _is_blank(out, p)
        return BackgroundResult(
            image=out, model="gradient", flat_color=None, generated_boxes=[], is_blank=blank,
            notes=[f"漸層底：{desc}，殘差 ΔE {r:.2f}（容許 {tol:.2f}；純色殘差 {r_flat:.2f}）"],
            confidence=float(conf), generated_mask=np.zeros((H, W), bool), generated_fraction=0.0, fit_residual=r,
            gradient=grad,
        )

    # ---- 2. 分區處理
    out = img.copy()
    gen_mask = np.zeros((H, W), bool)
    regions: list[dict] = []
    if not mask.any():
        notes.append(f"背景不是純色或漸層（純色殘差 ΔE {r_flat:.2f} > 容許 {tol:.2f}），但沒有要擦的東西：保留原圖")
        return BackgroundResult(
            image=out, model="inpaint", flat_color=None, generated_boxes=[], is_blank=False, notes=notes,
            confidence=1.0, generated_mask=gen_mask, generated_fraction=0.0, fit_residual=r_flat,
        )
    gk = 2 * int(p["group_px"]) + 1
    grouped = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gk, gk)))
    n_lbl, lbl, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    rk = 2 * int(p["ring_px"]) + 1
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rk, rk))
    pad = int(p["ring_px"]) + 2
    to_inpaint = np.zeros((H, W), bool)
    pending = []
    # 已知像素的正規化方框平滑（Lab）：局部判斷只看結構，雜訊（含 JPEG 造成的相關雜訊）大多被平均掉
    ks = max(1, int(p["local_smooth_px"]))
    kn = known.astype(np.float32)
    lab_full = _lab(img)
    den = cv2.boxFilter(kn, -1, (ks, ks), normalize=True)
    lab_s = cv2.boxFilter(lab_full * kn[..., None], -1, (ks, ks), normalize=True) / np.maximum(den, 1e-6)[..., None]
    for g in range(1, n_lbl):
        x, y, w, h, _ = stats[g]
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        reg = lbl[y0:y1, x0:x1] == g
        fill = reg & mask[y0:y1, x0:x1]
        if not fill.any():
            continue
        ring = (cv2.dilate(reg.astype(np.uint8), ring_kernel) > 0) & known[y0:y1, x0:x1] & ~reg
        ring |= reg & known[y0:y1, x0:x1]  # 組內未遮住的縫隙也是脈絡
        ry, rx = np.nonzero(ring)
        n_fill = int(fill.sum())
        dist = cv2.distanceTransform(fill.astype(np.uint8), cv2.DIST_L2, 3)
        half_w = float(dist.max())
        rec = {"bbox": (int(x0), int(y0), int(x1), int(y1)), "pixels": n_fill, "half_width_px": half_w}
        if len(ry) < p["ring_min_px"]:
            rec.update({"method": "inpaint", "residual": None})
            pending.append((rec, (y0, y1, x0, x1), fill, None))
            continue
        ring_rgb = img[y0:y1, x0:x1][ry, rx].astype(np.float64)
        ring_lab = lab_s[y0:y1, x0:x1][ry, rx].astype(np.float64)
        med = np.median(ring_rgb, axis=0)
        r_f = _residual_de(ring_lab, np.broadcast_to(med, ring_rgb.shape), p["fit_percentile"])
        crop = out[y0:y1, x0:x1]
        if r_f <= tol_local:
            crop[fill] = np.round(med).astype(np.uint8)
            rec.update({"method": "local_flat", "residual": r_f, "confidence": 0.9 * (1 - 0.5 * (r_f / tol_local) ** 2)})
            regions.append(rec)
            continue
        cw, ch = x1 - x0, y1 - y0
        uu = (rx + 0.5) / cw * 2 - 1
        vv = (ry + 0.5) / ch * 2 - 1
        done = False
        for deg in range(1, p["local_degree"] + 1):
            n_terms = (deg + 1) * (deg + 2) // 2
            if len(ry) < p["local_samples_per_term"] * n_terms:
                break
            coef, fit = _robust_poly(uu, vv, ring_rgb, deg, p)
            r_g = _residual_de(ring_lab, fit, p["fit_percentile"])
            if r_g <= tol_local:
                fy, fx = np.nonzero(fill)
                vals = _basis((fx + 0.5) / cw * 2 - 1, (fy + 0.5) / ch * 2 - 1, deg) @ coef
                crop[fy, fx] = np.clip(np.round(vals), 0, 255).astype(np.uint8)
                rec.update({"method": "local_gradient", "residual": r_g, "confidence": 0.85 * (1 - 0.5 * (r_g / tol_local) ** 2)})
                regions.append(rec)
                done = True
                break
        if not done:
            rec.update({"method": "inpaint", "residual": r_f})
            pending.append((rec, (y0, y1, x0, x1), fill, r_f))
    fill_all = mask.copy()
    if pending:
        for rec, (y0, y1, x0, x1), fill, _ in pending:
            to_inpaint[y0:y1, x0:x1] |= fill
        fn = _INPAINTERS.get(p["inpaint_method"])
        if fn is None:
            raise ValueError(f"未註冊的修補器：{p['inpaint_method']}")
        inp = fn(out, to_inpaint, p)
        out[to_inpaint] = inp[to_inpaint]
        gen_mask |= to_inpaint
        for rec, _, _, r_ring in pending:
            f_w = 1.0 / (1.0 + (rec["half_width_px"] / p["wide_mask_px"]) ** 2)
            f_c = 1.0 if r_ring is None else 1.0 / (1.0 + r_ring / (3.0 * tol_local))
            rec["confidence"] = 0.1 + 0.6 * f_w * f_c
            regions.append(rec)
    if p["noise_match"] and float(noise_rgb.max()) >= p["noise_match_min"]:
        nz = rng.standard_normal((int(fill_all.sum()), 3)) * noise_rgb[None, :]
        out[fill_all] = np.clip(out[fill_all].astype(np.float64) + nz, 0, 255).astype(np.uint8)
        notes.append(f"填補區已加上與周圍同強度的雜訊（σ≈{float(noise_rgb.mean()):.1f} 色階）")
    methods = {r["method"] for r in regions}
    if methods == {"inpaint"}:
        model = "inpaint"
    else:
        model = "mixed"
    gen_boxes = [r["bbox"] for r in regions if r["method"] == "inpaint"]
    px = np.array([r["pixels"] for r in regions], dtype=np.float64)
    cf = np.array([r["confidence"] for r in regions], dtype=np.float64)
    conf = float((px * cf).sum() / max(px.sum(), 1.0)) * f_known
    gen_frac = float(gen_mask.mean())
    notes.insert(0, f"背景不是單一純色或漸層（純色殘差 ΔE {r_flat:.2f} > 容許 {tol:.2f}）：分 {len(regions)} 區處理")
    cnt = {m: sum(1 for r in regions if r["method"] == m) for m in ("local_flat", "local_gradient", "inpaint")}
    notes.append(f"局部純色 {cnt['local_flat']} 區、局部漸層 {cnt['local_gradient']} 區、修補 {cnt['inpaint']} 區（生成 {100 * gen_frac:.1f}% 的頁面）")
    if gen_frac >= p["large_inpaint_frac"]:
        notes.append(f"大面積修補（OpenCV {p['inpaint_method']}），紋理與照片內容無法重建，品質有限；建議改接修補模型")
    blank = _is_blank(out, p)
    return BackgroundResult(
        image=out, model=model, flat_color=None, generated_boxes=gen_boxes, is_blank=blank, notes=notes,
        confidence=conf, generated_mask=gen_mask, generated_fraction=gen_frac, fit_residual=r_flat, regions=regions,
    )
