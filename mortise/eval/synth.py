"""合成干擾：把乾淨的頁面影像變成「像截圖、像掃描、像手機翻拍」的影像，並交出精確的座標對應。

    degraded, matrix, info = degrade(image, "photo", seed=7, strength=1.0)

- image：RGB 的 numpy uint8 陣列（通常是原生 PDF 畫出來的工作影像）。
- matrix：3×3 單應矩陣（numpy float64），把「原影像像素座標」對到「干擾後影像像素座標」。
  座標約定與 recognition/types.py 相同：原點在左上角的「像素角落」、x 向右、y 向下、浮點數；
  所以第 i 個像素涵蓋 [i, i+1)，中心是 i + 0.5。頁面相對座標乘上影像寬度就是這個座標。
- info：說明 dict（可直接寫成 JSON）：這次實際抽到的每一個參數，方便重現與分析。

同一個 (kind, seed, strength, params) 一定產生位元完全相同的結果（亂數只來自 numpy 的
default_rng([seed, kind 編號])；OpenCV 的縮放、扭曲與 JPEG 編碼都是決定性的）。

三種干擾（strength 是強度倍數：0 幾乎不加干擾、1 是一般情況、2 是很糟的情況）：

screenshot（截圖）
  1. 重新取樣到 96 至 144 dpi 的等效解析度（cv2.resize，縮小用 INTER_AREA 模擬螢幕渲染的反鋸齒；
     強度越高，dpi 下限越低）。矩陣是純縮放 diag(W'/W, H'/H)。
  2. 依機率做 JPEG 壓縮（截圖常被聊天軟體轉成 JPEG）。

scan（掃描）
  1. 依掃描 dpi 縮放；2. 輕微旋轉（±1.5° × 強度），畫布擴大以容納整頁，空白處填掃描器底色；
  3. 紙張色調、對比、亮度、gamma 變化；4. 輕微高斯模糊（光學）；5. 高斯雜訊；
  6. 依機率轉灰階（黑白掃描）；7. JPEG 壓縮。矩陣是「縮放＋旋轉＋平移」的仿射矩陣。

photo（手機翻拍）
  1. 縮放到照片中的頁面解析度；2. 透視變形（四角各自隨機偏移＋整體旋轉）後貼到程序產生的
     紋理背景（桌面、木紋、布紋）上，頁緣有軟陰影；3. 光照漸層（線性＋暗角）；
  4. 依機率加一塊柔邊陰影（手或手機的影子）；5. 白平衡偏色；6. 高斯模糊或輕微動態模糊；
  7. 雜訊（亮度＋色度）；8. JPEG 壓縮。矩陣是單應矩陣（cv2.getPerspectiveTransform 算出）。

已知限制：
- 沒有模擬：鏡頭桶形變形（非線性，3×3 矩陣表達不了）、紙張彎曲、摩爾紋、反光高光、
  手震造成的方向性模糊（只有輕微的水平動態模糊）、掃描的裝訂陰影與透背。
- 背景紋理是程序產生的，不是真實照片；真實照片樣本到了之後應以真實樣本為準。
- OpenCV 的 warp 函數用「像素中心」約定，本模組內部已換算（_to_cv），對外一律是像素角落約定。

可單獨升級的點：
- 背景紋理可換成真實桌面照片庫（放一個目錄，依 seed 抽一張）。
- 可加入非線性變形（紙張彎曲、鏡頭變形）；那時矩陣要改成座標對應函數或位移場。
- 各種干擾的參數範圍都在 DEFAULT_PARAMS，等真實樣本到了可以照統計值校正。
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

KINDS = ("screenshot", "scan", "photo")

DEFAULT_PARAMS: dict[str, Any] = {
    # ---- 共同 ----
    "source_dpi": 150.0,  # 輸入影像的解析度（dpi）；工作影像預設 150 dpi（見設定檔 [page] working_dpi）
    "min_dpi": 48.0,  # 強度再高，等效解析度也不低於此值（dpi）
    # ---- screenshot ----
    "screenshot_dpi_min": 96.0,  # 截圖等效解析度下限（dpi，強度 1 時）
    "screenshot_dpi_max": 144.0,  # 截圖等效解析度上限（dpi）
    "screenshot_jpeg_prob": 0.5,  # 截圖做 JPEG 壓縮的機率（0 到 1）
    "screenshot_jpeg_quality": (75, 95),  # 截圖 JPEG 品質範圍（1 到 100；強度越高越靠近下限再往下）
    # ---- scan ----
    "scan_dpi": (120.0, 200.0),  # 掃描解析度範圍（dpi）
    "scan_max_angle_deg": 1.5,  # 最大歪斜角度（度，乘上強度）
    "scan_margin": (0.0, 0.02),  # 頁面外多掃到的邊（頁寬比例）
    "scan_bed_gray": (215, 245),  # 掃描器底色的灰階值範圍（0 到 255）
    "scan_contrast": (0.8, 1.0),  # 對比倍數範圍（1 為不變；強度越高越低）
    "scan_brightness": (-12.0, 12.0),  # 亮度偏移範圍（灰階值）
    "scan_gamma": (0.85, 1.2),  # gamma 範圍
    "scan_paper_tint": 0.06,  # 紙張偏黃的最大幅度（比例，藍色通道最多降低此比例）
    "scan_blur_sigma": (0.3, 0.9),  # 光學模糊的高斯 sigma 範圍（像素，乘上強度）
    "scan_noise_sigma": (2.0, 6.0),  # 雜訊標準差範圍（灰階值，乘上強度）
    "scan_gray_prob": 0.4,  # 轉成灰階的機率
    "scan_jpeg_prob": 0.8,  # 做 JPEG 壓縮的機率
    "scan_jpeg_quality": (60, 90),  # JPEG 品質範圍
    # ---- photo ----
    "photo_page_scale": (0.55, 0.9),  # 照片中頁面寬度 ÷ 原影像寬度（決定照片中的等效 dpi）
    "photo_page_fill": (0.62, 0.85),  # 頁面佔畫布寬（或高）的比例範圍
    "photo_corner_jitter": 0.035,  # 透視：四角隨機偏移的標準差（頁寬比例，乘上強度）
    "photo_max_rotation_deg": 8.0,  # 整體旋轉的最大角度（度，乘上強度）
    "photo_keystone": 0.06,  # 梯形變形（上緣相對下緣縮短）的最大比例（乘上強度）
    "photo_light_gradient": (0.15, 0.4),  # 光照漸層的最大衰減範圍（比例，乘上強度）
    "photo_vignette": 0.15,  # 暗角最大衰減（比例，乘上強度）
    "photo_shadow_prob": 0.7,  # 加上柔邊陰影的機率
    "photo_shadow_strength": (0.2, 0.45),  # 陰影處的亮度衰減範圍（比例，乘上強度）
    "photo_white_balance": 0.08,  # 白平衡偏色：每個通道倍數偏離 1 的最大比例
    "photo_blur_sigma": (0.4, 1.2),  # 高斯模糊 sigma 範圍（像素，乘上強度）
    "photo_motion_prob": 0.3,  # 加上輕微水平動態模糊的機率
    "photo_motion_px": (3, 7),  # 動態模糊長度範圍（像素）
    "photo_noise_sigma": (3.0, 8.0),  # 亮度雜訊標準差範圍（灰階值，乘上強度）
    "photo_chroma_noise": 0.4,  # 色度雜訊相對亮度雜訊的比例
    "photo_jpeg_quality": (60, 88),  # JPEG 品質範圍（照片一定是 JPEG）
    "photo_drop_shadow_px": 12.0,  # 紙張在背景上的軟陰影寬度（像素）
}


def _p(params: dict | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ---------------------------------------------------------------- 矩陣工具


def translate(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])


def scale(sx: float, sy: float | None = None) -> np.ndarray:
    return np.diag([sx, sx if sy is None else sy, 1.0])


def rotate_deg(angle: float) -> np.ndarray:
    """繞原點旋轉（y 向下，所以正角度在畫面上是順時針）。"""
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def apply_h(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """把 (N, 2) 的點用 3×3 矩陣轉換（含透視除法）。"""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    hom = np.hstack([pts, np.ones((len(pts), 1))]) @ np.asarray(matrix, dtype=np.float64).T
    return hom[:, :2] / hom[:, 2:3]


def _to_cv(matrix: np.ndarray) -> np.ndarray:
    """像素角落約定的矩陣 → OpenCV warp 用的像素中心約定。"""
    return translate(-0.5, -0.5) @ matrix @ translate(0.5, 0.5)


def _range(rng: np.random.Generator, r: Any) -> float:
    lo, hi = r
    return float(rng.uniform(lo, hi)) if hi > lo else float(lo)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:  # 不應發生；保守起見原樣交回
        return img
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _quality(rng: np.random.Generator, r: Any, strength: float) -> int:
    lo, hi = r
    hi_eff = hi - (hi - lo) * max(0.0, strength - 1.0) * 0.5  # 強度超過 1：品質上限往下壓
    lo_eff = max(15.0, lo - (hi - lo) * max(0.0, strength - 1.0))
    q = rng.uniform(lo_eff, max(lo_eff, hi_eff)) if strength > 0 else hi
    return int(round(q))


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    interp = cv2.INTER_AREA if w < img.shape[1] else cv2.INTER_CUBIC
    return cv2.resize(img, (w, h), interpolation=interp)


def _clip_u8(a: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(a), 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 主函數


def degrade(image: np.ndarray, kind: str, seed: int, strength: float = 1.0, params: dict | None = None) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """加上一種干擾。回傳（干擾後影像、3×3 矩陣：原影像座標 → 干擾後影像座標、說明 dict）。"""
    if kind not in KINDS:
        raise ValueError(f"不認得的干擾種類：{kind}（可用：{', '.join(KINDS)}）")
    img = np.ascontiguousarray(image[..., :3] if image.ndim == 3 else np.repeat(image[..., None], 3, axis=2)).astype(np.uint8)
    p = _p(params)
    strength = max(0.0, float(strength))
    rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, KINDS.index(kind)])
    info: dict[str, Any] = {"kind": kind, "seed": int(seed), "strength": strength, "source_size": [int(img.shape[1]), int(img.shape[0])]}
    fn = {"screenshot": _screenshot, "scan": _scan, "photo": _photo}[kind]
    out, matrix = fn(img, rng, strength, p, info)
    info["output_size"] = [int(out.shape[1]), int(out.shape[0])]
    # 頁面在輸出影像中的等效解析度：以頁面中心的局部縮放估計
    c = np.array([[img.shape[1] / 2, img.shape[0] / 2]])
    d = apply_h(matrix, np.vstack([c, c + [1.0, 0.0], c + [0.0, 1.0]]))
    u, v = d[1] - d[0], d[2] - d[0]
    local = math.sqrt(abs(u[0] * v[1] - u[1] * v[0]))
    info["effective_dpi"] = float(p["source_dpi"] * local)
    return out, matrix, info


def _screenshot(img, rng, strength, p, info):
    h, w = img.shape[:2]
    hi = float(p["screenshot_dpi_max"])
    lo = max(float(p["min_dpi"]), hi - (hi - float(p["screenshot_dpi_min"])) * strength)
    dpi = float(rng.uniform(lo, hi)) if hi > lo else hi
    s = dpi / float(p["source_dpi"])
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    out = _resize(img, nw, nh)
    matrix = scale(nw / w, nh / h)
    use_jpeg = bool(rng.random() < p["screenshot_jpeg_prob"]) and strength > 0
    q = _quality(rng, p["screenshot_jpeg_quality"], strength)
    if use_jpeg:
        out = _jpeg(out, q)
    info.update({"dpi": dpi, "jpeg_quality": q if use_jpeg else None})
    return out, matrix


def _scan(img, rng, strength, p, info):
    h, w = img.shape[:2]
    dpi = max(float(p["min_dpi"]), _range(rng, p["scan_dpi"]))
    s = dpi / float(p["source_dpi"])
    angle = float(rng.uniform(-1.0, 1.0)) * float(p["scan_max_angle_deg"]) * strength
    margin = _range(rng, p["scan_margin"]) * w * s
    bed = int(round(_range(rng, p["scan_bed_gray"])))
    # 縮放＋旋轉（繞頁面中心），再平移讓整頁落在畫布內
    m = rotate_deg(angle) @ translate(-w / 2, -h / 2)
    m = scale(s) @ m
    corners = apply_h(m, np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=float))
    x0, y0 = corners.min(axis=0) - margin
    x1, y1 = corners.max(axis=0) + margin
    cw, ch = int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0))
    matrix = translate(-x0, -y0) @ m
    out = cv2.warpAffine(img, _to_cv(matrix)[:2], (cw, ch), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(bed, bed, bed))
    f = out.astype(np.float32)
    # 紙張色調、對比、亮度、gamma
    tint = float(rng.uniform(0.0, p["scan_paper_tint"])) * min(1.0, strength)
    f *= np.array([1.0, 1.0 - tint * 0.4, 1.0 - tint], dtype=np.float32)
    lo_c, hi_c = p["scan_contrast"]
    contrast = 1.0 - (1.0 - float(rng.uniform(lo_c, hi_c))) * strength
    bright = _range(rng, p["scan_brightness"]) * min(1.0, strength)
    gamma = 1.0 + (_range(rng, p["scan_gamma"]) - 1.0) * strength
    f = (f - 128.0) * contrast + 128.0 + bright
    f = 255.0 * np.power(np.clip(f, 0, 255) / 255.0, gamma)
    sigma = _range(rng, p["scan_blur_sigma"]) * strength
    if sigma > 0.05:
        f = cv2.GaussianBlur(f, (0, 0), sigma)
    noise = _range(rng, p["scan_noise_sigma"]) * strength
    gray = bool(rng.random() < p["scan_gray_prob"]) and strength > 0
    if gray:
        g = f @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        g += rng.normal(0.0, noise, g.shape).astype(np.float32) if noise > 0 else 0.0
        f = np.repeat(g[..., None], 3, axis=2)
    elif noise > 0:
        f += rng.normal(0.0, noise, f.shape).astype(np.float32)
    out = _clip_u8(f)
    use_jpeg = bool(rng.random() < p["scan_jpeg_prob"]) and strength > 0
    q = _quality(rng, p["scan_jpeg_quality"], strength)
    if use_jpeg:
        out = _jpeg(out, q)
    info.update({"dpi": dpi, "angle_deg": angle, "margin_px": margin, "bed_gray": bed, "paper_tint": tint, "contrast": contrast,
                 "brightness": bright, "gamma": gamma, "blur_sigma": sigma, "noise_sigma": noise, "grayscale": gray, "jpeg_quality": q if use_jpeg else None})
    return out, matrix


def _texture(rng, w: int, h: int) -> tuple[np.ndarray, str]:
    """程序產生的背景紋理（桌面、木紋、布紋）。低解析度產生再放大，速度快。"""
    style = str(rng.choice(["desk", "wood", "fabric", "dark"]))
    base = {"desk": (150, 150, 145), "wood": (140, 100, 65), "fabric": (90, 105, 130), "dark": (45, 45, 50)}[style]
    base = np.array(base, dtype=np.float32) * rng.uniform(0.85, 1.15, 3).astype(np.float32)
    sw, sh = max(8, w // 8), max(8, h // 8)
    field = np.zeros((sh, sw), np.float32)
    for k, amp in ((4, 0.5), (16, 0.3), (64, 0.2)):
        small = rng.normal(0, 1, (max(2, sh * k // 64), max(2, sw * k // 64))).astype(np.float32)
        field += amp * cv2.resize(small, (sw, sh), interpolation=cv2.INTER_CUBIC)
    if style == "wood":
        yy = np.arange(sh, dtype=np.float32)[:, None]
        field = 0.6 * np.sin(yy * rng.uniform(0.15, 0.35) + 3.0 * field) + 0.4 * field
    field = cv2.resize(field, (w, h), interpolation=cv2.INTER_LINEAR)
    if style == "fabric":
        xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        field += 0.25 * np.sin(xx * 1.3) * np.sin(yy * 1.3)
    tex = base[None, None, :] * (1.0 + 0.12 * field[..., None])
    return tex.astype(np.float32), style


def _photo(img, rng, strength, p, info):
    h, w = img.shape[:2]
    s = _range(rng, p["photo_page_scale"])
    pw, ph = w * s, h * s
    fill = _range(rng, p["photo_page_fill"])
    cw, ch = int(round(pw / fill)), int(round(ph / fill))
    cx, cy = cw / 2 + rng.uniform(-0.25, 0.25) * (cw - pw), ch / 2 + rng.uniform(-0.25, 0.25) * (ch - ph)
    base = np.array([[-pw / 2, -ph / 2], [pw / 2, -ph / 2], [pw / 2, ph / 2], [-pw / 2, ph / 2]])
    key = float(rng.uniform(0.0, p["photo_keystone"])) * strength
    base[0, 0] += key * pw / 2
    base[1, 0] -= key * pw / 2
    jitter = rng.normal(0.0, p["photo_corner_jitter"] * strength * pw, (4, 2))
    angle = float(rng.uniform(-1.0, 1.0)) * p["photo_max_rotation_deg"] * strength
    dst = apply_h(translate(cx, cy) @ rotate_deg(angle), base + jitter)
    # 確保四角都在畫布內（留 2% 邊）
    lo = np.array([0.02 * cw, 0.02 * ch])
    hi = np.array([0.98 * cw, 0.98 * ch])
    shift = np.maximum(lo - dst.min(axis=0), 0) - np.maximum(dst.max(axis=0) - hi, 0)
    dst = dst + shift
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst.astype(np.float32)).astype(np.float64)
    page = cv2.warpPerspective(img, _to_cv(matrix), (cw, ch), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)).astype(np.float32)
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), _to_cv(matrix), (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    tex, style = _texture(rng, cw, ch)
    # 紙張在背景上的軟陰影
    ds = float(p["photo_drop_shadow_px"]) * max(0.3, strength)
    sh_mask = cv2.GaussianBlur(mask, (0, 0), ds) if ds > 0.1 else mask
    sh_mask = np.roll(sh_mask, (int(ds * 0.5), int(ds * 0.4)), axis=(0, 1))
    tex *= (1.0 - 0.45 * sh_mask)[..., None]
    f = page * mask[..., None] + tex * (1.0 - mask[..., None])
    # 光照漸層＋暗角
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    theta = rng.uniform(0, 2 * math.pi)
    t = ((xx - cw / 2) * math.cos(theta) + (yy - ch / 2) * math.sin(theta)) / (0.5 * math.hypot(cw, ch))
    grad = _range(rng, p["photo_light_gradient"]) * strength
    light = 1.0 - grad * (t + 1.0) / 2.0
    r2 = ((xx - cw / 2) / (cw / 2)) ** 2 + ((yy - ch / 2) / (ch / 2)) ** 2
    vig = float(p["photo_vignette"]) * strength
    light *= 1.0 - vig * np.clip(r2 / 2.0, 0, 1)
    # 柔邊陰影（手或手機）
    shadow = bool(rng.random() < p["photo_shadow_prob"]) and strength > 0
    sd = 0.0
    if shadow:
        sd = _range(rng, p["photo_shadow_strength"]) * min(1.5, strength)
        blob = np.zeros((ch, cw), np.float32)
        center = (int(rng.uniform(0, cw)), int(rng.uniform(0, ch)))
        axes = (int(rng.uniform(0.15, 0.4) * cw), int(rng.uniform(0.1, 0.3) * ch))
        cv2.ellipse(blob, center, axes, float(rng.uniform(0, 180)), 0, 360, 1.0, -1)
        blob = cv2.GaussianBlur(blob, (0, 0), max(4.0, 0.05 * cw))
        light *= 1.0 - sd * blob
    f *= light[..., None]
    wb = 1.0 + rng.uniform(-1, 1, 3).astype(np.float32) * float(p["photo_white_balance"]) * min(1.0, strength)
    f *= wb[None, None, :]
    sigma = _range(rng, p["photo_blur_sigma"]) * strength
    if sigma > 0.05:
        f = cv2.GaussianBlur(f, (0, 0), sigma)
    motion = bool(rng.random() < p["photo_motion_prob"]) and strength > 0
    mlen = 0
    if motion:
        mlen = int(rng.integers(p["photo_motion_px"][0], p["photo_motion_px"][1] + 1))
        k = np.zeros((mlen, mlen), np.float32)
        k[mlen // 2, :] = 1.0 / mlen
        f = cv2.filter2D(f, -1, k)  # 水平、對稱的核：不造成位移
    noise = _range(rng, p["photo_noise_sigma"]) * strength
    if noise > 0:
        f += rng.normal(0.0, noise, (ch, cw, 1)).astype(np.float32)
        f += rng.normal(0.0, noise * p["photo_chroma_noise"], (ch, cw, 3)).astype(np.float32)
    out = _clip_u8(f)
    q = _quality(rng, p["photo_jpeg_quality"], strength) if strength > 0 else 95
    out = _jpeg(out, q)
    info.update({"page_scale": s, "canvas": [cw, ch], "page_corners": dst.tolist(), "rotation_deg": angle, "keystone": key, "texture": style,
                 "light_gradient": grad, "vignette": vig, "shadow": shadow, "shadow_strength": sd, "white_balance": wb.tolist(),
                 "blur_sigma": sigma, "motion_blur_px": mlen, "noise_sigma": noise, "jpeg_quality": q})
    return out, matrix
