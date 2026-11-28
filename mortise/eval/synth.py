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

彎曲的紙（degrade_curved，非線性變形，交出逐像素對應圖而不是 3×3 矩陣）
    photo, page_map, info = degrade_curved(image, "curl_top", seed=3)

  page_map：H×W×2 float32，照片每個像素中心 → 頁面影像座標（像素角落約定）；不是這張紙的像素為 NaN。
  做法：頁面像素格點 (u, v) → 三維曲面 → 針孔相機投影 → 三角網格光柵化（由遠而近畫，處理捲起時的自我遮擋）
  → 每個照片像素得到 (u, v) 真值 → 以 2 倍超取樣取樣頁面再縮小（反鋸齒）→ 依法向量加 Lambert 明暗。
  曲面變形（可疊加）：
    flat         平的紙（只有相機透視），當對照組；
    bend         弓起（圓柱彎曲，軸線沿頁高或頁寬）；
    curl_top     上緣捲起（線外側的部分繞在半徑 r 的圓柱上，等長，轉到上限角度後沿切線直走）；
    corner_curl  捲角（同上，軸線斜切一個角）；
    crease       折痕（同上，半徑很小、角度小）；
    curl_crease  上緣捲起＋水平折痕＋輕微弓起。
  場景（依亂數或 params 指定）：與紙同色的背景（paper_like）、碰在一起的第二張紙、硬邊陰影、反光。
  另有 table_page()（內有大表格的原生 PDF 頁面）與 render_pdf_page()（原生 PDF → 影像＋文字層，
  給「以 PDF 文字層為答案的 CER」用）。

已知限制：
- 沒有模擬：鏡頭桶形變形、摩爾紋、手震造成的方向性模糊（只有輕微的水平動態模糊）、掃描的裝訂陰影與透背。
- 彎曲變形是幾個等長變形依序疊加，疊加處不是嚴格的等長曲面；紙的厚度、紙背的顏色（捲起後露出背面）沒有模擬。
- 背景紋理是程序產生的，不是真實照片；真實照片樣本到了之後應以真實樣本為準。
- OpenCV 的 warp 函數用「像素中心」約定，本模組內部已換算（_to_cv），對外一律是像素角落約定。

可單獨升級的點：
- 背景紋理可換成真實桌面照片庫（放一個目錄，依 seed 抽一張）。
- 鏡頭變形可以用 degrade_curved 同樣的逐像素對應圖表達。
- 各種干擾的參數範圍都在 DEFAULT_PARAMS、CURVED_PARAMS，等真實樣本到了可以照統計值校正。
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


def _texture(rng, w: int, h: int, style: str | None = None) -> tuple[np.ndarray, str]:
    """程序產生的背景紋理（桌面、木紋、布紋）。低解析度產生再放大，速度快。style 為 None 時依亂數挑。"""
    if style is None:
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


# ================================================================ 彎曲的紙（逐像素對應）

CURVED_KINDS = ("flat", "bend", "curl_top", "corner_curl", "crease", "curl_crease")

CURVED_PARAMS: dict[str, Any] = {
    "curved_out_long": 1600,  # 照片長邊（像素）
    "curved_grid_px": 6.0,  # 曲面三角網格的間距（頁面像素）；越小越精確、越慢
    "curved_margin": (0.08, 0.16),  # 頁面外的畫布邊（佔頁面投影外接框的比例）
    "curved_cam_dist": (1.2, 1.5),  # 相機距離（頁面長邊的倍數）
    "curved_tilt_deg": (10.0, 6.0, 6.0),  # 相機繞 x、y 傾斜與滾轉的最大角度（度，乘上強度）
    "curved_bend_k": (0.8, 1.6),  # 弓起：整頁轉過的角度（弧度，乘上強度）
    "curved_bend_axis_v_prob": 0.75,  # 弓起的軸線沿頁高（左右兩側翹起）的機率；否則沿頁寬
    "curved_curl_v": (0.08, 0.18),  # 上緣捲起：從頁頂往下多遠開始捲（佔頁高）
    "curved_curl_r": (0.03, 0.06),  # 捲起半徑（佔頁高）
    "curved_curl_deg": (50.0, 100.0),  # 捲起的角度上限（度，乘上強度）
    "curved_corner_cut": (0.15, 0.32),  # 捲角：軸線在兩條邊上的截距（佔頁寬）
    "curved_corner_r": (0.015, 0.04),  # 捲角半徑（佔頁高）
    "curved_corner_deg": (40.0, 95.0),  # 捲角角度上限（度，乘上強度）
    "curved_crease_pos": (0.35, 0.65),  # 折痕位置（佔頁高或頁寬）
    "curved_crease_deg": (5.0, 13.0),  # 折痕角度（度，乘上強度）
    "curved_background": None,  # 指定背景："paper_like"（與紙同色的布）、desk、wood、fabric、dark；None 依亂數
    "curved_paper_like_prob": 0.35,  # 背景與紙同色的機率
    "curved_second_sheet": None,  # 是否有碰在一起的第二張紙（True／False）；None 依機率
    "curved_second_sheet_prob": 0.35,  # 有第二張紙的機率
    "curved_hard_shadow": None,  # 是否加硬邊陰影（True／False）；None 依機率
    "curved_hard_shadow_prob": 0.3,  # 加硬邊陰影的機率
    "curved_shadow_strength": (0.25, 0.45),  # 陰影處的亮度衰減（比例）
    "curved_glare": None,  # 是否加反光（True／False）；None 依機率
    "curved_glare_prob": 0.15,  # 加反光的機率
    "curved_light_gradient": (0.1, 0.3),  # 光照漸層的最大衰減（比例，乘上強度）
    "curved_light_dir": (0.35, -0.5, 0.8),  # 主光源方向（Lambert 明暗用；x 右、y 下、z 朝相機）
    "curved_ambient": 0.45,  # 環境光比例（0 到 1）：背光面也至少有這麼亮
    "curved_blur_sigma": (0.4, 0.9),  # 高斯模糊 sigma（像素，乘上強度）
    "curved_noise_sigma": (2.0, 5.0),  # 雜訊標準差（灰階值，乘上強度）
    "curved_jpeg_quality": (75, 92),  # JPEG 品質範圍
}


def _cp(params: dict | None) -> dict[str, Any]:
    p = dict(CURVED_PARAMS)
    if params:
        p.update(params)
    return p


def _fold(U, V, X, Y, Z, point, normal, r, tmax):
    """沿一條直線把紙往相機方向捲起或折起（等長）：線外側（normal 指向的那側）繞在半徑 r 的圓柱上，
    轉到 tmax（弧度）之後沿切線直走。U、V 是平的紙上的座標（決定哪一側），X、Y、Z 就地累加位移。"""
    nx, ny = normal
    s = (U - point[0]) * nx + (V - point[1]) * ny
    on = s > 0
    sp = np.where(on, s, 0.0)
    t = np.minimum(sp / max(r, 1e-6), tmax)
    rest = sp - t * r
    inplane = r * np.sin(t) + rest * math.cos(tmax)
    shift = inplane - sp
    X += np.where(on, shift * nx, 0.0)
    Y += np.where(on, shift * ny, 0.0)
    Z += np.where(on, r * (1 - np.cos(t)) + rest * math.sin(tmax), 0.0)


def _curved_surface(W: float, H: float, g: float, ops: list[dict]):
    us = np.arange(0.0, W + 1e-6, g)
    vs = np.arange(0.0, H + 1e-6, g)
    if us[-1] < W:
        us = np.append(us, W)
    if vs[-1] < H:
        vs = np.append(vs, H)
    U, V = np.meshgrid(us, vs)
    X, Y, Z = U.copy(), V.copy(), np.zeros_like(U)
    for op in ops:
        if op["type"] == "fold":
            _fold(U, V, X, Y, Z, op["point"], op["normal"], op["r"], math.radians(op["deg"]))
        elif op["type"] == "bend":
            ang = float(op["angle"])  # 整頁轉過的角度（弧度）
            if op["axis"] == "v":  # 軸線沿頁高：左右兩側翹起
                k = ang / W
                s = X - W / 2
                X = W / 2 + np.sin(k * s) / k
                Z = Z + op["sign"] * (1 - np.cos(k * s)) / k
            else:
                k = ang / H
                s = Y - H / 2
                Y = H / 2 + np.sin(k * s) / k
                Z = Z + op["sign"] * (1 - np.cos(k * s)) / k
    return U, V, np.stack([X, Y, Z], -1)


def _rot3(ax: float, ay: float, az: float) -> np.ndarray:
    ax, ay, az = map(math.radians, (ax, ay, az))
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return rz @ ry @ rx


def _project(P: np.ndarray, W: float, H: float, cam: dict):
    """針孔相機：相機在頁面中心正上方 dist × 長邊處往下看；回傳正規化影像座標與深度。"""
    D = cam["dist"] * max(W, H)
    C = np.array([W / 2, H / 2, D])
    Pc = (P - C) * np.array([1.0, 1.0, -1.0])  # 世界 z 朝相機 → 相機 z 朝前
    Pc = Pc @ _rot3(cam["tilt_x"], cam["tilt_y"], cam["roll"]).T
    return Pc[..., 0] / Pc[..., 2], Pc[..., 1] / Pc[..., 2], Pc[..., 2]


def _raster_uv(px, py, depth, U, V, out_w: int, out_h: int, ss: int) -> np.ndarray:
    """三角網格光柵化（畫家演算法，遠的先畫）：回傳每個（超取樣）像素中心的頁面座標 (u, v)，紙外為 NaN。"""
    gh, gw = U.shape
    idx = np.arange(gh * gw).reshape(gh, gw)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    tris = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    X = px.ravel() * ss
    Y = py.ravel() * ss
    Dd = depth.ravel()
    tris = tris[np.argsort(-Dd[tris].mean(1), kind="stable")]
    buf = np.full((out_h * ss, out_w * ss), -1, np.int32)
    shift = 4
    pts = np.round((np.stack([X[tris], Y[tris]], -1) - 0.5) * (1 << shift)).astype(np.int32)  # 角落約定 → OpenCV 像素中心
    for k in range(len(tris)):
        cv2.fillConvexPoly(buf, pts[k], int(k), lineType=cv2.LINE_8, shift=shift)
    ys, xs = np.nonzero(buf >= 0)
    t = tris[buf[ys, xs]]
    qx, qy = xs + 0.5, ys + 0.5
    x0, y0, x1, y1, x2, y2 = X[t[:, 0]], Y[t[:, 0]], X[t[:, 1]], Y[t[:, 1]], X[t[:, 2]], Y[t[:, 2]]
    det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    l0 = ((y1 - y2) * (qx - x2) + (x2 - x1) * (qy - y2)) / det
    l1 = ((y2 - y0) * (qx - x2) + (x0 - x2) * (qy - y2)) / det
    l2 = 1 - l0 - l1
    Uf, Vf = U.ravel(), V.ravel()
    out = np.full((out_h * ss, out_w * ss, 2), np.nan, np.float32)
    out[ys, xs, 0] = l0 * Uf[t[:, 0]] + l1 * Uf[t[:, 1]] + l2 * Uf[t[:, 2]]
    out[ys, xs, 1] = l0 * Vf[t[:, 0]] + l1 * Vf[t[:, 1]] + l2 * Vf[t[:, 2]]
    return out


def _grid_sample(G: np.ndarray, uv: np.ndarray, us: np.ndarray, vs: np.ndarray) -> np.ndarray:
    """在曲面網格上的量（G：gh×gw）依頁面座標 uv 雙線性取樣；uv 為 NaN 處給 0。"""
    ok = ~np.isnan(uv[..., 0])
    fu = np.interp(np.where(ok, uv[..., 0], 0), us, np.arange(len(us)))
    fv = np.interp(np.where(ok, uv[..., 1], 0), vs, np.arange(len(vs)))
    out = cv2.remap(G.astype(np.float32), fu.astype(np.float32), fv.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return np.where(ok, out, 0.0).astype(np.float32)


def _paper_like_texture(rng, w: int, h: int, paper: np.ndarray) -> np.ndarray:
    """與紙同色（略暗、略偏暖）的布紋：低對比，紙邊只剩很小的亮度差。"""
    base = paper.astype(np.float32) * float(rng.uniform(0.86, 0.97)) * np.array([1.0, 0.985, 0.95], np.float32)
    sw, sh = max(8, w // 8), max(8, h // 8)
    field = np.zeros((sh, sw), np.float32)
    for k, amp in ((4, 0.5), (16, 0.3), (64, 0.2)):
        small = rng.normal(0, 1, (max(2, sh * k // 64), max(2, sw * k // 64))).astype(np.float32)
        field += amp * cv2.resize(small, (sw, sh), interpolation=cv2.INTER_CUBIC)
    field = cv2.resize(field, (w, h), interpolation=cv2.INTER_LINEAR)
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    field += 0.35 * np.sin(xx * 1.1) * np.sin(yy * 1.1)
    return base[None, None, :] * (1.0 + 0.035 * field[..., None])


def _ops_for(kind: str, rng, strength: float, W: float, H: float, p: dict) -> list[dict]:
    st = max(0.0, strength)
    ops: list[dict] = []

    def curl_top():
        v = _range(rng, p["curved_curl_v"]) * H
        ops.append({"type": "fold", "name": "curl_top", "point": (0.0, v), "normal": (0.0, -1.0),
                    "r": _range(rng, p["curved_curl_r"]) * H, "deg": _range(rng, p["curved_curl_deg"]) * min(st, 1.5)})

    def crease(axis=None):
        axis = axis or ("h" if rng.random() < 0.6 else "v")
        pos = _range(rng, p["curved_crease_pos"])
        deg = _range(rng, p["curved_crease_deg"]) * st
        if axis == "h":
            ops.append({"type": "fold", "name": "crease", "point": (0.0, pos * H), "normal": (0.0, 1.0), "r": 0.004 * H, "deg": deg})
        else:
            ops.append({"type": "fold", "name": "crease", "point": (pos * W, 0.0), "normal": (1.0, 0.0), "r": 0.004 * H, "deg": deg})

    if kind == "bend":
        axis = "v" if rng.random() < p["curved_bend_axis_v_prob"] else "h"
        ops.append({"type": "bend", "axis": axis, "angle": _range(rng, p["curved_bend_k"]) * st, "sign": 1.0 if rng.random() < 0.7 else -1.0})
    elif kind == "curl_top":
        curl_top()
    elif kind == "corner_curl":
        corner = int(rng.integers(0, 4))
        a, b = _range(rng, p["curved_corner_cut"]) * W, _range(rng, p["curved_corner_cut"]) * W
        cx, cy = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)][corner]
        sx, sy = (1.0 if cx == 0 else -1.0), (1.0 if cy == 0 else -1.0)
        p1, p2 = np.array([cx + sx * a, cy]), np.array([cx, cy + sy * b])
        d = p2 - p1
        nrm = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-9)
        if (np.array([cx, cy]) - p1) @ nrm < 0:
            nrm = -nrm
        ops.append({"type": "fold", "name": "corner_curl", "corner": corner, "point": tuple(p1), "normal": tuple(nrm),
                    "r": _range(rng, p["curved_corner_r"]) * H, "deg": _range(rng, p["curved_corner_deg"]) * min(st, 1.5)})
    elif kind == "crease":
        crease()
    elif kind == "curl_crease":
        curl_top()
        crease("h")
        ops.append({"type": "bend", "axis": "v", "angle": 0.45 * st, "sign": 1.0})
    elif kind != "flat":
        raise ValueError(f"不認得的彎曲種類：{kind}（可用：{', '.join(CURVED_KINDS)}）")
    return ops


def degrade_curved(image: np.ndarray, kind: str, seed: int, strength: float = 1.0, params: dict | None = None) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """把頁面貼到三維曲面拍成照片。回傳（照片 RGB uint8、page_map、說明 dict）。

    page_map：H×W×2 float32，照片每個像素中心 → 頁面影像座標（像素角落約定）；不是這張紙的像素為 NaN。
    info["page_corners"]：頁面四角投影到照片的位置（左上、右上、右下、左下；捲起時可能被遮住）；
    info["page_outline"]：頁面四條邊投影到照片的折線（上、右、下、左，各 65 點）。
    info["flat_corners"]：「展平後」的四角：沒有折、捲的那片紙（平面）延伸到頁面四角的投影；捲角、捲邊時四角偵測應對照這組
    （實體的角翹起來了）。整張弓起（bend）時只供參考。
    """
    if kind not in CURVED_KINDS:
        raise ValueError(f"不認得的彎曲種類：{kind}（可用：{', '.join(CURVED_KINDS)}）")
    page = np.ascontiguousarray(image[..., :3] if image.ndim == 3 else np.repeat(image[..., None], 3, axis=2)).astype(np.uint8)
    p = _cp(params)
    strength = max(0.0, float(strength))
    rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, 100 + CURVED_KINDS.index(kind)])
    H, W = page.shape[:2]
    # ---- 抽參數（固定順序，方便重現）
    ops = _ops_for(kind, rng, strength, float(W), float(H), p)
    tx, ty, tr = p["curved_tilt_deg"]
    cam = {"dist": _range(rng, p["curved_cam_dist"]), "tilt_x": float(rng.uniform(-1, 1)) * tx * strength,
           "tilt_y": float(rng.uniform(-1, 1)) * ty * strength, "roll": float(rng.uniform(-1, 1)) * tr * strength}
    margin = _range(rng, p["curved_margin"])
    bg_style = p["curved_background"] or ("paper_like" if rng.random() < p["curved_paper_like_prob"] else str(rng.choice(["desk", "wood", "fabric", "dark"])))
    r_sheet = rng.random()
    second = bool(p["curved_second_sheet"]) if p["curved_second_sheet"] is not None else bool(r_sheet < p["curved_second_sheet_prob"])
    r_shadow = rng.random()
    hard_shadow = bool(p["curved_hard_shadow"]) if p["curved_hard_shadow"] is not None else bool(r_shadow < p["curved_hard_shadow_prob"]) and strength > 0
    r_glare = rng.random()
    glare = bool(p["curved_glare"]) if p["curved_glare"] is not None else bool(r_glare < p["curved_glare_prob"]) and strength > 0
    # ---- 曲面 → 投影
    g = float(p["curved_grid_px"])
    U, V, P = _curved_surface(float(W), float(H), g, ops)
    x, y, z = _project(P, float(W), float(H), cam)
    xmin, xmax, ymin, ymax = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    ext = max(xmax - xmin, ymax - ymin)
    sc = float(p["curved_out_long"]) / ext / (1 + 2 * margin)
    ox, oy = -xmin * sc + margin * ext * sc, -ymin * sc + margin * ext * sc
    px, py = x * sc + ox, y * sc + oy
    ow = int(math.ceil((xmax - xmin) * sc + 2 * margin * ext * sc))
    oh = int(math.ceil((ymax - ymin) * sc + 2 * margin * ext * sc))
    page_map = _raster_uv(px, py, z, U, V, ow, oh, 1)
    uv2 = _raster_uv(px, py, z, U, V, ow, oh, 2)
    # ---- 取樣頁面（2 倍超取樣再縮小）＋ Lambert 明暗
    valid2 = ~np.isnan(uv2[..., 0])
    mx = np.where(valid2, uv2[..., 0] - 0.5, -10).astype(np.float32)
    my = np.where(valid2, uv2[..., 1] - 0.5, -10).astype(np.float32)
    img2 = cv2.remap(page.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    dPu, dPv = np.gradient(P, axis=1), np.gradient(P, axis=0)
    nrm = np.cross(dPu, dPv)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True) + 1e-9
    nrm *= np.sign(nrm[..., 2:3] + 1e-9)
    L = np.asarray(p["curved_light_dir"], dtype=np.float64)
    L = L / np.linalg.norm(L)
    amb = float(p["curved_ambient"])
    flat_shade = amb + (1 - amb) * max(0.0, float(L[2]))  # 平的紙的亮度：讓平面部分維持原色
    shade_grid = (amb + (1 - amb) * np.clip(nrm @ L, 0, 1)) / flat_shade
    us, vs = U[0], V[:, 0]
    img2 *= _grid_sample(shade_grid, uv2, us, vs)[..., None]
    img = cv2.resize(img2, (ow, oh), interpolation=cv2.INTER_AREA)
    mask = cv2.resize(valid2.astype(np.float32), (ow, oh), interpolation=cv2.INTER_AREA)
    # ---- 背景、第二張紙
    paper = np.percentile(page.reshape(-1, 3), 90, axis=0).astype(np.float32)
    tex = _paper_like_texture(rng, ow, oh, paper) if bg_style == "paper_like" else _texture(rng, ow, oh, bg_style)[0]
    corners = np.array([[px[0, 0], py[0, 0]], [px[0, -1], py[0, -1]], [px[-1, -1], py[-1, -1]], [px[-1, 0], py[-1, 0]]])
    sheet_poly = None
    if second:
        tr_, br_ = corners[1], corners[2]
        e = (br_ - tr_) / max(np.linalg.norm(br_ - tr_), 1e-9)
        out_n = np.array([-e[1], e[0]])
        if (tr_ - corners.mean(axis=0)) @ out_n < 0:
            out_n = -out_n
        side = float(np.linalg.norm(br_ - tr_))
        gap = float(rng.uniform(-0.02, 0.004)) * side  # 負值：壓在主紙底下（碰在一起）
        off = float(rng.uniform(-0.15, 0.2)) * side
        ang = math.radians(float(rng.uniform(-4, 4)))
        ca, sa = math.cos(ang), math.sin(ang)
        e2 = np.array([ca * e[0] - sa * e[1], sa * e[0] + ca * e[1]])
        n2 = np.array([-e2[1], e2[0]]) * (1 if (np.array([-e2[1], e2[0]]) @ out_n) > 0 else -1)
        a0 = tr_ + gap * out_n + off * e
        span = float(rng.uniform(0.6, 1.0)) * side
        sheet_poly = np.array([a0, a0 + span * n2, a0 + span * n2 + side * 1.05 * e2, a0 + side * 1.05 * e2])
        sm = np.zeros((oh, ow), np.float32)
        cv2.fillConvexPoly(sm, np.round((sheet_poly - 0.5) * 16).astype(np.int32), 1.0, lineType=cv2.LINE_AA, shift=4)
        tone = paper * float(rng.uniform(0.95, 1.01))
        sheet_img = np.broadcast_to(tone, (oh, ow, 3)).copy()
        # 第二張紙上的「字」：幾條灰色橫條（讓文字偵測在那裡也看到內容，跟真實照片一樣）
        lh = max(1, int(round(0.005 * side)))
        for k in range(int(rng.integers(8, 20))):
            t0 = float(rng.uniform(0.05, 0.95))
            q0 = a0 + t0 * side * e2 + float(rng.uniform(0.05, 0.3)) * span * n2
            q1 = q0 + float(rng.uniform(0.15, 0.45)) * span * n2
            cv2.line(sheet_img, tuple(int(v) for v in q0), tuple(int(v) for v in q1), tuple(float(c) * 0.6 for c in tone), lh, lineType=cv2.LINE_AA)
        tex = tex * (1 - sm[..., None]) + sheet_img * sm[..., None]
    shm = np.roll(cv2.GaussianBlur(mask, (0, 0), 6.0), (5, 4), (0, 1))
    tex *= (1 - 0.35 * shm)[..., None]
    f = img + tex * (1 - mask[..., None])
    # ---- 光照：漸層、硬邊陰影、反光
    yy, xx = np.mgrid[0:oh, 0:ow].astype(np.float32)
    th = float(rng.uniform(0, 2 * math.pi))
    tt = ((xx - ow / 2) * math.cos(th) + (yy - oh / 2) * math.sin(th)) / (0.5 * math.hypot(ow, oh))
    grad = _range(rng, p["curved_light_gradient"]) * strength
    light = 1.0 - grad * (tt + 1.0) / 2.0
    shadow_poly, sd, s_sig = None, 0.0, 0.0
    if hard_shadow:
        bx0, by0 = corners.min(axis=0)
        bx1, by1 = corners.max(axis=0)
        wq, hq = bx1 - bx0, by1 - by0
        shadow_poly = np.array([[bx0 + rng.uniform(0.35, 0.65) * wq, by0 - 0.2 * hq], [bx1 + 0.2 * wq, by0 - 0.2 * hq],
                                [bx1 + 0.2 * wq, by0 + rng.uniform(0.4, 0.9) * hq], [bx0 + rng.uniform(0.5, 0.8) * wq, by1 + 0.2 * hq]])
        smk = np.zeros((oh, ow), np.float32)
        cv2.fillConvexPoly(smk, np.round((shadow_poly - 0.5) * 16).astype(np.int32), 1.0, lineType=cv2.LINE_AA, shift=4)
        s_sig = float(rng.uniform(1.0, 3.0))
        smk = cv2.GaussianBlur(smk, (0, 0), s_sig)
        sd = _range(rng, p["curved_shadow_strength"])
        light *= 1.0 - sd * smk
    f *= light[..., None]
    glare_ell = None
    if glare:
        pc = corners.mean(axis=0) + rng.uniform(-0.25, 0.25, 2) * (corners.max(axis=0) - corners.min(axis=0))
        axes = (float(rng.uniform(0.05, 0.12)) * ow, float(rng.uniform(0.03, 0.07)) * oh)
        gm = np.zeros((oh, ow), np.float32)
        cv2.ellipse(gm, (int(pc[0]), int(pc[1])), (int(axes[0]), int(axes[1])), float(rng.uniform(0, 180)), 0, 360, 1.0, -1)
        gm = cv2.GaussianBlur(gm, (0, 0), 0.15 * min(axes)) * mask
        gs = float(rng.uniform(0.7, 0.97))
        f = f + (255.0 - f) * (gs * gm)[..., None]
        glare_ell = {"center": pc.tolist(), "axes": list(axes), "strength": gs}
    wb = 1.0 + rng.uniform(-1, 1, 3).astype(np.float32) * 0.05 * min(1.0, strength)
    f *= wb[None, None, :]
    sigma = _range(rng, p["curved_blur_sigma"]) * strength
    if sigma > 0.05:
        f = cv2.GaussianBlur(f, (0, 0), sigma)
    noise = _range(rng, p["curved_noise_sigma"]) * strength
    if noise > 0:
        f += rng.normal(0.0, noise, (oh, ow, 1)).astype(np.float32)
        f += rng.normal(0.0, noise * 0.4, (oh, ow, 3)).astype(np.float32)
    q = _quality(rng, p["curved_jpeg_quality"], strength) if strength > 0 else 95
    out = _jpeg(_clip_u8(f), q)
    # ---- 說明
    n_out = 65

    def edge(a, b):
        t = np.linspace(0, 1, n_out)
        return np.stack([np.interp(t, np.linspace(0, 1, len(a)), a), np.interp(t, np.linspace(0, 1, len(b)), b)], -1).tolist()

    outline = [edge(px[0, :], py[0, :]), edge(px[:, -1], py[:, -1]), edge(px[-1, ::-1], py[-1, ::-1]), edge(px[::-1, 0], py[::-1, 0])]
    info: dict[str, Any] = {
        "kind": kind, "seed": int(seed), "strength": strength, "source_size": [int(W), int(H)], "output_size": [int(ow), int(oh)],
        "ops": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in op.items()} for op in ops], "camera": cam, "margin": margin,
        "background": bg_style, "second_sheet": second, "second_sheet_poly": None if sheet_poly is None else sheet_poly.tolist(),
        "hard_shadow": hard_shadow, "shadow_poly": None if shadow_poly is None else shadow_poly.tolist(), "shadow_strength": sd, "shadow_blur_sigma": s_sig,
        "glare": glare_ell, "light_gradient": grad, "white_balance": wb.tolist(), "blur_sigma": sigma, "noise_sigma": noise, "jpeg_quality": q,
        "page_corners": corners.tolist(), "page_outline": outline,
        "page_fraction": float(np.mean(~np.isnan(page_map[..., 0]))),
    }
    # 「展平後」的四角：紙沒有折、捲的那一大片（z = 0 的平面）延伸到頁面四角的投影位置，也就是平的那幾段紙邊直線的交點。
    # 捲角、捲邊時，四角偵測與透視攤平要找的是這組（實體的角翹起來了，投影位置不是攤平要的角）；整張弓起（bend）時沒有平的部分，只供參考
    Pf = np.array([[0.0, 0.0, 0.0], [W, 0.0, 0.0], [W, H, 0.0], [0.0, H, 0.0]])
    fx, fy, _ = _project(Pf, float(W), float(H), cam)
    info["flat_corners"] = np.stack([fx * sc + ox, fy * sc + oy], 1).tolist()
    # 頁面在照片中的等效解析度（中央附近的局部縮放）
    pc0 = np.array([W / 2.0, H / 2.0])
    jj = np.argmin(np.abs(us - pc0[0]))
    ii = np.argmin(np.abs(vs - pc0[1]))
    if 0 < ii < len(vs) - 1 and 0 < jj < len(us) - 1:
        du = np.hypot(px[ii, jj + 1] - px[ii, jj - 1], py[ii, jj + 1] - py[ii, jj - 1]) / (us[jj + 1] - us[jj - 1])
        dv = np.hypot(px[ii + 1, jj] - px[ii - 1, jj], py[ii + 1, jj] - py[ii - 1, jj]) / (vs[ii + 1] - vs[ii - 1])
        info["effective_dpi"] = float(150.0 * math.sqrt(du * dv))
    return out, page_map, info


def sample_page_map(page_map: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """照片座標（像素角落約定）→ 頁面座標：在 page_map 上雙線性取樣。回傳（u、v、是否落在紙上）。
    四個鄰近像素都要在紙上才算（紙邊附近不內插背景）。"""
    valid = (~np.isnan(page_map[..., 0])).astype(np.float32)
    b = np.nan_to_num(page_map).astype(np.float32)
    xs = (np.asarray(x, dtype=np.float32) - 0.5)
    ys = (np.asarray(y, dtype=np.float32) - 0.5)
    shape = xs.shape
    xs2, ys2 = xs.reshape(1, -1) if xs.ndim == 1 else xs, ys.reshape(1, -1) if ys.ndim == 1 else ys
    u = cv2.remap(b[..., 0], xs2, ys2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    v = cv2.remap(b[..., 1], xs2, ys2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    w = cv2.remap(valid, xs2, ys2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return u.reshape(shape), v.reshape(shape), (w > 0.999).reshape(shape)


# ---------------------------------------------------------------- 原生 PDF 頁面（含文字層）


def render_pdf_page(pdf: Any, page_index: int = 0, dpi: float = 150.0) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """原生 PDF 畫成影像，並取出文字層。pdf 是路徑或 bytes。
    回傳（RGB uint8 影像、文字段清單 [{"text", "bbox": [x0, y0, x1, y1]（影像像素，角落約定）}]）。
    文字段是 PDFium 的文字矩形（同一行、同一字型的一段），同一行相鄰的段落再依距離併成一行。"""
    import pypdfium2 as pdfium

    src = pdf if isinstance(pdf, (bytes, bytearray)) else str(pdf)
    doc = pdfium.PdfDocument(src)
    try:
        pg = doc[page_index]
        w_pt, h_pt = pg.get_size()
        k = dpi / 72.0
        img = np.ascontiguousarray(pg.render(scale=k).to_numpy()[..., :3])
        tp = pg.get_textpage()
        segs = []
        for i in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(i)
            text = tp.get_text_bounded(l, b, r, t).replace("\r", "").replace("\n", " ").strip()
            if text:
                segs.append({"text": text, "bbox": [l * k, (h_pt - t) * k, r * k, (h_pt - b) * k]})
    finally:
        doc.close()
    return img, _merge_segments(segs)


def _merge_segments(segs: list[dict]) -> list[dict]:
    """同一行（垂直重疊過半）且水平間距小於 1.2 個行高的文字段併成一行。"""
    segs = sorted(segs, key=lambda s: (round(s["bbox"][1] / 4), s["bbox"][0]))
    out: list[dict] = []
    for s in segs:
        x0, y0, x1, y1 = s["bbox"]
        hit = None
        for o in out:
            a0, b0, a1, b1 = o["bbox"]
            ov = min(y1, b1) - max(y0, b0)
            hgt = max(1e-6, min(y1 - y0, b1 - b0))
            if ov / hgt > 0.5 and -0.5 * hgt <= x0 - a1 <= 1.2 * max(y1 - y0, b1 - b0):
                hit = o
                break
        if hit is None:
            out.append({"text": s["text"], "bbox": list(s["bbox"])})
        else:
            hit["text"] = hit["text"] + " " + s["text"]
            hit["bbox"] = [min(hit["bbox"][0], x0), min(hit["bbox"][1], y0), max(hit["bbox"][2], x1), max(hit["bbox"][3], y1)]
    return out


def table_page_pdf(seed: int = 0, rows: int = 13) -> bytes:
    """內有大表格的頁面（原生 PDF，A4）：標題、課表式格線表格（4 欄 × rows 列）、頁尾。"""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    rng = np.random.default_rng([int(seed) & 0xFFFFFFFF, 7])
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, H - 88, "Garden Club Work Rota 4127")
    c.setFont("Helvetica", 8.5)
    c.drawString(60, H - 106, "Saturday mornings from 3rd May, Potting Shed B")
    x0, x1 = 56, W - 56
    cols = [x0, x0 + 48, x0 + 196, x0 + 344, x1]
    words = ("Planting basics for Community Gardens and Compost seasonal soil water seed bulb harvest "
             "pruning hedges flower allotment greenhouse orchard composting watering shed tools volunteer").split()
    y = H - 124
    bands = []
    for r in range(rows + 1):
        h = 24 if r == 0 else 40
        bands.append((y, y - h))
        y -= h
    c.setLineWidth(0.8)
    for ya, _ in bands:
        c.line(x0, ya, x1, ya)
    c.line(x0, bands[-1][1], x1, bands[-1][1])
    for xx in cols:
        c.line(xx, bands[0][0], xx, bands[-1][1])
    for r, (ya, _) in enumerate(bands):
        for k in range(4):
            if r == 0:
                c.setFont("Helvetica-Bold", 9.5)
                c.drawString(cols[k] + 5, ya - 15, ["Week", "Morning", "Afternoon", "Notes"][k])
                continue
            if k == 0:
                c.setFont("Helvetica", 9)
                c.drawString(cols[0] + 5, ya - 13, f"{r}")
                c.drawString(cols[0] + 5, ya - 27, f"{(r * 7) % 28 + 1} Oct")
                continue
            if k == 3 and r % 3:
                continue
            c.setFont("Helvetica", 8)
            for li in range(int(rng.integers(1, 4))):
                s = " ".join(str(wd) for wd in rng.choice(words, int(rng.integers(3, 6))))
                c.drawString(cols[k] + 5, ya - 12 - li * 10, s[:32])
    c.setFont("Helvetica", 8.5)
    c.drawString(W / 2 - 42, 46, "Club Handbook 2031-32")
    c.drawString(W - 76, 46, "3")
    c.showPage()
    c.save()
    return buf.getvalue()


def table_page(seed: int = 0, dpi: float = 150.0) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """table_page_pdf() 畫成影像並取出文字層（見 render_pdf_page）。"""
    return render_pdf_page(table_page_pdf(seed), 0, dpi)
