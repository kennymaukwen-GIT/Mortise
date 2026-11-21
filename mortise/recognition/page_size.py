"""尺寸判斷（②頁面用）：由長寬比與尺度線索，在規格表中排出頁面尺寸的候選清單。

做法（貝氏比較，信心值就是後驗機率）
    1. 規格表整理：sizes 是「名稱 → (寬 pt, 高 pt)」。規格表沒有的另一方向（例如只有「A5 直」）自動補上旋轉版
       （add_rotated）。長寬比（高 ÷ 寬）相對差在 family_tol 之內的尺寸歸為同一個「家族」
       （例如 A3 直、A4 直、A5 直都是 √2 直式）。
    2. 長寬比似然：以對數比的常態分布 exp(−½ (ln(r量/r規) / σ)²)，σ 是長寬比的相對誤差
       （hints["aspect_error"] 為絕對誤差，例如 PageQuad.aspect.error；沒有就用預設值，下限 aspect_sigma_floor）。
    3. 尺度似然（只在家族內分大小；有哪些線索就用哪些，都沒有時同家族各尺寸的似然相同）：
       - dpi：hints["page_px"]（頁面的像素寬高）與 hints["dpi"]（可信的檔案標記，例如掃描器寫入）
         推得頁寬 pt，與候選的寬比較（σ = dpi_sigma）。
       - 字級：hints["text_px"]（內文字級的 em 像素大小，可給一串量測值，取中位數）與 page_px，
         推得每個候選尺寸下的內文 pt，再用內文字級的先驗（文件約 10.5 pt、簡報約 20 pt，對數常態）評分。
         這是弱線索：A4 與 A5 只差 √2 倍，先驗分布的寬度與之相當，所以只會「偏向」不會「確定」。
    4. 先驗：每個家族相同，家族內平分（size_priors 可另給權重）；另有「非標準尺寸」假設，
       其似然是常數 exp(−½ k²)（相當於「差 k 個 σ」），先驗 p_nonstandard_prior。
    5. 後驗 = 先驗 × 長寬比似然 × 尺度似然，正規化（含非標準假設）。所以候選信心值的總和 = 1 − P(非標準)。
    judge() 依後驗把結果分成三種：
       "clear"（遙遙領先）、"ambiguous"（前兩名接近，應請使用者選）、"nonstandard"（都不符）。

    單張照片的絕對尺度不可觀測：沒有 dpi、字級線索時，同家族各尺寸的信心值相同，judge() 會回報 ambiguous
    （家族本身可能是 clear，另以 family_verdict 回報）。

已知限制
    - 字級先驗是通用值：海報、名片、大字報的內文字級遠大於文件，字級線索會把它推向小尺寸。
      已知文件類型時，上游可用 params 換先驗。
    - dpi 標記只在掃描檔可信；截圖與照片的 dpi 標記多半是 72／96／144 等無意義的值，上游不要傳。
    - 規格家族的命名只認得 ISO A、Letter、4:3、16:9、16:10 等常見比例，其他以長寬比數字命名。

可單獨升級的點
    - 字級先驗可改用真實樣本統計（依文件類型分開）。
    - 加入其他尺度線索（例如照片 EXIF 的對焦距離、畫面中已知尺寸的物體），在 _scale_likelihood() 加一項即可。
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .types import SizeCandidate

DEFAULT_PARAMS: dict = {
    # ---- 長寬比 ----
    "aspect_sigma": 0.012,  # 沒有給長寬比誤差時的預設相對誤差（1σ，無單位；0.012 = 1.2%）
    "aspect_sigma_floor": 0.003,  # 長寬比相對誤差的下限（截圖像素取整、規格本身的捨入）
    "family_tol": 0.004,  # 長寬比相對差在這之內的尺寸歸同一家族
    "add_rotated": True,  # 規格表沒有的另一方向是否自動補上
    # ---- 非標準尺寸假設 ----
    "p_nonstandard_prior": 0.1,  # 非標準尺寸的先驗機率
    "nonstandard_k": 2.5,  # 非標準假設的似然 = exp(−½ k²)：長寬比差超過約 k 個 σ 時，非標準勝出
    # ---- 尺度線索 ----
    "dpi_sigma": 0.03,  # 由 dpi 標記推得頁寬的相對誤差（1σ）
    "text_doc_pt": 10.5,  # 文件內文字級的典型值（pt）
    "text_doc_log_sigma": 0.25,  # 文件內文字級的對數標準差（無單位）
    "text_slide_pt": 20.0,  # 簡報內文字級的典型值（pt）
    "text_slide_log_sigma": 0.4,  # 簡報內文字級的對數標準差
    "slide_keywords": ["簡報", "slide", "Slide", "螢幕", "screen"],  # 名稱含這些字的尺寸視為簡報類
    "slide_ratios": [16 / 9, 4 / 3, 16 / 10],  # 長寬比（長邊 ÷ 短邊）符合這些的尺寸也視為簡報類
    "size_priors": {},  # 各尺寸的先驗權重（名稱 → 倍數），預設全為 1
    # ---- 結果判定（judge）----
    "clear_min_conf": 0.6,  # 第一名信心值至少這個才算遙遙領先
    "clear_ratio": 3.0,  # 且至少是第二名的這個倍數
    "family_clear_min_conf": 0.6,  # 家族層級的遙遙領先門檻
}

_FAMILY_NAMES = [(math.sqrt(2.0), "ISO A"), (792.0 / 612.0, "Letter"), (16 / 9, "16:9"), (4 / 3, "4:3"), (16 / 10, "16:10"), (14 / 8.5, "Legal")]


def _p(params: dict | None) -> dict:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


def _rotated_name(name: str, w: float, h: float) -> str:
    if "直" in name:
        return name.replace("直", "橫")
    if "橫" in name:
        return name.replace("橫", "直")
    return f"{name} {'直' if h > w else '橫'}"  # w、h 是旋轉後的寬高


def _family_label(ratio_hw: float, tol: float) -> str:
    long_short = max(ratio_hw, 1 / ratio_hw)
    orient = "直" if ratio_hw > 1 + 1e-6 else ("橫" if ratio_hw < 1 - 1e-6 else "方")
    for r, nm in _FAMILY_NAMES:
        if abs(math.log(long_short / r)) <= max(tol, 0.004):
            return f"{nm} {orient}"
    return f"長寬比 {ratio_hw:.3f}"


def _is_slide(name: str, w: float, h: float, p: dict) -> bool:
    if any(k in name for k in p["slide_keywords"]):
        return True
    ls = max(w, h) / min(w, h)
    return any(abs(math.log(ls / r)) < 0.004 for r in p["slide_ratios"]) and w > h


def _expand(sizes: dict[str, tuple[float, float]], p: dict) -> list[dict]:
    items = []
    for name, (w, h) in sizes.items():
        items.append({"name": name, "w": float(w), "h": float(h), "rotated": False})
    if p["add_rotated"]:
        extra = []
        for it in items:
            w, h = it["h"], it["w"]
            if abs(w - h) < 0.5:
                continue
            if any(abs(o["w"] - w) < 0.5 and abs(o["h"] - h) < 0.5 for o in items + extra):
                continue
            extra.append({"name": _rotated_name(it["name"], w, h), "w": w, "h": h, "rotated": True, "from": it["name"]})
        items += extra
    for it in items:
        it["slide"] = _is_slide(it.get("from", it["name"]), max(it["w"], it["h"]), min(it["w"], it["h"]), p) if it["rotated"] else _is_slide(it["name"], it["w"], it["h"], p)
    # 家族：依長寬比分群（貪婪，依長寬比排序後相鄰的歸一群）
    items.sort(key=lambda it: it["h"] / it["w"])
    fam_id, last = -1, None
    for it in items:
        r = it["h"] / it["w"]
        if last is None or abs(math.log(r / last)) > p["family_tol"]:
            fam_id += 1
            last = r
        it["family_id"] = fam_id
    for fid in range(fam_id + 1):
        mem = [it for it in items if it["family_id"] == fid]
        r = float(np.exp(np.mean([math.log(m["h"] / m["w"]) for m in mem])))
        lab = _family_label(r, p["family_tol"])
        for m in mem:
            m["family"] = lab
            m["family_size"] = len(mem)
    return items


def _scale_likelihood(it: dict, hints: dict, p: dict) -> tuple[float, list[str]]:
    """家族內分大小的尺度似然與說明。"""
    L = 1.0
    why = []
    page_px = hints.get("page_px")
    wpx = float(page_px[0]) if page_px is not None else None
    dpi = hints.get("dpi")
    if dpi and wpx:
        sig = float(hints.get("dpi_sigma", p["dpi_sigma"]))
        implied = wpx / float(dpi) * 72.0
        z = math.log(implied / it["w"]) / sig
        L *= math.exp(-0.5 * z * z)
        why.append(f"依 {float(dpi):g} dpi 推得頁寬 {implied:.0f} pt，與本尺寸 {it['w']:.0f} pt 相差 {100 * (implied / it['w'] - 1):+.1f}%")
    tp = hints.get("text_px")
    if tp is not None and wpx:
        arr = np.atleast_1d(np.asarray(tp, dtype=np.float64))
        arr = arr[np.isfinite(arr) & (arr > 0)]
        if arr.size:
            med = float(np.median(arr))
            body_pt = med * it["w"] / wpx
            if it["slide"]:
                mu, sg, kind = p["text_slide_pt"], p["text_slide_log_sigma"], "簡報"
            else:
                mu, sg, kind = p["text_doc_pt"], p["text_doc_log_sigma"], "文件"
            z = math.log(body_pt / mu) / sg
            L *= math.exp(-0.5 * z * z)
            why.append(f"內文字級中位數 {med:.1f} px 換算為 {body_pt:.1f} pt（{kind}典型 {mu:g} pt）")
    return L, why


def infer_size(
    aspect_ratio: float,
    sizes: dict[str, tuple[float, float]],
    hints: dict | None = None,
    params: dict | None = None,
) -> list[SizeCandidate]:
    """依長寬比（高 ÷ 寬）與尺度線索排出尺寸候選，信心值（後驗機率）由高到低。

    hints 可用的鍵：
        aspect_error：長寬比的絕對誤差（1σ，例如 PageQuad.aspect.error）
        page_px：頁面的像素（寬, 高）——攤平後的影像尺寸或整張圖
        dpi：可信的 dpi 標記（掃描檔）；dpi_sigma：其相對誤差
        text_px：內文字級的 em 像素大小（一個數或一串量測值）
    候選信心值的總和 = 1 − P(非標準尺寸)；用 judge() 取得三種結果的判定。
    """
    p = _p(params)
    hints = dict(hints or {})
    a = float(aspect_ratio)
    if not (a > 0 and math.isfinite(a)):
        return []
    sig = p["aspect_sigma"]
    if hints.get("aspect_error") is not None:
        sig = float(hints["aspect_error"]) / a
    sig = max(sig, p["aspect_sigma_floor"])
    items = _expand(sizes, p)
    n_fam = len({it["family_id"] for it in items})
    p_std = 1.0 - p["p_nonstandard_prior"]
    scale_any = bool((hints.get("dpi") or hints.get("text_px") is not None) and hints.get("page_px") is not None)
    rows = []
    for it in items:
        r = it["h"] / it["w"]
        d = math.log(a / r)
        z = d / sig
        La = math.exp(-0.5 * z * z)
        Ls, why = _scale_likelihood(it, hints, p)
        prior = p_std / n_fam / it["family_size"] * float(p["size_priors"].get(it["name"], 1.0))
        rows.append({"it": it, "La": La, "Ls": Ls, "prior": prior, "z": z, "d": d, "why": why})
    # 非標準假設：似然常數；尺度似然取 1（非標準尺寸對尺度線索沒有預測）
    L_none = math.exp(-0.5 * p["nonstandard_k"] ** 2)
    ev_none = p["p_nonstandard_prior"] * L_none
    ev = np.array([r["prior"] * r["La"] * r["Ls"] for r in rows])
    # 尺度似然只在家族內有意義：把每個家族的尺度似然正規化成「平均為 1」，避免它改變家族之間的比較
    fam_ids = np.array([r["it"]["family_id"] for r in rows])
    for fid in set(fam_ids.tolist()):
        m = fam_ids == fid
        ls = np.array([rows[i]["Ls"] for i in np.where(m)[0]])
        if ls.max() > 0:
            ev[m] = ev[m] / max(ls.mean(), 1e-300)
        else:
            ev[m] = np.array([rows[i]["prior"] * rows[i]["La"] for i in np.where(m)[0]])
    total = ev.sum() + ev_none
    post = ev / total if total > 0 else np.zeros_like(ev)
    out = []
    for r, c in zip(rows, post):
        it = r["it"]
        mates = [o["it"]["name"] for o in rows if o["it"]["family_id"] == it["family_id"] and o["it"]["name"] != it["name"]]
        parts = [f"長寬比 {a:.4f}（誤差 ±{100 * sig:.1f}%）與本尺寸 {it['h'] / it['w']:.4f} 相差 {100 * (math.exp(r['d']) - 1):+.2f}%（{abs(r['z']):.1f}σ）"]
        if mates:
            if r["why"]:
                parts += r["why"]
            else:
                parts.append(f"同家族還有 {'、'.join(mates)}，沒有絕對尺度線索（單張影像的實際大小不可觀測），無法區分")
        if it["rotated"]:
            parts.append(f"規格表沒有這個方向，由「{it['from']}」旋轉而得")
        out.append(
            SizeCandidate(
                name=it["name"],
                width_pt=it["w"],
                height_pt=it["h"],
                confidence=float(c),
                reason="；".join(parts),
                aspect_score=float(r["La"]),
                family=it["family"],
                scale_evidence=bool(scale_any and r["why"]),
            )
        )
    out.sort(key=lambda s: (-s.confidence, s.name))
    return out


def judge(candidates: list[SizeCandidate], params: dict | None = None) -> dict:
    """把 infer_size 的結果分成三種情況。

    回傳 {"verdict": "clear"｜"ambiguous"｜"nonstandard", "best": 名稱或 None, "p_nonstandard": 機率,
          "family": 第一名的家族, "family_confidence": 該家族的總信心值, "family_verdict": "clear"｜"ambiguous"｜"nonstandard",
          "scale_observable": 是否有尺度線索}
    """
    p = _p(params)
    if not candidates:
        return {"verdict": "nonstandard", "best": None, "p_nonstandard": 1.0, "family": None, "family_confidence": 0.0, "family_verdict": "nonstandard", "scale_observable": False}
    c = sorted(candidates, key=lambda s: -s.confidence)
    p_none = max(0.0, 1.0 - sum(s.confidence for s in c))
    fam: dict[str, float] = {}
    for s in c:
        fam[s.family] = fam.get(s.family, 0.0) + s.confidence
    fam_sorted = sorted(fam.items(), key=lambda kv: -kv[1])
    top_f, top_fc = fam_sorted[0]
    second_fc = fam_sorted[1][1] if len(fam_sorted) > 1 else 0.0
    best, second = c[0].confidence, (c[1].confidence if len(c) > 1 else 0.0)
    if p_none >= best and p_none >= top_fc:
        verdict = fam_verdict = "nonstandard"
    else:
        verdict = "clear" if (best >= p["clear_min_conf"] and best >= p["clear_ratio"] * max(second, p_none)) else "ambiguous"
        fam_verdict = "clear" if (top_fc >= p["family_clear_min_conf"] and top_fc >= p["clear_ratio"] * max(second_fc, p_none)) else "ambiguous"
    return {
        "verdict": verdict,
        "best": c[0].name if verdict != "nonstandard" else None,
        "p_nonstandard": float(p_none),
        "family": top_f,
        "family_confidence": float(top_fc),
        "family_verdict": fam_verdict,
        "scale_observable": any(s.scale_evidence for s in c),
    }
