"""描圖向量化的點陣化：把路徑畫回像素，用來自我驗證（vectorize.render 的實作）。

與 SVG、PDF 的填色規則一致：
  填色依 fill_rule（nonzero、evenodd）；筆畫依線寬、端點（butt、round、square）、轉角（miter、round、bevel）；
  漸層的色標在 sRGB 數值上線性內插；反鋸齒的覆蓋率在 sRGB 數值上混色（一般瀏覽器與 resvg 的做法）。
覆蓋率是「精確面積」：把每條邊切到像素格線上，逐格累加有號面積（與字型點陣器相同的做法），不是超取樣，
所以邊界位置沒有系統偏差，才能拿來驗證 0.1 像素等級的量測。

座標約定：像素 i 佔 [i, i+1)，中心在 i+0.5。路徑編碼與 ShapeElement.path 相同：
  [0, x, y] 起筆、[1, x, y] 直線、[2, x1, y1, x2, y2, x, y] 三次貝茲、[3] 閉合。
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

_KAPPA = 0.5522847498307936  # 四段三次貝茲畫圓時控制點的距離 ÷ 半徑


# ---------------------------------------------------------------- 路徑工具


def flatten_path(path: list[list[float]], tol: float = 0.05) -> list[tuple[np.ndarray, bool]]:
    """把路徑攤平成折線。回傳每條子路徑的（點 (N, 2)、是否閉合）。tol 是貝茲攤平的容許誤差（像素）。"""
    subs: list[tuple[np.ndarray, bool]] = []
    cur: list[tuple[float, float]] = []
    closed = False

    def flush():
        nonlocal cur, closed
        if len(cur) >= 2:
            subs.append((np.asarray(cur, dtype=np.float64), closed))
        cur, closed = [], False

    for seg in path:
        k = int(seg[0])
        if k == 0:
            flush()
            cur = [(float(seg[1]), float(seg[2]))]
        elif k == 1:
            cur.append((float(seg[1]), float(seg[2])))
        elif k == 2:
            if not cur:
                continue
            p0 = np.asarray(cur[-1])
            p1, p2, p3 = np.asarray(seg[1:3], dtype=np.float64), np.asarray(seg[3:5], dtype=np.float64), np.asarray(seg[5:7], dtype=np.float64)
            dd = max(float(np.hypot(*(p0 - 2 * p1 + p2))), float(np.hypot(*(p1 - 2 * p2 + p3))))
            n = int(min(400, max(1, math.ceil(math.sqrt(0.75 * dd / max(tol, 1e-6))))))
            t = np.linspace(0.0, 1.0, n + 1)[1:, None]
            pts = ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * (t ** 2) * p2 + (t ** 3) * p3
            cur.extend((float(x), float(y)) for x, y in pts)
        elif k == 3:
            closed = True
            start = cur[0] if cur else None
            flush()
            if start is not None:
                cur = [start]  # 閉合之後沒有再起筆時，下一段從子路徑起點開始（與 SVG 相同）
    flush()
    return subs


def path_node_count(path: list[list[float]]) -> int:
    """節點數：線段與曲線段的數目；閉合時終點不在起點上，補上的那一段也算。"""
    n = 0
    start = None
    last = None
    for seg in path:
        k = int(seg[0])
        if k == 0:
            start = last = (float(seg[1]), float(seg[2]))
        elif k == 1:
            n += 1
            last = (float(seg[1]), float(seg[2]))
        elif k == 2:
            n += 1
            last = (float(seg[5]), float(seg[6]))
        elif k == 3 and start is not None and last is not None:
            if math.hypot(last[0] - start[0], last[1] - start[1]) > 1e-6:
                n += 1
            last = start
    return n


def ellipse_path(cx: float, cy: float, rx: float, ry: float, angle_deg: float = 0.0) -> list[list[float]]:
    """橢圓（四段三次貝茲）。angle_deg 順時針為正（y 向下）。"""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)

    def tr(x, y):
        return [cx + x * ca - y * sa, cy + x * sa + y * ca]

    kx, ky = rx * _KAPPA, ry * _KAPPA
    pts = [(rx, 0), (rx, ky), (kx, ry), (0, ry), (-kx, ry), (-rx, ky), (-rx, 0), (-rx, -ky), (-kx, -ry), (0, -ry), (kx, -ry), (rx, -ky), (rx, 0)]
    q = [tr(x, y) for x, y in pts]
    out: list[list[float]] = [[0, *q[0]]]
    for i in range(4):
        out.append([2, *q[3 * i + 1], *q[3 * i + 2], *q[3 * i + 3]])
    out.append([3])
    return out


def rounded_rect_path(cx: float, cy: float, w: float, h: float, r: float, angle_deg: float = 0.0) -> list[list[float]]:
    """矩形或圓角矩形。r 為 0 時只有四條直線。"""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    hx, hy = w / 2.0, h / 2.0
    r = max(0.0, min(r, hx, hy))

    def tr(x, y):
        return [cx + x * ca - y * sa, cy + x * sa + y * ca]

    if r <= 1e-9:
        c = [tr(-hx, -hy), tr(hx, -hy), tr(hx, hy), tr(-hx, hy)]
        return [[0, *c[0]], [1, *c[1]], [1, *c[2]], [1, *c[3]], [3]]
    k = r * (1 - _KAPPA)
    out: list[list[float]] = [[0, *tr(-hx + r, -hy)]]
    full_w, full_h = hx - r <= 1e-9, hy - r <= 1e-9
    if not full_w:
        out.append([1, *tr(hx - r, -hy)])
    out.append([2, *tr(hx - k, -hy), *tr(hx, -hy + k), *tr(hx, -hy + r)])
    if not full_h:
        out.append([1, *tr(hx, hy - r)])
    out.append([2, *tr(hx, hy - k), *tr(hx - k, hy), *tr(hx - r, hy)])
    if not full_w:
        out.append([1, *tr(-hx + r, hy)])
    out.append([2, *tr(-hx + k, hy), *tr(-hx, hy - k), *tr(-hx, hy - r)])
    if not full_h:
        out.append([1, *tr(-hx, -hy + r)])
    out.append([2, *tr(-hx, -hy + k), *tr(-hx + k, -hy), *tr(-hx + r, -hy)])
    out.append([3])
    return out


def polygon_path(points) -> list[list[float]]:
    pts = [[float(x), float(y)] for x, y in points]
    return [[0, *pts[0]]] + [[1, *p] for p in pts[1:]] + [[3]]


# ---------------------------------------------------------------- 精確面積的覆蓋率


def _accumulate(polys: list[np.ndarray], W: int, H: int) -> np.ndarray:
    """多個封閉多邊形（各為 (N, 2)，座標已在目標像素格上）的有號覆蓋率（繞數 × 面積），形狀 (H, W)。"""
    acc = np.zeros((H, W + 2), dtype=np.float64)
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in polys:
        if len(p) < 3:
            continue
        q = np.roll(p, -1, axis=0)
        xs0.append(p[:, 0]); ys0.append(p[:, 1]); xs1.append(q[:, 0]); ys1.append(q[:, 1])
    if not xs0:
        return acc[:, :W]
    x0 = np.clip(np.concatenate(xs0), 0.0, float(W)); y0 = np.clip(np.concatenate(ys0), 0.0, float(H))
    x1 = np.clip(np.concatenate(xs1), 0.0, float(W)); y1 = np.clip(np.concatenate(ys1), 0.0, float(H))
    keep = y0 != y1  # 水平邊對面積沒有貢獻
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    if x0.size == 0:
        return acc[:, :W]
    # 每條邊與整數格線（x 與 y）的交點參數 t；邊被切成每段都落在單一像素格內
    ny = (np.abs(np.floor(y1) - np.floor(y0)) + 1).astype(np.int64)
    nx = (np.abs(np.floor(x1) - np.floor(x0)) + 1).astype(np.int64)
    n_edges = x0.size
    eid_y = np.repeat(np.arange(n_edges), ny)
    off_y = np.arange(ny.sum()) - np.repeat(np.cumsum(ny) - ny, ny)
    ylo = np.floor(np.minimum(y0, y1))
    gy = ylo[eid_y] + off_y + 1  # 交到的水平格線 y 值（可能略超出邊的範圍，後面以 t 篩掉）
    ty = (gy - y0[eid_y]) / (y1 - y0)[eid_y]
    eid_x = np.repeat(np.arange(n_edges), nx)
    off_x = np.arange(nx.sum()) - np.repeat(np.cumsum(nx) - nx, nx)
    xlo = np.floor(np.minimum(x0, x1))
    gx = xlo[eid_x] + off_x + 1
    dx = (x1 - x0)[eid_x]
    with np.errstate(divide="ignore", invalid="ignore"):
        tx = np.where(dx != 0, (gx - x0[eid_x]) / dx, -1.0)
    eid = np.concatenate([np.arange(n_edges), np.arange(n_edges), eid_y, eid_x])
    t = np.concatenate([np.zeros(n_edges), np.ones(n_edges), ty, tx])
    ok = (t >= 0.0) & (t <= 1.0)
    eid, t = eid[ok], t[ok]
    order = np.lexsort((t, eid))
    eid, t = eid[order], t[order]
    same = eid[1:] == eid[:-1]
    e = eid[:-1][same]
    ta, tb = t[:-1][same], t[1:][same]
    nz = tb > ta
    e, ta, tb = e[nz], ta[nz], tb[nz]
    ex, ey = (x1 - x0)[e], (y1 - y0)[e]
    xa, xb = x0[e] + ex * ta, x0[e] + ex * tb
    ya, yb = y0[e] + ey * ta, y0[e] + ey * tb
    xm, ym = 0.5 * (xa + xb), 0.5 * (ya + yb)
    col = np.clip(np.floor(xm), 0, W).astype(np.int64)
    row = np.floor(ym).astype(np.int64)
    inside = (row >= 0) & (row < H)
    col, row, xm = col[inside], row[inside], xm[inside]
    d = (yb - ya)[inside]
    xmf = np.clip(xm - col, 0.0, 1.0)
    flat = acc.reshape(-1)
    idx = row * (W + 2) + col
    np.add.at(flat, idx, d * (1.0 - xmf))
    np.add.at(flat, idx + 1, d * xmf)
    return np.cumsum(acc, axis=1)[:, :W]


def coverage(polys: list[np.ndarray], W: int, H: int, rule: str = "nonzero") -> np.ndarray:
    """封閉多邊形集合的覆蓋率（0 到 1），形狀 (H, W)。rule：nonzero 或 evenodd。"""
    acc = np.abs(_accumulate(polys, W, H))
    if rule == "evenodd":
        m = np.mod(acc, 2.0)
        return np.where(m > 1.0, 2.0 - m, m).astype(np.float32)
    return np.clip(acc, 0.0, 1.0).astype(np.float32)


def _ccw(poly: np.ndarray) -> np.ndarray:
    """讓多邊形的方向一致（有號面積為正），筆畫的各個零件才能用繞數相加求聯集。"""
    x, y = poly[:, 0], poly[:, 1]
    area = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return poly if area >= 0 else poly[::-1]


def _disk(c, r: float, n: int = 0) -> np.ndarray:
    n = n or int(min(64, max(12, math.ceil(2 * math.pi * r / 0.6))))
    a = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    return np.stack([c[0] + r * np.cos(a), c[1] + r * np.sin(a)], axis=1)


def stroke_polygons(pts: np.ndarray, closed: bool, width: float, cap: str = "butt", join: str = "miter", miter_limit: float = 4.0) -> list[np.ndarray]:
    """一條折線的筆畫外形，拆成多個同向的凸多邊形（線段的矩形、轉角的補塊、端點的蓋子）；聯集就是筆畫。"""
    hw = width / 2.0
    out: list[np.ndarray] = []
    p = np.asarray(pts, dtype=np.float64)
    if len(p) >= 2:
        keep = np.r_[True, np.hypot(*(p[1:] - p[:-1]).T) > 1e-9]
        p = p[keep]
    if closed and len(p) >= 3 and np.hypot(*(p[0] - p[-1])) < 1e-9:
        p = p[:-1]
    n = len(p)
    if n < 2:
        if n == 1 and cap != "butt":
            out.append(_ccw(_disk(p[0], hw)) if cap == "round" else _ccw(np.array([[p[0][0] - hw, p[0][1] - hw], [p[0][0] + hw, p[0][1] - hw], [p[0][0] + hw, p[0][1] + hw], [p[0][0] - hw, p[0][1] + hw]])))
        return out
    segs = [(i, (i + 1) % n) for i in range(n if closed else n - 1)]
    dirs = []
    for i, j in segs:
        d = p[j] - p[i]
        L = float(np.hypot(*d))
        d = d / L
        nrm = np.array([-d[1], d[0]])
        dirs.append(d)
        out.append(_ccw(np.array([p[i] + nrm * hw, p[j] + nrm * hw, p[j] - nrm * hw, p[i] - nrm * hw])))
    # 轉角
    joints = range(n) if closed else range(1, n - 1)
    for v in joints:
        d0 = dirs[(v - 1) % len(segs)] if closed else dirs[v - 1]
        d1 = dirs[v % len(segs)] if closed else dirs[v]
        cross = d0[0] * d1[1] - d0[1] * d1[0]
        dot = float(np.clip(d0[0] * d1[0] + d0[1] * d1[1], -1.0, 1.0))
        if abs(cross) < 1e-9 and dot > 0:
            continue
        if join == "round":
            out.append(_ccw(_disk(p[v], hw)))
            continue
        s = 1.0 if cross > 0 else -1.0  # 外側在轉彎的另一邊
        n0 = np.array([-d0[1], d0[0]]) * (-s)
        n1 = np.array([-d1[1], d1[0]]) * (-s)
        a, b = p[v] + n0 * hw, p[v] + n1 * hw
        tri = [p[v], a, b]
        if join == "miter":
            half = math.acos(dot) / 2.0  # 轉角的一半
            ratio = 1.0 / max(math.cos(half), 1e-9)  # 尖角長度 ÷ 半線寬
            if ratio <= miter_limit:
                m = n0 + n1
                m = m / max(float(np.hypot(*m)), 1e-12)
                tri = [p[v], a, p[v] + m * hw * ratio, b]
        out.append(_ccw(np.asarray(tri)))
    if not closed and cap != "butt":
        for end, d in ((p[0], -dirs[0]), (p[-1], dirs[-1])):
            if cap == "round":
                out.append(_ccw(_disk(end, hw)))
            else:  # square
                nrm = np.array([-d[1], d[0]])
                out.append(_ccw(np.array([end + nrm * hw, end + nrm * hw + d * hw, end - nrm * hw + d * hw, end - nrm * hw])))
    return out


def path_coverage(path: list[list[float]], W: int, H: int, scale: float = 1.0, rule: str = "nonzero", tol: float = 0.03) -> np.ndarray:
    """路徑填色的覆蓋率。沒有閉合的子路徑照 SVG 的規則自動閉合。"""
    polys = [pts * scale for pts, _ in flatten_path(path, tol / max(scale, 1e-6)) if len(pts) >= 3]
    return coverage(polys, W, H, rule)


def stroke_coverage(path: list[list[float]], width: float, W: int, H: int, scale: float = 1.0, cap: str = "butt", join: str = "miter", tol: float = 0.03) -> np.ndarray:
    """路徑筆畫的覆蓋率。"""
    polys: list[np.ndarray] = []
    for pts, closed in flatten_path(path, tol / max(scale, 1e-6)):
        polys.extend(stroke_polygons(pts * scale, closed, width * scale, cap, join))
    return coverage(polys, W, H, "nonzero")


# ---------------------------------------------------------------- 顏色與合成


def gradient_colors(kind: str, coords: list[float], stops: list[tuple[float, tuple]], xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """漸層在各點 (xs, ys) 的顏色（float32，0 到 255）。色標在 sRGB 數值上線性內插，範圍外取最近的色標。"""
    if kind == "radial":
        cx, cy, r = coords[:3]
        t = np.hypot(xs - cx, ys - cy) / max(r, 1e-9)
    else:
        x0, y0, x1, y1 = coords[:4]
        dx, dy = x1 - x0, y1 - y0
        t = ((xs - x0) * dx + (ys - y0) * dy) / max(dx * dx + dy * dy, 1e-12)
    t = np.clip(t, 0.0, 1.0)
    offs = np.asarray([s[0] for s in stops], dtype=np.float64)
    cols = np.asarray([s[1] for s in stops], dtype=np.float64)
    return np.stack([np.interp(t, offs, cols[:, c]) for c in range(3)], axis=-1).astype(np.float32)


def render_paths(paths, size: tuple[int, int], scale: float = 1.0) -> np.ndarray:
    """由下到上把路徑畫成 RGBA（uint8，未預乘）。paths 的每一項要有 TracedPath 的欄位。"""
    W, H = int(round(size[0] * scale)), int(round(size[1] * scale))
    rgb = np.zeros((H, W, 3), dtype=np.float32)  # 預乘透明度的顏色
    alpha = np.zeros((H, W), dtype=np.float32)

    def comp(cov: np.ndarray, color: np.ndarray):
        nonlocal rgb, alpha
        a = cov[..., None]
        rgb = rgb * (1.0 - a) + color * a
        alpha = alpha + cov * (1.0 - alpha)

    yy = xx = None
    for p in paths:
        has_fill = p.fill is not None or p.gradient is not None
        if has_fill:
            cov = path_coverage(p.path, W, H, scale, p.fill_rule)
            if p.gradient is not None:
                if yy is None:
                    yy, xx = np.mgrid[0:H, 0:W]
                    yy = (yy + 0.5) / scale
                    xx = (xx + 0.5) / scale
                col = gradient_colors(p.gradient.kind, list(p.gradient.coords), list(p.gradient.stops), xx, yy)
            else:
                col = np.asarray(p.fill, dtype=np.float32)
            comp(cov, col)
        if p.stroke is not None and p.stroke_width_px:
            cov = stroke_coverage(p.path, float(p.stroke_width_px), W, H, scale, p.stroke_cap, p.stroke_join)
            comp(cov, np.asarray(p.stroke, dtype=np.float32))
    out = np.zeros((H, W, 4), dtype=np.uint8)
    nz = alpha > 1e-6
    col = np.zeros_like(rgb)
    col[nz] = rgb[nz] / alpha[nz][:, None]
    out[..., :3] = np.clip(np.round(col), 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(np.round(alpha * 255.0), 0, 255).astype(np.uint8)
    return out


def composite_over(rgba: np.ndarray, background: Optional[tuple]) -> np.ndarray:
    """把 RGBA 疊在底色上，回傳 RGB（float32）。background 為 None 時底色當成白色。"""
    bg = np.asarray(background if background is not None else (255, 255, 255), dtype=np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return rgba[..., :3].astype(np.float32) * a + bg * (1.0 - a)
