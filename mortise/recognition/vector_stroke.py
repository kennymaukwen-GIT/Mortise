"""描圖向量化的筆畫還原：等寬的細長區域（線條圖、圖表的軸線與格線、圖示的外框線）還原成「中心線加線寬」。

做法：
  1. 把區域細化成一像素寬的骨架（Zhang–Suen 細化，自行以 NumPy 實作），建成圖：端點、交叉點、兩者之間的線段。
  2. 沿每段骨架，在垂直方向對覆蓋率場取樣：覆蓋率的總和就是線寬、重心就是中心線的位置（次像素，不受骨架落在哪個像素影響）。
  3. 寬度一致才算筆畫；在交叉點把方向相同的兩段接成一條（格線才會是完整的一條線）。
  4. 端點沿切線方向把覆蓋率加總，量出線實際的終點；中心線再擬合成直線、折線或曲線。
  5. 把筆畫畫回來與原區域比，重疊率不夠就放棄（回傳 None，由呼叫端改用填色輪廓）。
寬度小於 2 像素的線，顏色與寬度無法完全分離（每個像素都是混色），這是量測上的限制；會在 notes 註明。
"""

from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np

from . import vector_fit as vf
from . import vector_raster as vr
from .types import TracedPath, TracedPrimitive

_OFFS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]  # 由北開始順時針


def _thin(mask: np.ndarray) -> np.ndarray:
    """Zhang–Suen 細化，再去掉階梯轉角上多餘的像素，得到八連通、一像素寬的骨架。"""
    img = np.pad(mask.astype(np.uint8), 1)
    while True:
        changed = False
        for step in (0, 1):
            nb = [img[1 + dy : img.shape[0] - 1 + dy, 1 + dx : img.shape[1] - 1 + dx] for dy, dx in _OFFS]
            p2, p3, p4, p5, p6, p7, p8, p9 = nb
            B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            cen = img[1:-1, 1:-1]
            cond = (cen == 1) & (B >= 2) & (B <= 6) & (A == 1)
            if step == 0:
                cond &= (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond &= (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            if cond.any():
                cen[cond] = 0
                changed = True
        if not changed:
            break
    # 階梯轉角：某像素的兩個正交鄰居都在骨架上、對面三格都空，拿掉它不影響連通
    for a, b, opp in (((-1, 0), (0, 1), [(0, -1), (1, -1), (1, 0)]), ((0, 1), (1, 0), [(-1, 0), (-1, -1), (0, -1)]),
                      ((1, 0), (0, -1), [(-1, 0), (-1, 1), (0, 1)]), ((0, -1), (-1, 0), [(0, 1), (1, 1), (1, 0)])):
        def sh(d):
            return img[1 + d[0] : img.shape[0] - 1 + d[0], 1 + d[1] : img.shape[1] - 1 + d[1]]

        cen = img[1:-1, 1:-1]
        cond = (cen == 1) & (sh(a) == 1) & (sh(b) == 1)
        for o in opp:
            cond &= sh(o) == 0
        cen[cond] = 0
    return img[1:-1, 1:-1].astype(bool)


def _graph(sk: np.ndarray):
    """骨架 → 圖。回傳（節點座標清單、線段清單）。線段是（起節點、迄節點、像素鏈 [(y, x), ...]）；封閉環的兩個節點都是 -1。"""
    H, W = sk.shape
    ys, xs = np.nonzero(sk)
    n = len(ys)
    if n == 0:
        return [], []
    idm = np.full((H + 2, W + 2), -1, dtype=np.int64)
    idm[ys + 1, xs + 1] = np.arange(n)
    nb = np.stack([idm[ys + 1 + dy, xs + 1 + dx] for dy, dx in _OFFS], axis=1)
    deg = (nb >= 0).sum(axis=1)
    nbl = [[int(v) for v in row if v >= 0] for row in nb]
    is_j = deg >= 3
    # 相鄰的交叉像素合成一個節點
    node_of = np.full(n, -1, dtype=np.int64)
    nodes: list[tuple[float, float]] = []
    for i in np.nonzero(is_j)[0]:
        if node_of[i] >= 0:
            continue
        stack, members = [int(i)], []
        node_of[i] = len(nodes)
        while stack:
            p = stack.pop()
            members.append(p)
            for q in nbl[p]:
                if is_j[q] and node_of[q] < 0:
                    node_of[q] = len(nodes)
                    stack.append(q)
        nodes.append((float(np.mean(ys[members])) + 0.5, float(np.mean(xs[members])) + 0.5))
    for i in np.nonzero(deg <= 1)[0]:
        node_of[i] = len(nodes)
        nodes.append((float(ys[i]) + 0.5, float(xs[i]) + 0.5))
    edges = []
    visited = np.zeros(n, dtype=bool)

    def walk(prev: int, cur: int, start_node: int):
        chain = []
        while True:
            if node_of[cur] >= 0:
                return start_node, int(node_of[cur]), chain
            if visited[cur]:
                return None
            visited[cur] = True
            chain.append((int(ys[cur]), int(xs[cur])))
            nxt = [q for q in nbl[cur] if q != prev]
            # 優先走到節點，其次走沒走過的像素
            to_node = [q for q in nxt if node_of[q] >= 0 and not (node_of[q] == start_node and len(chain) <= 1)]
            fresh = [q for q in nxt if node_of[q] < 0 and not visited[q]]
            if fresh:
                prev, cur = cur, fresh[0]
            elif to_node:
                prev, cur = cur, to_node[0]
            else:
                return start_node, -2, chain  # 走到死路（不該發生）

    for i in range(n):
        if node_of[i] < 0:
            continue
        for q in nbl[i]:
            if node_of[q] >= 0:
                if node_of[q] != node_of[i] and node_of[q] > node_of[i] and (deg[i] <= 1 or deg[q] <= 1 or True):
                    if not is_j[i] or not is_j[q]:
                        edges.append((int(node_of[i]), int(node_of[q]), []))
                continue
            if visited[q]:
                continue
            e = walk(i, q, int(node_of[i]))
            if e is not None:
                edges.append(e)
    for i in range(n):  # 沒有節點的封閉環
        if node_of[i] < 0 and not visited[i]:
            chain = []
            prev, cur = -1, i
            while not visited[cur]:
                visited[cur] = True
                chain.append((int(ys[cur]), int(xs[cur])))
                nxt = [q for q in nbl[cur] if q != prev and not visited[q]]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
            edges.append((-1, -1, chain))
    return nodes, edges


def _smooth(pts: np.ndarray, w: int = 5, closed: bool = False) -> np.ndarray:
    n = len(pts)
    if n < 3:
        return pts.copy()
    h = w // 2
    if closed:
        ext = np.vstack([pts[-h:], pts, pts[:h]])
        k = np.ones(w) / w
        return np.stack([np.convolve(ext[:, c], k, mode="valid") for c in range(2)], axis=1)
    out = pts.copy()
    for i in range(n):
        a, b = max(0, i - h), min(n, i + h + 1)
        r = min(i - a, b - 1 - i)
        out[i] = pts[i - r : i + r + 1].mean(axis=0)
    return out


def _tangents(pts: np.ndarray, closed: bool = False) -> np.ndarray:
    n = len(pts)
    if n < 2:
        return np.tile(np.array([[1.0, 0.0]]), (n, 1))
    if closed:
        d = np.roll(pts, -2, axis=0) - np.roll(pts, 2, axis=0)
    else:
        d = np.empty_like(pts)
        k = min(2, n - 1)
        for i in range(n):
            a, b = max(0, i - k), min(n - 1, i + k)
            d[i] = pts[b] - pts[a]
    L = np.hypot(d[:, 0], d[:, 1])
    L[L < 1e-9] = 1.0
    return d / L[:, None]


def _cross_sections(c: np.ndarray, pts: np.ndarray, tan: np.ndarray, R: float, step: float = 0.25):
    """沿每個點的法線對覆蓋率場取樣。回傳（線寬、重心偏移）；只取包含中心的那一瓣（不把隔壁的平行線算進來）。"""
    offs = np.arange(-R, R + 1e-9, step)
    nrm = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
    X = pts[:, 0:1] + nrm[:, 0:1] * offs[None, :]
    Y = pts[:, 1:2] + nrm[:, 1:2] * offs[None, :]
    prof = cv2.remap(c, (X - 0.5).astype(np.float32), (Y - 0.5).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    mid = len(offs) // 2
    keep = np.zeros_like(prof, dtype=bool)
    keep[:, mid] = True
    for direction in (1, -1):
        alive = np.ones(len(pts), dtype=bool)
        prev = prof[:, mid].copy()
        i = mid + direction
        while 0 <= i < len(offs):
            cur = prof[:, i]
            alive &= (cur > 0.02) & ~((cur > prev + 0.04) & (prev < 0.6))
            keep[:, i] = alive
            prev = np.where(alive, cur, prev)
            i += direction
    lobe = np.where(keep, prof, 0.0)
    mass = lobe.sum(axis=1) * step
    cen = (lobe * offs[None, :]).sum(axis=1) * step / np.maximum(mass, 1e-9)
    return mass, cen, nrm


def fit_strokes(c: np.ndarray, offset: tuple[int, int], color, tol: float, fit_opts: dict, prim_opts: dict, P: dict) -> Optional[list[TracedPath]]:
    """把一個區域的覆蓋率場（裁切範圍內）還原成筆畫。不是等寬的細長形狀就回傳 None。"""
    m = c >= 0.5
    area = int(m.sum())
    if area < 3:
        return None
    wmax = float(P["stroke_max_width_px"])
    dt = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    if 2.0 * float(dt.max()) - 1.0 > wmax + 1.0:
        return None
    sk = _thin(m)
    if not sk.any():
        return None
    w0 = max(1.0, 2.0 * float(np.median(dt[sk])) - 1.0)
    if float(c.sum()) < float(P["stroke_min_aspect"]) * w0 * w0:
        return None
    # 修掉端點與轉角的細化毛邊（短分支）
    for _ in range(2):
        nodes, edges = _graph(sk)
        deg = {}
        for a, b, ch in edges:
            for v in (a, b):
                if v >= 0:
                    deg[v] = deg.get(v, 0) + 1
        prune = max(2.0, 0.9 * w0)
        removed = False
        for a, b, ch in edges:
            if a < 0 or b < 0:
                continue
            da, db = deg.get(a, 0), deg.get(b, 0)
            if len(ch) <= prune and ((da == 1 and db >= 3) or (db == 1 and da >= 3)):
                for y, x in ch:
                    sk[y, x] = False
                end = a if da == 1 else b
                ey, ex = int(nodes[end][0]), int(nodes[end][1])
                sk[ey, ex] = False
                removed = True
        if not removed:
            break
        sk = _thin(sk)
    nodes, edges = _graph(sk)
    if not edges:
        return None
    if len(edges) > 400:
        return None
    R = wmax / 2.0 + 2.5
    spread_rel = float(P["stroke_width_spread"])
    # ---- 每段：次像素中心線與線寬
    segs = []  # dict(a, b, pts, width, closed)
    for a, b, ch in edges:
        pix = np.array([[x + 0.5, y + 0.5] for y, x in ch], dtype=np.float64) if ch else np.zeros((0, 2))
        closed = a < 0 and b < 0
        full = pix
        if not closed:
            pa = np.array([[nodes[a][1], nodes[a][0]]]) if a >= 0 else np.zeros((0, 2))
            pb = np.array([[nodes[b][1], nodes[b][0]]]) if b >= 0 else np.zeros((0, 2))
            full = np.vstack([pa, pix, pb])
        if len(full) < 2:
            continue
        sm = _smooth(full, 5, closed)
        tan = _tangents(sm, closed)
        mass, cen, nrm = _cross_sections(c, sm, tan, R)
        refined = sm + nrm * cen[:, None]
        segs.append({"a": a, "b": b, "pts": refined, "w": mass, "closed": closed, "tan": tan})
    if not segs:
        return None
    deg = {}
    for s in segs:
        for v in (s["a"], s["b"]):
            if v >= 0:
                deg[v] = deg.get(v, 0) + 1
    # ---- 寬度一致性（離端點與交叉點一個線寬以外的取樣）
    for s in segs:
        n = len(s["pts"])
        mg = int(math.ceil(w0)) + 1
        lo = mg if (s["closed"] is False) else 0
        hi = n - mg if (s["closed"] is False) else n
        ww = s["w"][lo:hi] if hi - lo >= 3 else s["w"]
        s["width"] = float(np.median(ww))
        s["core"] = (lo, hi) if hi - lo >= 3 else (0, n)
        mad = 1.4826 * float(np.median(np.abs(ww - s["width"])))
        if s["width"] > wmax or s["width"] < 0.3:
            return None
        if hi - lo >= 3 and mad > max(spread_rel * s["width"], 0.25):
            return None
    # ---- 在交叉點把方向相同、寬度相近的兩段接成一條
    def end_dir(s, at_start: bool) -> np.ndarray:
        p = s["pts"]
        lo, hi = s["core"]
        if at_start:
            i0 = min(lo, len(p) - 2)
            i1 = min(len(p) - 1, i0 + 6)
            return vf._unit(p[i1] - p[i0])  # 由節點往外
        i0 = max(hi - 1, 1)
        i1 = max(0, i0 - 6)
        return vf._unit(p[i1] - p[i0])

    used = [False] * len(segs)
    link: dict[tuple[int, bool], tuple[int, bool]] = {}  # （段、是否起點端）→ 接上的（段、端）
    at_node: dict[int, list[tuple[int, bool]]] = {}
    for i, s in enumerate(segs):
        if s["closed"]:
            continue
        at_node.setdefault(s["a"], []).append((i, True))
        at_node.setdefault(s["b"], []).append((i, False))
    for v, ends in at_node.items():
        if len(ends) < 2:
            continue
        cands = []
        for x in range(len(ends)):
            for y in range(x + 1, len(ends)):
                (i, si), (j, sj) = ends[x], ends[y]
                if i == j:
                    continue
                di, dj = end_dir(segs[i], si), end_dir(segs[j], sj)
                cosang = float(di @ dj)
                wi, wj = segs[i]["width"], segs[j]["width"]
                if abs(wi - wj) > max(0.25 * max(wi, wj), 0.5):
                    continue
                if len(ends) == 2 or cosang < -math.cos(math.radians(25)):
                    cands.append((cosang, x, y))
        cands.sort()
        taken = set()
        for _cs, x, y in cands:
            if x in taken or y in taken:
                continue
            taken.add(x); taken.add(y)
            link[ends[x]] = ends[y]
            link[ends[y]] = ends[x]
    # ---- 串成一條條筆畫
    strokes = []  # dict(pts, width, closed, free_start, free_end)
    for i, s in enumerate(segs):
        if used[i]:
            continue
        if s["closed"]:
            used[i] = True
            strokes.append({"pts": s["pts"], "width": s["width"], "closed": True, "ends": (None, None), "weights": [len(s["pts"])], "widths": [s["width"]]})
            continue
        # 往回找到這條筆畫的起頭
        cur, at_start = i, True
        seen = {i}
        while (cur, at_start) in link:
            nxt, nend = link[(cur, at_start)]
            if nxt in seen:
                break
            seen.add(nxt)
            cur, at_start = nxt, not nend
        chain = []
        closed_loop = False
        seg_i, start_end = cur, at_start
        first = (seg_i, start_end)
        widths, weights = [], []
        while True:
            used[seg_i] = True
            sg = segs[seg_i]
            lo, hi = sg["core"]
            p = sg["pts"][lo:hi]
            if len(p) == 0:
                p = sg["pts"]
            chain.append(p if start_end else p[::-1])
            widths.append(sg["width"]); weights.append(len(p))
            far = (seg_i, not start_end)
            if far in link:
                nxt, nend = link[far]
                if (nxt, nend) == first:
                    closed_loop = True
                    break
                if used[nxt]:
                    break
                seg_i, start_end = nxt, nend
                continue
            break
        pts = np.vstack(chain)
        sa = segs[first[0]]
        node_start = sa["a"] if first[1] else sa["b"]
        sb = segs[seg_i]
        node_end = sb["b"] if start_end else sb["a"]
        strokes.append({"pts": pts, "width": float(np.average(widths, weights=weights)), "closed": closed_loop, "ends": (node_start, node_end), "weights": weights, "widths": widths})
    # ---- 端點：自由端沿切線加總覆蓋率量出真正的終點；接在交叉點上的端點延伸到交叉點
    out: list[TracedPath] = []
    ox, oy = float(offset[0]), float(offset[1])
    total_cov = np.zeros_like(c)
    Hc, Wc = c.shape
    for st in strokes:
        pts = st["pts"]
        w = st["width"]
        if len(pts) < 2:
            return None
        cap = "butt"
        if not st["closed"]:
            new_ends = []
            for which, node in ((0, st["ends"][0]), (1, st["ends"][1])):
                k = min(len(pts) - 1, 4)
                if which == 0:
                    p_end, t_dir = pts[0], vf._unit(pts[0] - pts[k])
                else:
                    p_end, t_dir = pts[-1], vf._unit(pts[-1] - pts[-1 - k])
                if node is not None and node >= 0 and deg.get(node, 0) >= 2:
                    q = np.array([nodes[node][1], nodes[node][0]])
                    ext = float((q - p_end) @ t_dir)
                    new_ends.append(p_end + t_dir * max(ext, 0.0))
                else:
                    ss = np.arange(0.125, 2.0 * w + 6.0, 0.25)
                    X = (p_end[0] + t_dir[0] * ss - 0.5).astype(np.float32)[None, :]
                    Y = (p_end[1] + t_dir[1] * ss - 0.5).astype(np.float32)[None, :]
                    prof = cv2.remap(c, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)[0]
                    stop = np.nonzero(prof < 0.02)[0]
                    upto = int(stop[0]) if len(stop) else len(prof)
                    ext = float(prof[:upto].sum() * 0.25)
                    tip = p_end + t_dir * ext
                    if w >= 3.5 and ext > 0.6 * w:
                        # 圓頭：靠近尖端的地方線寬會縮；平頭則一路等寬
                        probe = tip - t_dir * (0.3 * w)
                        mass, _, _ = _cross_sections(c, probe[None, :], t_dir[None, :], w / 2.0 + 2.0)
                        if float(mass[0]) < 0.93 * w:
                            cap = "round"
                    new_ends.append(tip)
            if cap == "round":
                for i_end in (0, 1):
                    node = st["ends"][i_end]
                    if not (node is not None and node >= 0 and deg.get(node, 0) >= 2):
                        k = min(len(pts) - 1, 4)
                        t_dir = vf._unit(pts[0] - pts[k]) if i_end == 0 else vf._unit(pts[-1] - pts[-1 - k])
                        new_ends[i_end] = new_ends[i_end] - t_dir * (w / 2.0)
            pts = np.vstack([new_ends[0][None, :], pts, new_ends[1][None, :]])
        prim = None
        notes: list[str] = []
        if st["closed"]:
            pf = vf.fit_primitive(pts, max(tol, 0.3), prim_opts, full=True) if len(pts) >= 12 else None
            if pf is not None:
                path, prim = pf.path, pf.prim
            else:
                path, _info = vf.fit_contour(pts, tol, fit_opts)
        else:
            o2 = dict(fit_opts)
            o2["corner_trim"] = max(1.5, 0.75 * w)  # 中心線在轉角附近（約一個線寬內）量不準
            o2["rdp_eps"] = max(0.6, 0.3 * w)
            path, info = vf.fit_open(pts, max(tol, 0.2), o2)
            if info.lines == 1 and info.beziers == 0 and info.arcs == 0 and len(path) == 2:
                a, b = np.array(path[0][1:3]), np.array(path[1][1:3])
                d = b - a
                prim = TracedPrimitive("line", float((a[0] + b[0]) / 2 + ox), float((a[1] + b[1]) / 2 + oy), float(np.hypot(*d)), 0.0, float(math.degrees(math.atan2(d[1], d[0]))))
        if not path:
            return None
        if w < 2.0:
            notes.append("線寬小於 2 像素，顏色與寬度無法完全分離，數值的信心較低")
        join = "round" if cap == "round" else "miter"
        total_cov = np.maximum(total_cov, vr.stroke_coverage(path, w, Wc, Hc, 1.0, cap, join))
        gpath = [[seg[0]] + [float(v) + (ox if i % 2 == 0 else oy) for i, v in enumerate(seg[1:])] for seg in path]
        if prim is not None and prim.kind != "line":
            prim = TracedPrimitive(prim.kind, prim.cx + ox, prim.cy + oy, prim.w, prim.h, prim.angle_deg, prim.corner_radius, [[x + ox, y + oy] for x, y in prim.points])
        err_pts = pts + np.array([ox, oy])
        from .vectorize import _fit_err

        out.append(TracedPath(path=gpath, fill=None, stroke=tuple(color), stroke_width_px=float(w), stroke_cap=cap, stroke_join=join, primitive=prim,
                              fit_error_px=_fit_err(err_pts, gpath), notes=notes))
    # ---- 畫回來比對
    # 用覆蓋率本身算重疊率（不取門檻：半像素寬的邊緣剛好落在 0.5 上，取門檻會忽大忽小）
    inter = float(np.minimum(total_cov, c).sum())
    union = float(np.maximum(total_cov, c).sum())
    if union <= 0 or inter / union < float(P["stroke_min_iou"]):
        return None
    return out
