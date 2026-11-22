"""描圖向量化的幾何：次像素輪廓、角點、直線／圓弧／三次貝茲擬合、基本幾何辨識。

輸入是「覆蓋率場」（每個像素被某個區域蓋住的比例，0 到 1），輸出是節點盡量少的路徑。
  trace_coverage   取 0.5 等值線：沿像素之間的裂縫走一圈，每條裂縫給一個次像素點。
                   邊緣兩側在視窗內都飽和時用「部分面積」公式（沿法線方向把覆蓋率加總，直線邊緣是精確值）；
                   否則在相鄰像素中心之間線性內插。
  fit_primitive    整圈輪廓先試基本幾何（圓、橢圓、矩形、圓角矩形），容許只給看得到的那一部分點（被遮住時補全用）。
  fit_contour      其餘輪廓：找角點與直線段，角點之間先試直線、再試圓弧、最後用 Schneider 的最小平方三次貝茲
                   （Graphics Gems 的 FitCurves 做法，自行以 NumPy 改寫），平滑相接處共用切線。
座標約定：像素 i 佔 [i, i+1)，中心在 i+0.5；路徑編碼與 ShapeElement.path 相同。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .types import TracedPrimitive
from .vector_raster import ellipse_path, polygon_path, rounded_rect_path

# ---------------------------------------------------------------- 次像素輪廓


@dataclass
class Contour:
    points: np.ndarray  # (N, 2) 閉合折線（不重複起點），x、y
    area: float  # 有號面積：正的是外圈，負的是洞
    inside: np.ndarray  # (N, 2) 每個點內側像素的 (y, x)
    outside: np.ndarray  # (N, 2) 每個點外側像素的 (y, x)（可能在影像外）


def trace_coverage(c: np.ndarray, sat_eps: float = 0.1, min_points: int = 4) -> list[Contour]:
    """覆蓋率場的 0.5 等值線。回傳每一圈（外圈面積為正、洞為負）。"""
    H, W = c.shape
    pad = 3
    Cp = np.zeros((H + 2 * pad, W + 2 * pad), dtype=np.float32)
    Cp[pad:-pad, pad:-pad] = c
    M = Cp >= 0.5
    if not M.any():
        return []
    Hp, Wp = M.shape
    stride = Wp + 1
    E = np.zeros_like(M); E[1:, :] = M[1:, :] & ~M[:-1, :]
    Wk = np.zeros_like(M); Wk[1:, :] = M[:-1, :] & ~M[1:, :]
    S = np.zeros_like(M); S[:, 1:] = M[:, :-1] & ~M[:, 1:]
    N = np.zeros_like(M); N[:, 1:] = M[:, 1:] & ~M[:, :-1]
    ye, xe = np.nonzero(E); ys, xs = np.nonzero(S); yw, xw = np.nonzero(Wk); yn, xn = np.nonzero(N)
    # 方向：0 東、1 南、2 西、3 北（內側永遠在行進方向的右手邊）
    start = np.concatenate([ye * stride + xe, ys * stride + xs, yw * stride + xw + 1, (yn + 1) * stride + xn])
    end = np.concatenate([ye * stride + xe + 1, (ys + 1) * stride + xs, yw * stride + xw, yn * stride + xn])
    dirs = np.concatenate([np.zeros(len(ye), np.int64), np.ones(len(ys), np.int64), np.full(len(yw), 2, np.int64), np.full(len(yn), 3, np.int64)])
    iy = np.concatenate([ye, ys, yw - 1, yn]); ix = np.concatenate([xe, xs - 1, xw, xn])
    oy = np.concatenate([ye - 1, ys, yw, yn]); ox = np.concatenate([xe, xs, xw, xn - 1])
    n = len(start)
    out = np.full((4, (Hp + 1) * stride), -1, dtype=np.int64)
    out[dirs, start] = np.arange(n)
    nxt = out[(dirs + 1) % 4, end]  # 先右轉（內側以四連通看待）
    alt = out[dirs, end]
    nxt = np.where(nxt >= 0, nxt, alt)
    alt = out[(dirs + 3) % 4, end]
    nxt = np.where(nxt >= 0, nxt, alt)
    # 每條裂縫的次像素點
    uy, ux = oy - iy, ox - ix
    V = np.stack([Cp[iy + k * uy, ix + k * ux] for k in (-2, -1, 0, 1, 2, 3)], axis=1).astype(np.float64)
    cp, cq = V[:, 2], V[:, 3]
    t_lin = (cp - 0.5) / np.maximum(cp - cq, 1e-6)
    hi, lo = 1.0 - sat_eps, sat_eps
    s = np.where(V[:, 2] >= hi, 0, np.where(V[:, 1] >= hi, -1, np.where(V[:, 0] >= hi, -2, -9)))
    z = np.where(V[:, 3] <= lo, 1, np.where(V[:, 4] <= lo, 2, np.where(V[:, 5] <= lo, 3, 9)))
    cs0 = np.concatenate([np.zeros((n, 1)), np.cumsum(V, axis=1)], axis=1)
    ok = (s > -9) & (z < 9)
    rows = np.arange(n)
    # 由內側最近的飽和像素（位置 s）加總到外側最近的空像素（位置 z），兩端用實際值而不是當成 1 與 0，避免截斷造成的偏差
    t_area = s - 0.5 + cs0[rows, np.clip(z + 3, 0, 6)] - cs0[rows, np.clip(s + 2, 0, 6)]
    t = np.clip(np.where(ok, t_area, t_lin), 0.0, 1.0)
    px = ix + 0.5 + t * ux - pad
    py = iy + 0.5 + t * uy - pad
    # 走出每一圈
    nxt_l = nxt.tolist()
    seen = bytearray(n)
    contours: list[Contour] = []
    for s0 in range(n):
        if seen[s0]:
            continue
        cyc = []
        i = s0
        while not seen[i]:
            seen[i] = 1
            cyc.append(i)
            i = nxt_l[i]
        if len(cyc) < min_points:
            continue
        idx = np.asarray(cyc, dtype=np.int64)
        pts = np.stack([px[idx], py[idx]], axis=1)
        x, y = pts[:, 0], pts[:, 1]
        area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        contours.append(Contour(points=pts, area=area, inside=np.stack([iy[idx] - pad, ix[idx] - pad], axis=1), outside=np.stack([oy[idx] - pad, ox[idx] - pad], axis=1)))
    return contours


# ---------------------------------------------------------------- 小工具


def resample_closed(P: np.ndarray, step: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """把閉合折線重新取樣成等弧長的點。回傳（點、每個點在原折線上的位置：浮點序號）。"""
    Q = np.vstack([P, P[:1]])
    seg = np.hypot(*(Q[1:] - Q[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(cum[-1])
    m = max(4, int(round(L / step)))
    s = np.arange(m) * (L / m)
    x = np.interp(s, cum, Q[:, 0])
    y = np.interp(s, cum, Q[:, 1])
    pos = np.interp(s, cum, np.arange(len(Q), dtype=np.float64))
    return np.stack([x, y], axis=1), pos


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = float(np.hypot(v[0], v[1]))
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def _tls_line(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """全最小平方直線。回傳（重心、單位方向、各點的有號垂直距離）。方向與點的先後順序一致。"""
    c = pts.mean(axis=0)
    d = pts - c
    cov = d.T @ d
    w, v = np.linalg.eigh(cov)
    u = v[:, 1]
    if np.dot(pts[-1] - pts[0], u) < 0:
        u = -u
    nrm = np.array([-u[1], u[0]])
    return c, u, d @ nrm


def _line_intersect(c1, u1, c2, u2) -> Optional[np.ndarray]:
    den = u1[0] * u2[1] - u1[1] * u2[0]
    if abs(den) < 1e-9:
        return None
    w = c2 - c1
    s = (w[0] * u2[1] - w[1] * u2[0]) / den
    return c1 + s * u1


def fit_circle_points(pts: np.ndarray) -> tuple[float, float, float]:
    """最小平方圓擬合（代數解後再做幾何精修）。"""
    x, y = pts[:, 0], pts[:, 1]
    mx, my = x.mean(), y.mean()
    u, v = x - mx, y - my
    A = np.stack([u, v, np.ones_like(u)], axis=1)
    b = u * u + v * v
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-12))
    for _ in range(3):  # 高斯牛頓：最小化幾何距離
        dx, dy = u - cx, v - cy
        d = np.hypot(dx, dy)
        d = np.maximum(d, 1e-9)
        J = np.stack([-dx / d, -dy / d, -np.ones_like(d)], axis=1)
        res = d - r
        step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        cx, cy, r = cx + step[0], cy + step[1], r + step[2]
    return float(cx + mx), float(cy + my), float(r)


# ---------------------------------------------------------------- Schneider 三次貝茲擬合


def _bez(ctrl: np.ndarray, u: np.ndarray) -> np.ndarray:
    u = u[:, None]
    return ((1 - u) ** 3) * ctrl[0] + 3 * ((1 - u) ** 2) * u * ctrl[1] + 3 * (1 - u) * (u ** 2) * ctrl[2] + (u ** 3) * ctrl[3]


def _chord_param(pts: np.ndarray) -> np.ndarray:
    d = np.hypot(*(pts[1:] - pts[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    return cum / max(cum[-1], 1e-12)


def _generate(pts: np.ndarray, u: np.ndarray, t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    p0, p3 = pts[0], pts[-1]
    b0, b1, b2, b3 = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3
    A1 = b1[:, None] * t1[None, :]
    A2 = b2[:, None] * t2[None, :]
    c00, c01, c11 = float(np.sum(A1 * A1)), float(np.sum(A1 * A2)), float(np.sum(A2 * A2))
    tmp = pts - ((b0 + b1)[:, None] * p0 + (b2 + b3)[:, None] * p3)
    x0, x1 = float(np.sum(A1 * tmp)), float(np.sum(A2 * tmp))
    det = c00 * c11 - c01 * c01
    seg = float(np.hypot(*(p3 - p0)))
    if abs(det) > 1e-12:
        al = (x0 * c11 - x1 * c01) / det
        ar = (c00 * x1 - c01 * x0) / det
    else:
        al = ar = seg / 3.0
    eps = 1e-6 * seg
    if al < eps or ar < eps:
        al = ar = seg / 3.0
    return np.array([p0, p0 + t1 * al, p3 + t2 * ar, p3])


def _max_error(pts: np.ndarray, ctrl: np.ndarray, u: np.ndarray) -> tuple[float, int]:
    d = _bez(ctrl, u) - pts
    e = d[:, 0] ** 2 + d[:, 1] ** 2
    if len(e) > 2:
        i = int(np.argmax(e[1:-1])) + 1
    else:
        i = len(e) // 2
    return float(math.sqrt(e[i])), i


def _reparam(pts: np.ndarray, ctrl: np.ndarray, u: np.ndarray) -> np.ndarray:
    q = _bez(ctrl, u)
    c1 = 3 * (ctrl[1:] - ctrl[:-1])
    c2 = 2 * (c1[1:] - c1[:-1])
    uu = u[:, None]
    q1 = ((1 - uu) ** 2) * c1[0] + 2 * (1 - uu) * uu * c1[1] + (uu ** 2) * c1[2]
    q2 = (1 - uu) * c2[0] + uu * c2[1]
    diff = q - pts
    num = np.sum(diff * q1, axis=1)
    den = np.sum(q1 * q1, axis=1) + np.sum(diff * q2, axis=1)
    out = np.where(np.abs(den) > 1e-12, u - num / np.where(np.abs(den) > 1e-12, den, 1.0), u)
    out = np.clip(out, 0.0, 1.0)
    out[0], out[-1] = 0.0, 1.0
    return np.maximum.accumulate(out)


def _try_cubic(pts: np.ndarray, t1: np.ndarray, t2: np.ndarray, tol: float, iters: int = 4) -> tuple[np.ndarray, float, int]:
    if len(pts) == 2:
        d = float(np.hypot(*(pts[1] - pts[0]))) / 3.0
        return np.array([pts[0], pts[0] + t1 * d, pts[1] + t2 * d, pts[1]]), 0.0, 0
    u = _chord_param(pts)
    ctrl = _generate(pts, u, t1, t2)
    err, split = _max_error(pts, ctrl, u)
    if err <= tol:
        return ctrl, err, split
    if err <= tol * 4.0:
        for _ in range(iters):
            u = _reparam(pts, ctrl, u)
            ctrl = _generate(pts, u, t1, t2)
            err, split = _max_error(pts, ctrl, u)
            if err <= tol:
                break
    return ctrl, err, split


def _center_tangent(pts: np.ndarray, i: int, k: int = 2) -> np.ndarray:
    a = pts[max(0, i - k)]
    b = pts[min(len(pts) - 1, i + k)]
    return _unit(a - b)  # 指向前一段（Schneider 的慣例：右端切線朝向曲線內部）


def fit_cubics(pts: np.ndarray, t1: Optional[np.ndarray], t2: Optional[np.ndarray], tol: float, depth: int = 0) -> list[np.ndarray]:
    """Schneider 擬合：把一串點擬合成一段或多段三次貝茲（每段為 4 個控制點）。t1 是起點切線（朝前）、t2 是終點切線（朝後）。"""
    n = len(pts)
    if t1 is None:
        t1 = _unit(pts[min(n - 1, 2)] - pts[0])
    if t2 is None:
        t2 = _unit(pts[max(0, n - 3)] - pts[-1])
    ctrl, err, split = _try_cubic(pts, t1, t2, tol)
    if err <= tol or n <= 3 or depth > 24:
        return [ctrl]
    split = int(min(max(split, 1), n - 2))
    tc = _center_tangent(pts, split)
    return fit_cubics(pts[: split + 1], t1, tc, tol, depth + 1) + fit_cubics(pts[split:], -tc, t2, tol, depth + 1)


def _merge_cubics(pts: np.ndarray, segs: list[np.ndarray], tol: float) -> list[np.ndarray]:
    """Schneider 是對半切的貪婪法，常多切；這裡把相鄰兩段合起來重試，能合就合（節點更少）。"""
    if len(segs) < 2:
        return segs
    # 每段對應到 pts 的範圍：用端點找最近的序號
    idx = [0]
    for s in segs:
        d = np.hypot(*(pts - s[3]).T)
        lo = idx[-1]
        idx.append(lo + int(np.argmin(d[lo:])))
    idx[-1] = len(pts) - 1
    segs = list(segs)
    i = 0
    while i < len(segs) - 1:
        a, b = idx[i], idx[i + 2]
        t1 = _unit(segs[i][1] - segs[i][0]) if np.hypot(*(segs[i][1] - segs[i][0])) > 1e-9 else None
        t2 = _unit(segs[i + 1][2] - segs[i + 1][3]) if np.hypot(*(segs[i + 1][2] - segs[i + 1][3])) > 1e-9 else None
        sub = pts[a : b + 1]
        if len(sub) >= 3 and t1 is not None and t2 is not None:
            ctrl, err, _ = _try_cubic(sub, t1, t2, tol, iters=6)
            if err <= tol:
                segs[i : i + 2] = [ctrl]
                idx[i + 1 : i + 2] = []
                continue
        i += 1
    return segs


def _arc_segments(A: np.ndarray, B: np.ndarray, cx: float, cy: float, r: float, sweep: float, tol: float) -> list[np.ndarray]:
    """圓弧（由 A 到 B，掃過 sweep 弧度，有號）轉成三次貝茲。每段的角度依容許誤差決定，最多 120 度。"""
    a0 = math.atan2(A[1] - cy, A[0] - cx)
    max_ang = math.radians(30)
    for deg in (120, 90, 60, 45, 30):
        th = math.radians(deg)
        err = r * (4.0 / 27.0) * (math.sin(th / 4) ** 6) / (math.cos(th / 4) ** 2)
        if err <= tol:
            max_ang = th
            break
    n = max(1, int(math.ceil(abs(sweep) / max_ang - 1e-9)))
    dth = sweep / n
    k = (4.0 / 3.0) * math.tan(dth / 4.0)
    segs = []
    for i in range(n):
        t0, t1 = a0 + dth * i, a0 + dth * (i + 1)
        p0 = np.array([cx + r * math.cos(t0), cy + r * math.sin(t0)])
        p3 = np.array([cx + r * math.cos(t1), cy + r * math.sin(t1)])
        c1 = p0 + k * r * np.array([-math.sin(t0), math.cos(t0)])
        c2 = p3 - k * r * np.array([-math.sin(t1), math.cos(t1)])
        segs.append(np.array([p0, c1, c2, p3]))
    # 端點釘在 A、B（與鄰段共用的節點）：把微小的偏差線性分攤到各段
    da, db = A - segs[0][0], B - segs[-1][3]
    for i, s in enumerate(segs):
        w0, w1 = i / n, (i + 1) / n
        o0, o1 = da * (1 - w0) + db * w0, da * (1 - w1) + db * w1
        s[0] += o0; s[1] += o0; s[2] += o1; s[3] += o1
    return segs


# ---------------------------------------------------------------- 角點與直線段


def _rdp_closed(Q: np.ndarray, eps: float) -> list[int]:
    """閉合折線的 Douglas–Peucker 簡化，回傳保留點的序號（遞增）。"""
    n = len(Q)
    i0 = 0
    i1 = int(np.argmax(np.hypot(*(Q - Q[0]).T)))
    if i1 == 0:
        return [0]
    keep = {i0, i1}
    stack = [(i0, i1), (i1, n)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        pa, pb = Q[a], Q[b % n]
        seg = pb - pa
        L = float(np.hypot(*seg))
        mid = Q[a + 1 : b]
        if L < 1e-9:
            d = np.hypot(*(mid - pa).T)
        else:
            d = np.abs(seg[0] * (mid[:, 1] - pa[1]) - seg[1] * (mid[:, 0] - pa[0])) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            m = a + 1 + k
            keep.add(m)
            stack.append((a, m))
            stack.append((m, b))
    return sorted(keep)


def _turning(Q: np.ndarray, k: int) -> np.ndarray:
    """每個點前後各取 k 個點的弦，兩條弦的夾角（度，有號：順著行進方向右轉為正）。"""
    a = Q - np.roll(Q, k, axis=0)
    b = np.roll(Q, -k, axis=0) - Q
    cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    dot = a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1]
    return np.degrees(np.arctan2(cross, dot))


@dataclass
class _Run:
    i0: int  # 起點序號（在 Q 上；可大於等於 len(Q) 表示跨過接縫）
    i1: int  # 終點序號（含）
    c: np.ndarray  # 直線上的一點
    u: np.ndarray  # 單位方向（順著輪廓行進方向）
    err: float = 0.0


def _find_runs(Q: np.ndarray, tol_line: float, min_len: float, trim: float) -> list[_Run]:
    """輪廓上的直線段。先用 Douglas–Peucker 找候選，再以中段的全最小平方直線為準，往兩端延伸到偏離為止。

    曲線上「延伸到偏離為止」的一段一定是弓形（中段鼓起），真正的直線中段只有雜訊，所以用中段的弓高來分辨；
    這個判斷與曲率半徑無關，大圓不會被切成一段段直線。
    """
    n = len(Q)
    if n < 8:
        return []
    keep = _rdp_closed(Q, max(0.5, tol_line * 2.0))
    if len(keep) < 2:
        return []
    Q3 = np.vstack([Q, Q, Q])
    lim = 0.6 * tol_line
    bow_tol = max(0.04, 0.3 * lim)
    runs: list[_Run] = []
    for a, b in zip(keep, keep[1:] + [keep[0] + n]):
        if b - a < max(4, int(min_len)):
            continue
        t = int(math.ceil(trim))
        i0, i1 = a + t, b - t
        if i1 - i0 < 3:
            continue
        ok = True
        for _ in range(2):
            w = max(1, int(round(0.15 * (i1 - i0))))
            c, u, _d = _tls_line(Q3[n + i0 + w : n + i1 - w + 1])
            nrm = np.array([-u[1], u[0]])
            mid = (i0 + i1) // 2
            span = b - a
            lo, hi = mid - span, mid + span
            dev = np.abs((Q3[n + lo : n + hi + 1] - c) @ nrm)
            bad = np.nonzero(dev > lim)[0] + lo
            left = bad[bad < mid]
            right = bad[bad > mid]
            i0 = int(left.max()) + 1 if len(left) else lo
            i1 = int(right.min()) - 1 if len(right) else hi
            if i1 - i0 < max(4, int(min_len)) or i1 - i0 >= n:
                ok = False
                break
        if not ok:
            continue
        pts = Q3[n + i0 : n + i1 + 1]
        if float(np.hypot(*(pts[-1] - pts[0]))) < min_len:
            continue
        w = max(1, int(round(0.1 * (i1 - i0))))
        core = pts[w:-w] if len(pts) > 2 * w + 4 else pts
        c, u, d = _tls_line(core)
        if float(np.max(np.abs(d))) > tol_line:
            continue
        s = (core - c) @ u
        half = max(float(np.max(np.abs(s))), 1e-6)
        if len(core) >= 4:
            coef = np.polyfit(s / half, d, 2)
            if abs(coef[0]) > bow_tol:  # 中段鼓起來的是曲線
                continue
        # 兩端收到幾乎貼在直線上的地方（與圓弧相切時，接點不要伸進圓弧）
        nrm = np.array([-u[1], u[0]])
        dev = np.abs((pts - c) @ nrm)
        tight = 0.4 * lim
        while i1 - i0 > 4 and dev[0] > tight:
            i0 += 1; dev = dev[1:]
        while i1 - i0 > 4 and dev[-1] > tight:
            i1 -= 1; dev = dev[:-1]
        while i0 < 0:
            i0 += n; i1 += n
        while i0 >= n:
            i0 -= n; i1 -= n
        runs.append(_Run(i0=i0, i1=i1, c=c, u=u, err=float(np.max(np.abs(d)))))
    # 相鄰兩段在角點附近重疊時，各退一半
    runs.sort(key=lambda r: r.i0)
    for k in range(len(runs)):
        ra, rb = runs[k], runs[(k + 1) % len(runs)]
        if ra is rb:
            continue
        b0 = rb.i0 + (n if k == len(runs) - 1 else 0)
        if b0 <= ra.i1:
            m = (b0 + ra.i1) // 2
            ra.i1 = m
            rb.i0 = m + 1 - (n if k == len(runs) - 1 else 0)
            if rb.i0 < 0:
                rb.i0 += n; rb.i1 += n
    return [r for r in runs if r.i1 - r.i0 >= 3]


def _find_corners(Q: np.ndarray, k1: int, k2: int, min_deg: float) -> list[int]:
    """角點：轉角集中在很短的弧長內（短弦量到的轉角與長弦量到的差不多），而且是局部最大。"""
    n = len(Q)
    if n < 2 * k2 + 2:
        return []
    a1 = np.abs(_turning(Q, k1))
    a2 = np.abs(_turning(Q, k2))
    cand = (a2 >= min_deg) & (a1 >= 0.6 * a2)
    if not cand.any():
        return []
    score = a1 + a2
    out = []
    w = k2
    idx = np.nonzero(cand)[0]
    for i in idx:
        win = [(i + o) % n for o in range(-w, w + 1)]
        if score[i] >= score[win].max() - 1e-9:
            if out and (i - out[-1]) <= w:
                if score[i] > score[out[-1]]:
                    out[-1] = int(i)
                continue
            out.append(int(i))
    if len(out) >= 2 and (out[0] + n - out[-1]) <= w:
        if score[out[0]] >= score[out[-1]]:
            out.pop()
        else:
            out.pop(0)
    return out


# ---------------------------------------------------------------- 一般輪廓的擬合


@dataclass
class FitInfo:
    lines: int = 0
    arcs: int = 0
    beziers: int = 0
    corners: int = 0
    max_err: float = 0.0
    polygon: Optional[np.ndarray] = None  # 全部是直線時的頂點


def _snap_axis(u: np.ndarray, c: np.ndarray, length: float, tol: float) -> tuple[np.ndarray, bool]:
    """接近水平或垂直、而且偏差在量測誤差之內的直線，扶正。"""
    for axis in (np.array([1.0, 0.0]), np.array([0.0, 1.0])):
        dot = float(u @ axis)
        if abs(dot) > 0.9:
            off = abs(float(u[0] * axis[1] - u[1] * axis[0])) * length / 2.0  # 端點相對於扶正後直線的偏移
            if off <= tol:
                return axis * (1.0 if dot > 0 else -1.0), True
    return u, False


def fit_contour(P: np.ndarray, tol: float, opts: Optional[dict] = None) -> tuple[list[list[float]], FitInfo]:
    """把一圈次像素點擬合成路徑（起筆、若干直線與曲線、閉合）。"""
    o = {"tol_line": tol, "min_line": 6.0, "corner_deg": 28.0, "corner_trim": 1.5, "snap_tol": 0.3, "k1": 2, "k2": 4, "arc": True}
    if opts:
        o.update(opts)
    info = FitInfo()
    Q, _ = resample_closed(P, 1.0)
    n = len(Q)
    if n < 8:
        keep = _rdp_closed(Q, max(0.3, tol))
        pts = Q[keep] if len(keep) >= 3 else Q
        info.lines = len(pts)
        info.polygon = pts
        return polygon_path(pts), info
    runs = _find_runs(Q, o["tol_line"], o["min_line"], o["corner_trim"])
    corners = _find_corners(Q, int(o["k1"]), int(o["k2"]), o["corner_deg"])
    # 扶正接近水平、垂直的直線段
    for r in runs:
        length = float(np.hypot(*(Q[r.i1 % n] - Q[r.i0 % n])))
        r.u, _ = _snap_axis(r.u, r.c, length, o["snap_tol"])
    runs.sort(key=lambda r: r.i0)
    # 直線段之內與兩端附近的角點併入直線段的端點
    def near_run(i: int) -> bool:
        for r in runs:
            for a, b in ((r.i0, r.i1), (r.i0 - n, r.i1 - n), (r.i0 + n, r.i1 + n)):
                if a - 3 <= i <= b + 3:
                    return True
        return False

    corners = [i for i in corners if not near_run(i)]
    info.corners = len(corners)

    if not runs and not corners:
        # 整圈都是平滑曲線
        circ = _closed_smooth(Q, tol, o)
        info.beziers = len(circ)
        return _segs_to_path(circ), info

    # 把直線段與角點排成一圈的「事件」；兩個事件之間是曲線段（可能長度為零）
    events: list[tuple[int, int, Optional[_Run]]] = []  # （起、迄序號、直線段或 None 表示角點）
    for r in runs:
        events.append((r.i0, r.i1, r))
    for i in corners:
        events.append((i, i, None))
    events.sort(key=lambda e: e[0])
    m = len(events)
    Q3 = np.vstack([Q, Q, Q])

    def joint_between(ea, eb) -> tuple[Optional[np.ndarray], bool]:
        """兩條直線段之間沒有（或幾乎沒有）曲線時，交點就是角點。回傳（交點、是否直接相接）。"""
        ra, rb = ea[2], eb[2]
        gap = (eb[0] - ea[1]) % n
        if ra is not None and rb is not None and gap <= 2 * o["corner_trim"] + 3:
            x = _line_intersect(ra.c, ra.u, rb.c, rb.u)
            mid = Q3[n + ea[1] + gap // 2]
            if x is not None and float(np.hypot(*(x - mid))) <= max(3.0, gap * 1.5):
                return x, True
        return None, False

    # 每個事件的進入點與離開點
    enter: list[Optional[np.ndarray]] = [None] * m
    leave: list[Optional[np.ndarray]] = [None] * m
    direct = [False] * m  # 事件 i 與事件 i+1 是否直接相接
    for i in range(m):
        ea, eb = events[i], events[(i + 1) % m]
        if m == 1:
            break
        x, ok = joint_between(ea, eb)
        if ok:
            leave[i] = x
            enter[(i + 1) % m] = x
            direct[i] = True
    for i, e in enumerate(events):
        r = e[2]
        if r is None:
            p = Q[e[0] % n]
            enter[i] = p if enter[i] is None else enter[i]
            leave[i] = p if leave[i] is None else leave[i]
        else:
            if enter[i] is None:
                enter[i] = r.c + r.u * float((Q[r.i0 % n] - r.c) @ r.u)
            if leave[i] is None:
                leave[i] = r.c + r.u * float((Q[r.i1 % n] - r.c) @ r.u)
    segs: list[np.ndarray] = []  # 每段：直線為 (2, 2)，曲線為 (4, 2)
    for i in range(m):
        e = events[i]
        r = e[2]
        if r is not None:
            segs.append(np.array([enter[i], leave[i]]))
            info.lines += 1
        if m == 1:
            nxt_e, a_idx, b_idx = e, e[1], e[0] + n
        else:
            nxt_e = events[(i + 1) % m]
            if direct[i]:
                continue
            a_idx = e[1]
            b_idx = nxt_e[0]
            while b_idx <= a_idx:
                b_idx += n
        A = leave[i]
        B = enter[(i + 1) % m] if m > 1 else enter[i]
        pts = Q3[n + a_idx : n + b_idx + 1].copy() if a_idx >= 0 else Q3[2 * n + a_idx : 2 * n + b_idx + 1].copy()
        if len(pts) < 2:
            if float(np.hypot(*(B - A))) > 1e-6:
                segs.append(np.array([A, B])); info.lines += 1
            continue
        pts[0], pts[-1] = A, B
        # 與直線段平滑相接時共用切線
        t1 = t2 = None
        if r is not None and len(pts) >= 4:
            ch = _unit(pts[min(len(pts) - 1, 3)] - pts[0])
            if float(ch @ r.u) > math.cos(math.radians(25)):
                t1 = r.u.copy()
        rn = nxt_e[2]
        if rn is not None and len(pts) >= 4:
            ch = _unit(pts[-1] - pts[max(0, len(pts) - 4)])
            if float(ch @ rn.u) > math.cos(math.radians(25)):
                t2 = -rn.u
        trim_a = r is None or t1 is None
        trim_b = rn is None or t2 is None
        piece = _fit_piece(pts, t1, t2, tol, o, info, trim_a, trim_b)
        segs.extend(piece)
    if len(segs) == 0:
        circ = _closed_smooth(Q, tol, o)
        info.beziers = len(circ)
        return _segs_to_path(circ), info
    if info.beziers == 0 and info.arcs == 0:
        info.polygon = np.array([s[0] for s in segs])
    return _segs_to_path(segs), info


def _fit_piece(pts: np.ndarray, t1, t2, tol: float, o: dict, info: FitInfo, trim_a: bool, trim_b: bool) -> list[np.ndarray]:
    """兩個節點之間的一段：直線 → 圓弧 → 三次貝茲。端點固定在 pts 的頭尾。"""
    A, B = pts[0], pts[-1]
    chord = float(np.hypot(*(B - A)))
    n = len(pts)
    # 角點附近的點被反鋸齒磨圓，不拿來擬合（端點本身保留）
    t = int(round(o["corner_trim"]))
    lo = 1 + (t if trim_a else 0)
    hi = n - 1 - (t if trim_b else 0)
    core = pts[lo:hi] if hi - lo >= 2 else pts[1:-1]
    fit_pts = np.vstack([A[None], core, B[None]]) if len(core) else pts
    if chord > 1e-6 and len(core):
        d = np.abs((B - A)[0] * (core[:, 1] - A[1]) - (B - A)[1] * (core[:, 0] - A[0])) / chord
        if float(d.max()) <= o["tol_line"] * (1.0 if t1 is None and t2 is None else 0.5):
            info.lines += 1
            info.max_err = max(info.max_err, float(d.max()))
            return [np.array([A, B])]
    elif chord > 1e-6:
        info.lines += 1
        return [np.array([A, B])]
    cub = fit_cubics(fit_pts, t1, t2, tol)
    cub = _merge_cubics(fit_pts, cub, tol)
    best = cub
    kind = "bez"
    if o.get("arc", True) and len(core) >= 5:
        cx, cy, r = fit_circle_points(fit_pts)
        if 1.0 < r < 1e4:
            res = np.abs(np.hypot(fit_pts[:, 0] - cx, fit_pts[:, 1] - cy) - r)
            if float(res.max()) <= tol:
                ang = np.unwrap(np.arctan2(fit_pts[:, 1] - cy, fit_pts[:, 0] - cx))
                sweep = float(ang[-1] - ang[0])
                if abs(sweep) >= math.radians(20):
                    arcs = _arc_segments(A, B, cx, cy, r, sweep, tol * 0.5)
                    tan_ok = True
                    for tt, seg, end in ((t1, arcs[0], 0), (t2, arcs[-1], 1)):
                        if tt is not None:
                            v = _unit(seg[1] - seg[0]) if end == 0 else _unit(seg[2] - seg[3])
                            if float(v @ tt) < math.cos(math.radians(6)):
                                tan_ok = False
                    if tan_ok and len(arcs) <= len(cub):
                        best, kind = arcs, "arc"
    if kind == "arc":
        info.arcs += len(best)
    else:
        info.beziers += len(best)
    return best


def _closed_smooth(Q: np.ndarray, tol: float, o: dict) -> list[np.ndarray]:
    """沒有角點也沒有直線段的一圈平滑曲線：起點取在曲率最小的地方，頭尾共用切線。"""
    n = len(Q)
    k = min(4, max(1, n // 8))
    turn = np.abs(_turning(Q, k))
    turn_s = np.convolve(np.concatenate([turn[-3:], turn, turn[:3]]), np.ones(7) / 7.0, mode="valid")
    s = int(np.argmin(turn_s))
    R = np.roll(Q, -s, axis=0)
    pts = np.vstack([R, R[:1]])
    tan = _unit(R[min(n - 1, 2)] - R[-2 if n > 2 else -1])
    cub = fit_cubics(pts, tan, -tan, tol)
    return _merge_cubics(pts, cub, tol)


def _segs_to_path(segs: list[np.ndarray]) -> list[list[float]]:
    path: list[list[float]] = [[0, float(segs[0][0][0]), float(segs[0][0][1])]]
    for s in segs:
        if len(s) == 2:
            path.append([1, float(s[1][0]), float(s[1][1])])
        else:
            path.append([2, float(s[1][0]), float(s[1][1]), float(s[2][0]), float(s[2][1]), float(s[3][0]), float(s[3][1])])
    # 最後一段回到起點時，直線改由「閉合」表示（少一個重複的節點）
    last = path[-1]
    if int(last[0]) == 1 and len(path) > 2 and math.hypot(last[1] - path[0][1], last[2] - path[0][2]) < 1e-6:
        path.pop()
    path.append([3])
    return path


def _rdp_open(P: np.ndarray, eps: float) -> list[int]:
    keep = {0, len(P) - 1}
    stack = [(0, len(P) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        pa, pb = P[a], P[b]
        seg = pb - pa
        L = float(np.hypot(*seg))
        mid = P[a + 1 : b]
        if L < 1e-9:
            d = np.hypot(*(mid - pa).T)
        else:
            d = np.abs(seg[0] * (mid[:, 1] - pa[1]) - seg[1] * (mid[:, 0] - pa[0])) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            m = a + 1 + k
            keep.add(m)
            stack.append((a, m))
            stack.append((m, b))
    return sorted(keep)


def _corner_open(P: np.ndarray, i: int, min_deg: float, k1: int = 2, k2: int = 4) -> bool:
    """開放折線上的點 i 是不是角點（短弦與長弦量到的轉角差不多，表示轉角集中在很短的範圍內）。"""
    n = len(P)
    if i - k1 < 0 or i + k1 >= n:
        return False
    k2 = min(k2, i, n - 1 - i)

    def ang(k):
        a, b = P[i] - P[i - k], P[i + k] - P[i]
        return abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])))

    a1, a2 = ang(k1), ang(k2)
    return a2 >= min_deg and a1 >= 0.6 * a2


def fit_open(pts: np.ndarray, tol: float, opts: Optional[dict] = None) -> tuple[list[list[float]], FitInfo]:
    """把一條不閉合的中心線擬合成路徑（筆畫用）。

    整條是直線就輸出一段直線；否則先用 Douglas–Peucker 找轉折，逐段判斷是不是直線：
    相鄰兩條直線的交點當角點（中心線在轉角附近量不準，不直接用量到的點）；不是直線的相鄰段合成一段曲線再擬合。
    """
    o = {"tol_line": tol, "corner_deg": 28.0, "corner_trim": 1.0, "snap_tol": 0.3, "arc": True}
    if opts:
        o.update(opts)
    info = FitInfo()
    P = np.asarray(pts, dtype=np.float64)
    if len(P) < 2:
        return [], info
    A, B = P[0], P[-1]
    chord = float(np.hypot(*(B - A)))
    if len(P) == 2 or chord < 1e-6:
        info.lines = 1
        return [[0, float(A[0]), float(A[1])], [1, float(B[0]), float(B[1])]], info
    c, u, d = _tls_line(P)
    if float(np.max(np.abs(d))) <= o["tol_line"]:
        s = (P - c) @ u
        u2, _ = _snap_axis(u, c, float(s.max() - s.min()), o["snap_tol"])
        s = (P - c) @ u2
        a, b = c + u2 * float(s[0]), c + u2 * float(s[-1])
        info.lines = 1
        info.max_err = float(np.max(np.abs(d)))
        return [[0, float(a[0]), float(a[1])], [1, float(b[0]), float(b[1])]], info
    trim = int(round(o["corner_trim"]))
    idx = _rdp_open(P, float(o.get("rdp_eps", max(0.6, 2.0 * tol))))
    # 每段：是不是直線
    raw = []
    for a, b in zip(idx[:-1], idx[1:]):
        seg = P[a : b + 1]
        ta = trim if a > 0 else 0
        tb = trim if b < len(P) - 1 else 0
        core = seg[ta : len(seg) - tb] if len(seg) - ta - tb >= 3 else seg
        length = float(np.hypot(*(seg[-1] - seg[0])))
        cc, uu, dd = _tls_line(core) if len(core) >= 2 else (seg[0], _unit(seg[-1] - seg[0]), np.zeros(1))
        is_line = len(core) >= 2 and float(np.max(np.abs(dd))) <= o["tol_line"]
        raw.append({"a": a, "b": b, "line": is_line, "c": cc, "u": uu, "len": length})
    # 兩條直線之間夾著的極短片段是轉角被磨圓的部分：拿掉，讓兩條直線直接相交
    short = max(3.0, 2.0 * trim + 1.0)
    pieces = []
    for i, pc in enumerate(raw):
        if pc["len"] < short and 0 < i < len(raw) - 1 and raw[i - 1]["line"] and raw[i + 1]["line"] and raw[i - 1]["len"] >= short and raw[i + 1]["len"] >= short:
            continue
        pieces.append(pc)
    # 相鄰的非直線段合成一段曲線（中間沒有真正的角點時）；幾乎同向的相鄰直線也合併
    merged = [pieces[0]]
    for pc in pieces[1:]:
        last = merged[-1]
        if not last["line"] and not pc["line"] and last["b"] == pc["a"] and not _corner_open(P, pc["a"], o["corner_deg"]):
            last["b"] = pc["b"]
            last["len"] += pc["len"]
            continue
        if last["line"] and pc["line"] and float(last["u"] @ pc["u"]) > math.cos(math.radians(3.0)):
            seg = P[last["a"] : pc["b"] + 1]
            cc, uu, dd = _tls_line(seg)
            if float(np.max(np.abs(dd))) <= o["tol_line"]:
                last.update({"b": pc["b"], "c": cc, "u": uu, "len": last["len"] + pc["len"]})
                continue
        merged.append(pc)
    pieces = merged
    for pc in pieces:
        if pc["line"]:
            pc["u"], _ = _snap_axis(pc["u"], pc["c"], pc["len"], o["snap_tol"])
    # 節點
    nodes = [P[0].copy()]
    for i in range(len(pieces) - 1):
        pa, pb = pieces[i], pieces[i + 1]
        near = P[(pa["b"] + pb["a"]) // 2]
        x = None
        if pa["line"] and pb["line"]:
            x = _line_intersect(pa["c"], pa["u"], pb["c"], pb["u"])
            if x is not None and float(np.hypot(*(x - near))) > 3.0 + 2.0 * trim:
                x = None
        if x is None:
            x = near.copy()
            ln = pa if pa["line"] else (pb if pb["line"] else None)
            if ln is not None:
                x = ln["c"] + ln["u"] * float((x - ln["c"]) @ ln["u"])
        nodes.append(x)
    nodes.append(P[-1].copy())
    if pieces[0]["line"]:
        pc = pieces[0]
        nodes[0] = pc["c"] + pc["u"] * float((nodes[0] - pc["c"]) @ pc["u"])
    if pieces[-1]["line"]:
        pc = pieces[-1]
        nodes[-1] = pc["c"] + pc["u"] * float((nodes[-1] - pc["c"]) @ pc["u"])
    info.corners = len(pieces) - 1
    segs: list[np.ndarray] = []
    for i, pc in enumerate(pieces):
        A2, B2 = nodes[i], nodes[i + 1]
        if pc["line"]:
            segs.append(np.array([A2, B2])); info.lines += 1
            continue
        seg = P[pc["a"] : pc["b"] + 1].copy()
        seg[0], seg[-1] = A2, B2
        t1 = t2 = None
        if i > 0 and pieces[i - 1]["line"] and len(seg) >= 4:
            ch = _unit(seg[min(len(seg) - 1, 3)] - seg[0])
            if float(ch @ pieces[i - 1]["u"]) > math.cos(math.radians(25)):
                t1 = pieces[i - 1]["u"].copy()
        if i < len(pieces) - 1 and pieces[i + 1]["line"] and len(seg) >= 4:
            ch = _unit(seg[-1] - seg[max(0, len(seg) - 4)])
            if float(ch @ pieces[i + 1]["u"]) > math.cos(math.radians(25)):
                t2 = -pieces[i + 1]["u"]
        segs.extend(_fit_piece(seg, t1, t2, tol, o, info, i > 0 and t1 is None, i < len(pieces) - 1 and t2 is None))
    path: list[list[float]] = [[0, float(segs[0][0][0]), float(segs[0][0][1])]]
    for sg in segs:
        if len(sg) == 2:
            path.append([1, float(sg[1][0]), float(sg[1][1])])
        else:
            path.append([2, float(sg[1][0]), float(sg[1][1]), float(sg[2][0]), float(sg[2][1]), float(sg[3][0]), float(sg[3][1])])
    return path, info


# ---------------------------------------------------------------- 基本幾何


@dataclass
class PrimFit:
    prim: TracedPrimitive
    path: list[list[float]]
    max_err: float
    rms: float
    notes: list[str] = field(default_factory=list)


def _rrect_sdf(p: np.ndarray, pts: np.ndarray) -> np.ndarray:
    cx, cy, th, hx, hy, r = p
    ca, sa = math.cos(th), math.sin(th)
    dx, dy = pts[:, 0] - cx, pts[:, 1] - cy
    lx = np.abs(dx * ca + dy * sa)
    ly = np.abs(-dx * sa + dy * ca)
    r = min(max(r, 0.0), hx, hy)
    qx, qy = lx - (hx - r), ly - (hy - r)
    return np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0) - r


def _ellipse_dist(pts: np.ndarray, cx, cy, a, b, th) -> np.ndarray:
    """點到橢圓的近似有號距離（Sampson 距離）。"""
    ca, sa = math.cos(th), math.sin(th)
    dx, dy = pts[:, 0] - cx, pts[:, 1] - cy
    x = dx * ca + dy * sa
    y = -dx * sa + dy * ca
    f = (x / a) ** 2 + (y / b) ** 2 - 1.0
    g = 2.0 * np.hypot(x / (a * a), y / (b * b))
    return f / np.maximum(g, 1e-12)


def fit_primitive(pts: np.ndarray, tol: float, opts: Optional[dict] = None, full: bool = True) -> Optional[PrimFit]:
    """用輪廓點（次像素）試基本幾何，由簡到繁：圓、矩形與圓角矩形、橢圓。

    full=False 表示 pts 只是看得到的那一部分（其餘被上層遮住），這時要求更嚴，而且每一邊都要有點支持。
    擬合不好回傳 None。規則化（扶正、圓角歸零、正圓）只在偏差小於量測誤差時才做。
    """
    o = {"snap_tol": 0.3, "min_size": 5.0, "corner_blur": 1.0}
    if opts:
        o.update(opts)
    P = np.asarray(pts, dtype=np.float64)
    if len(P) < 10:
        return None
    ext = P.max(axis=0) - P.min(axis=0)
    if min(ext) < o["min_size"] and full:
        return None
    best: Optional[PrimFit] = None

    def consider(pf: Optional[PrimFit]):
        nonlocal best
        if pf is not None and (best is None or pf.max_err < best.max_err * 0.7):
            best = pf

    # ---- 圓
    cx, cy, r = fit_circle_points(P)
    res = np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r
    if r >= 2.0 and float(np.max(np.abs(res))) <= tol and _arc_support(P, cx, cy) >= (0.999 if full else 0.5):
        best = PrimFit(TracedPrimitive("circle", cx, cy, 2 * r, 2 * r), ellipse_path(cx, cy, r, r), float(np.max(np.abs(res))), float(np.sqrt(np.mean(res ** 2))))
        return best
    # ---- 矩形、圓角矩形（有號距離的最小平方）
    consider(_fit_rrect(P, tol, o, full))
    if best is not None and best.prim.kind == "rect":
        return best
    # ---- 橢圓
    if len(P) >= 12:
        try:
            (ex, ey), (d1, d2), ang = cv2.fitEllipse(P.astype(np.float32))
        except cv2.error:
            d1 = d2 = 0.0
        if d1 > 4.0 and d2 > 4.0 and max(d1, d2) < 20 * max(ext):
            a, b, th = d1 / 2.0, d2 / 2.0, math.radians(ang)
            dist = _ellipse_dist(P, ex, ey, a, b, th)
            if float(np.max(np.abs(dist))) <= tol and _arc_support(P, ex, ey) >= (0.999 if full else 0.6):
                if a < b:
                    a, b, th = b, a, th + math.pi / 2
                deg = math.degrees(th) % 180.0
                if deg > 90.0:
                    deg -= 180.0
                notes = []
                if abs(a - b) <= o["snap_tol"]:
                    rr = (a + b) / 2.0
                    pf = PrimFit(TracedPrimitive("circle", ex, ey, 2 * rr, 2 * rr), ellipse_path(ex, ey, rr, rr), float(np.max(np.abs(dist))), float(np.sqrt(np.mean(dist ** 2))))
                else:
                    # 長軸方向接近水平或垂直，而且扶正造成的偏差在量測誤差內才扶正
                    for snap in (0.0, 90.0, -90.0):
                        if abs(deg - snap) < 45 and abs(math.sin(math.radians(deg - snap))) * (a - b) <= o["snap_tol"]:
                            deg = snap
                            notes.append("橢圓的方向已扶正")
                            break
                    pf = PrimFit(TracedPrimitive("ellipse", ex, ey, 2 * a, 2 * b, deg), ellipse_path(ex, ey, a, b, deg), float(np.max(np.abs(dist))), float(np.sqrt(np.mean(dist ** 2))), notes)
                consider(pf)
    return best


def _arc_support(P: np.ndarray, cx: float, cy: float) -> float:
    """輪廓點繞中心涵蓋的角度比例（0 到 1）。"""
    ang = np.arctan2(P[:, 1] - cy, P[:, 0] - cx)
    bins = np.zeros(72, dtype=bool)
    bins[np.clip(((ang + math.pi) / (2 * math.pi) * 72).astype(int), 0, 71)] = True
    return float(bins.mean())


def _fit_rrect(P: np.ndarray, tol: float, o: dict, full: bool) -> Optional[PrimFit]:
    from scipy.optimize import least_squares

    (mx, my), (mw, mh), mang = cv2.minAreaRect(P.astype(np.float32))
    if mw < 3.0 or mh < 3.0:
        return None
    th0 = math.radians(mang)
    hx0, hy0 = mw / 2.0, mh / 2.0
    sub = P if len(P) <= 400 else P[np.linspace(0, len(P) - 1, 400).astype(int)]
    lo = [mx - mw, my - mh, th0 - 0.6, 1.0, 1.0, 0.0]
    hi = [mx + mw, my + mh, th0 + 0.6, hx0 * 3 + 2, hy0 * 3 + 2, max(hx0, hy0) * 3 + 2]
    best = None
    for r0 in (0.0 + 1e-3, 0.3 * min(hx0, hy0), 0.9 * min(hx0, hy0)):
        try:
            sol = least_squares(lambda p: _rrect_sdf(p, sub), [mx, my, th0, hx0, hy0, r0], bounds=(lo, hi), method="trf", max_nfev=60)
        except Exception:
            continue
        d = _rrect_sdf(sol.x, P)
        m = float(np.max(np.abs(d)))
        if best is None or m < best[1]:
            best = (sol.x, m, d)
        if m <= tol * 0.5:
            break
    if best is None:
        return None
    p, m, d = best
    cx, cy, th, hx, hy, r = p
    r = min(max(r, 0.0), hx, hy)
    # 尖角被反鋸齒磨圓：圓角半徑小到與邊緣模糊同級時，試半徑為零的矩形（只看離角點夠遠的點）
    notes: list[str] = []
    kind = "rounded_rect"
    blur = float(o["corner_blur"])
    if r <= 1.0 + 1.2 * blur:
        p0 = np.array([cx, cy, th, hx, hy, 0.0])
        ca, sa = math.cos(th), math.sin(th)
        dx, dy = P[:, 0] - cx, P[:, 1] - cy
        lx, ly = np.abs(dx * ca + dy * sa), np.abs(-dx * sa + dy * ca)
        away = (hx - lx > 1.5 + blur) | (hy - ly > 1.5 + blur)
        away &= ~((hx - lx < 1.5 + blur) & (hy - ly < 1.5 + blur))
        if away.sum() >= 8:
            sub2 = P[away]
            try:
                sol = least_squares(lambda q: _rrect_sdf(np.array([q[0], q[1], q[2], q[3], q[4], 0.0]), sub2), [cx, cy, th, hx, hy], method="lm", max_nfev=40)
                d0 = _rrect_sdf(np.array([*sol.x, 0.0]), sub2)
                if float(np.max(np.abs(d0))) <= tol:
                    cx, cy, th, hx, hy = sol.x
                    r, kind = 0.0, "rect"
                    d = d0
                    m = float(np.max(np.abs(d0)))
            except Exception:
                pass
    if m > tol:
        return None
    if not full and not _rect_sides_supported(P, cx, cy, th, hx, hy, r):
        return None
    hx, hy = abs(hx), abs(hy)
    deg = math.degrees(th)
    # 角度正規化到 (-45, 45]，必要時交換寬高
    while deg > 45.0:
        deg -= 90.0; hx, hy = hy, hx
    while deg <= -45.0:
        deg += 90.0; hx, hy = hy, hx
    if abs(math.sin(math.radians(deg))) * max(hx, hy) <= o["snap_tol"]:
        if abs(deg) > 0.05:
            notes.append("方向已扶正")
        deg = 0.0
    if kind == "rounded_rect" and abs(hx - hy) <= o["snap_tol"] and r >= min(hx, hy) - o["snap_tol"]:
        rr = (hx + hy) / 2.0
        return PrimFit(TracedPrimitive("circle", cx, cy, 2 * rr, 2 * rr), ellipse_path(cx, cy, rr, rr), m, float(np.sqrt(np.mean(d ** 2))))
    prim = TracedPrimitive(kind, float(cx), float(cy), float(2 * hx), float(2 * hy), float(deg), float(r))
    return PrimFit(prim, rounded_rect_path(cx, cy, 2 * hx, 2 * hy, r, deg), m, float(np.sqrt(np.mean(d ** 2))), notes)


def _rect_sides_supported(P: np.ndarray, cx, cy, th, hx, hy, r) -> bool:
    """補全用：矩形的四條邊都要有看得到的點支持（每邊至少有一段），否則被遮住的那一邊位置是猜的。"""
    ca, sa = math.cos(th), math.sin(th)
    dx, dy = P[:, 0] - cx, P[:, 1] - cy
    lx, ly = dx * ca + dy * sa, -dx * sa + dy * ca
    need = 0.25
    for coord, other, half, ohalf in ((lx, ly, hx, hy), (ly, lx, hy, hx)):
        for sgn in (1.0, -1.0):
            on = (np.abs(coord * sgn - half) < 1.0) & (np.abs(other) < ohalf - r - 0.5)
            if on.sum() < 3:
                return False
            span = float(other[on].max() - other[on].min()) if on.any() else 0.0
            if span < need * 2 * max(ohalf - r, 1e-6) and span < 12.0:
                return False
    return True


def polygon_primitive(poly: np.ndarray, snap_tol: float = 0.3) -> Optional[TracedPrimitive]:
    """全部由直線組成的輪廓：頂點不多時記成多邊形。"""
    n = len(poly)
    if n < 3 or n > 16:
        return None
    c = poly.mean(axis=0)
    ext = poly.max(axis=0) - poly.min(axis=0)
    return TracedPrimitive("polygon", float(c[0]), float(c[1]), float(ext[0]), float(ext[1]), 0.0, 0.0, [[float(x), float(y)] for x, y in poly])


def path_points(path: list[list[float]], step: float = 1.0) -> np.ndarray:
    """路徑上的取樣點（評測與驗證用）。"""
    from .vector_raster import flatten_path

    out = []
    for pts, closed in flatten_path(path, 0.02):
        q = np.vstack([pts, pts[:1]]) if closed else pts
        seg = np.hypot(*(q[1:] - q[:-1]).T)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        L = float(cum[-1])
        if L <= 0:
            continue
        s = np.linspace(0.0, L, max(2, int(L / step) + 1))
        out.append(np.stack([np.interp(s, cum, q[:, 0]), np.interp(s, cum, q[:, 1])], axis=1))
    return np.vstack(out) if out else np.zeros((0, 2))
