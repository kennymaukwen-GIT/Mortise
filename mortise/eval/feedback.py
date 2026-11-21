"""回饋資料：把使用者（產品創辦人）的修正與評價存成可用的資料。

一、.feedback.json 的格式（放在樣本旁邊，與樣本同名；也可以用 FeedbackStore.add 存進回饋庫）：

    {
      "schema": "mortise.feedback/1",
      "sample": "slides/季報.pdf",          ← 樣本路徑（相對於樣本目錄）；放在樣本旁邊時可省略
      "author": "founder",                   ← （可選）
      "created": "2026-11-21T10:00:00",      ← （可選）
      "comments": ["標題字型不對", "..."],   ← 自由文字意見（也接受單一字串 "comment"）
      "page_ratings": [                      ← 「這一頁整體評分」（正向或負向都可以）
        {"page_index": 0, "score": 4, "max_score": 5, "comment": "排版很接近"}],
      "corrections": [                       ← 修正：沿用 mortise.artifacts.Correction 的欄位
        {"target_stage": 5, "page_index": 0, "element_id": "p001-…", "field": "font_family",
         "new_value": "Noto Sans TC", "instruction": "",
         "system_value": "Arial", "system_confidence": 0.62}],   ← （可選）系統當時的值與信心值快照
      "confirmations": [                     ← 「這個元素是對的」（正向回饋）
        {"target_stage": 3, "page_index": 0, "element_id": "p001-…", "field": "text",
         "system_value": "…", "system_confidence": 0.9}]
    }
  修正單的 id 可省略（依內容自動產生，重複匯入不會重複）；source 一律是 user。
  field 用該階段產物中的欄位名稱，例如 ③ 的 text、font_size、bold、color；④ 的 role、alignment；
  ⑤ 的 font_family、size_pt；② 的 size（頁面實際尺寸）。

二、FeedbackStore：存、依樣本讀、轉成標註資料（JSONL）、統計每個欄位被修正的比例。
  標註資料每一列：sample、page_index、stage、element_id、field、system_value、system_confidence、
  correct_value、corrected（是否被修正）、kind（correction／confirmation）、feedback_id、instruction。
  系統當時的值：優先用回饋裡的快照；沒有快照時，到該樣本的流水線工作目錄裡找
  （resolve_value：在該階段的產物中找 id 相符的元素，再找欄位；找不到就留空並標 resolved=false）。

三、apply_feedback：把回饋的修正單送進 Pipeline 重跑（使用者修正鎖定，之後重跑不會被覆蓋）。

四、信心值校準：由（系統當時的信心值、是否被修正）做分箱可靠度表（等寬分箱），
  可靠度 = 該箱中「沒有被修正」的比例；用加權的相鄰違反合併法（PAV）把各箱可靠度修成單調不減，
  再於各箱平均信心值之間線性內插，得到「原始信心值 → 校準後信心值」的對應。
  樣本總數或有效箱數不足時回報 sufficient=false 與原因，calibrate() 原樣交回原始信心值，不硬算。

已知限制：
- 只有「明確」的修正與確認才算數；使用者看過但沒動的欄位不會自動當成正確（那會讓可靠度偏高）。
- resolve_value 是通用的產物搜尋：同一元素下有多個同名欄位（例如一行有多個樣式段）時取第一個。
- 校準表是全域或依欄位分開建；尚未依輸入類型（截圖／照片）分層。

可單獨升級的點：分層校準（依階段×欄位×輸入類型）；改用 isotonic regression 或 Platt scaling；
回饋介面直接寫入系統值快照（就不必回頭查工作目錄）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np
from pydantic import BaseModel

from ..artifacts import Correction, CorrectionSource, FidelityReport, Measured

SCHEMA = "mortise.feedback/1"

DEFAULT_PARAMS: dict[str, Any] = {
    "calib_bins": 10,  # 校準分箱數（等寬，信心值 0 到 1）
    "calib_min_total": 50,  # 至少要有幾筆（信心值、是否修正）才建校準表（筆）
    "calib_min_per_bin": 5,  # 一箱至少幾筆才算有效箱（筆）
    "calib_min_bins": 2,  # 至少要有幾個有效箱才能內插（箱）
}


def _p(params: dict | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:12]


def _jsonable(v: Any) -> Any:
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


# ====================================================================== 格式


@dataclass
class PageRating:
    page_index: Optional[int]  # None 表示整份文件
    score: float
    max_score: float = 5.0
    comment: str = ""


@dataclass
class Confirmation:
    target_stage: int
    field: str
    element_id: Optional[str] = None
    page_index: Optional[int] = None
    comment: str = ""
    system_value: Any = None
    system_confidence: Optional[float] = None


@dataclass
class Feedback:
    sample: str
    corrections: list[Correction] = field(default_factory=list)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)  # 修正單 id → {"system_value", "system_confidence"}
    confirmations: list[Confirmation] = field(default_factory=list)
    page_ratings: list[PageRating] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    author: str = ""
    created: str = ""
    feedback_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        cors = []
        for c in self.corrections:
            d = c.model_dump(mode="json")
            d.update(self.snapshots.get(c.id, {}))
            cors.append(d)
        return {"schema": SCHEMA, "sample": self.sample, "feedback_id": self.feedback_id, "author": self.author, "created": self.created,
                "comments": self.comments, "page_ratings": [asdict(r) for r in self.page_ratings], "corrections": cors,
                "confirmations": [asdict(c) for c in self.confirmations]}


def parse_feedback(data: dict[str, Any], sample: Optional[str] = None) -> Feedback:
    """由 dict 建立回饋（補上修正單 id 與來源）。"""
    sample = data.get("sample") or sample or ""
    corrections, snaps = [], {}
    for n, c in enumerate(data.get("corrections", [])):
        c = dict(c)
        snap = {k: c.pop(k) for k in ("system_value", "system_confidence") if k in c}
        c.setdefault("source", CorrectionSource.USER.value)
        c.setdefault("id", "fb-" + _digest(sample, c.get("target_stage"), c.get("page_index"), c.get("element_id"), c.get("field"), c.get("new_value"), n)[:10])
        cor = Correction.model_validate(c)
        corrections.append(cor)
        if snap:
            snaps[cor.id] = snap
    comments = list(data.get("comments", []))
    if data.get("comment"):
        comments.insert(0, str(data["comment"]))
    fb = Feedback(
        sample=sample, corrections=corrections, snapshots=snaps,
        confirmations=[Confirmation(**{k: v for k, v in c.items() if k in Confirmation.__dataclass_fields__}) for c in data.get("confirmations", [])],
        page_ratings=[PageRating(**{k: v for k, v in r.items() if k in PageRating.__dataclass_fields__}) for r in data.get("page_ratings", [])],
        comments=comments, author=data.get("author", ""), created=data.get("created", ""), feedback_id=data.get("feedback_id", ""),
    )
    if not fb.feedback_id:
        fb.feedback_id = _digest(fb.to_dict())
    return fb


def load_feedback(path: Path, sample: Optional[str] = None) -> Feedback:
    return parse_feedback(json.loads(Path(path).read_text(encoding="utf-8")), sample)


# ====================================================================== 找系統當時的值


def _find_by_id(obj: Any, eid: str) -> Any:
    if isinstance(obj, BaseModel):
        if getattr(obj, "id", None) == eid:
            return obj
        for k in obj.__class__.model_fields:
            r = _find_by_id(getattr(obj, k), eid)
            if r is not None:
                return r
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            r = _find_by_id(x, eid)
            if r is not None:
                return r
    return None


FIELD_ALIASES = {"font_family": ["font_family", "family"], "size": ["size", "page_width_pt"], "font_size": ["font_size", "size_pt"],
                 "alignment": ["alignment", "align"], "color": ["color"]}


def _find_field(obj: Any, names: list[str]) -> tuple[bool, Any]:
    """在 obj 與其子物件中找第一個名稱相符的欄位（廣度優先，最多 6 層）。

    先找真正的欄位（帶信心值的 Measured 在這裡），都找不到才用計算屬性（例如 TextLine.text）。"""
    for use_props in (False, True):
        queue = [obj]
        for _ in range(6):
            nxt = []
            for o in queue:
                if isinstance(o, BaseModel):
                    for n in names:
                        if n in o.__class__.model_fields or (use_props and isinstance(getattr(o.__class__, n, None), property)):
                            return True, getattr(o, n)
                    nxt.extend(getattr(o, k) for k in o.__class__.model_fields)
                elif isinstance(o, (list, tuple)):
                    nxt.extend(o)
            queue = nxt
            if not queue:
                break
    return False, None


def resolve_value(artifacts: Any, stage: int, field_name: str, element_id: Optional[str] = None, page_index: Optional[int] = None) -> dict[str, Any]:
    """在一次流水線結果（runner.RunArtifacts）中找出某階段某元素某欄位的值與信心值。"""
    page = page_index or 0
    root = {1: lambda: artifacts.doc, 2: lambda: artifacts.specs[page], 3: lambda: artifacts.elements[page],
            4: lambda: artifacts.layout, 5: lambda: artifacts.styled, 7: lambda: artifacts.report}.get(stage, lambda: None)()
    if root is None:
        return {"resolved": False, "system_value": None, "system_confidence": None}
    obj = _find_by_id(root, element_id) if element_id else root
    if obj is None:
        return {"resolved": False, "system_value": None, "system_confidence": None}
    ok, v = _find_field(obj, FIELD_ALIASES.get(field_name, [field_name]))
    if not ok:
        return {"resolved": False, "system_value": None, "system_confidence": None}
    if isinstance(v, Measured):
        return {"resolved": True, "system_value": _jsonable(v.value), "system_confidence": float(v.confidence)}
    conf = getattr(obj, "confidence", None)
    return {"resolved": True, "system_value": _jsonable(v), "system_confidence": float(conf) if isinstance(conf, (int, float)) else None}


# ====================================================================== 回饋庫


class FeedbackStore:
    """回饋庫：root/<樣本路徑，斜線換成 __>/<feedback_id>.json。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, sample: str) -> Path:
        return self.root / sample.replace("/", "__")

    def add(self, fb: Feedback | dict, sample: Optional[str] = None) -> Path:
        if isinstance(fb, dict):
            fb = parse_feedback(fb, sample)
        if sample:
            fb.sample = sample
        if not fb.created:
            fb.created = _dt.datetime.now().isoformat(timespec="seconds")
        d = self._dir(fb.sample)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{fb.feedback_id}.json"
        path.write_text(json.dumps(fb.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return path

    def get(self, sample: str) -> list[Feedback]:
        d = self._dir(sample)
        if not d.exists():
            return []
        out = [load_feedback(f, sample) for f in sorted(d.glob("*.json"))]
        return sorted(out, key=lambda f: (f.created, f.feedback_id))

    def samples(self) -> list[str]:
        out = []
        for d in sorted(self.root.iterdir()):
            fbs = list(d.glob("*.json")) if d.is_dir() else []
            if fbs:
                out.append(load_feedback(fbs[0]).sample or d.name.replace("__", "/"))
        return out

    def all(self) -> list[Feedback]:
        return [fb for s in self.samples() for fb in self.get(s)]

    def import_sample_dir(self, sample_dir: Path) -> int:
        """把樣本目錄裡所有 .feedback.json 匯入回饋庫（同內容重複匯入不會重複）。回傳匯入幾份。"""
        from .runner import discover

        n = 0
        for s in discover(Path(sample_dir)):
            if s.feedback_path:
                self.add(load_feedback(s.feedback_path, s.key), s.key)
                n += 1
        return n

    # ---- 標註資料
    def to_annotations(self, resolver: Optional[Callable[[str], Any]] = None, samples: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
        """把回饋轉成標註資料。resolver(樣本) 回傳該樣本的 RunArtifacts（找系統當時的值用），可省略。"""
        rows: list[dict[str, Any]] = []
        cache: dict[str, Any] = {}
        for s in (list(samples) if samples is not None else self.samples()):
            for fb in self.get(s):
                def system(stage, fld, eid, page, snap):
                    if snap and "system_value" in snap:
                        return {"resolved": True, "system_value": snap.get("system_value"), "system_confidence": snap.get("system_confidence")}
                    if resolver is None:
                        return {"resolved": False, "system_value": None, "system_confidence": None}
                    if s not in cache:
                        try:
                            cache[s] = resolver(s)
                        except Exception:  # noqa: BLE001
                            cache[s] = None
                    return resolve_value(cache[s], stage, fld, eid, page) if cache[s] is not None else {"resolved": False, "system_value": None, "system_confidence": None}

                for c in fb.corrections:
                    sv = system(c.target_stage, c.field, c.element_id, c.page_index, fb.snapshots.get(c.id))
                    rows.append({"sample": s, "page_index": c.page_index, "stage": c.target_stage, "element_id": c.element_id, "field": c.field,
                                 **sv, "correct_value": _jsonable(c.new_value), "corrected": True, "kind": "correction",
                                 "feedback_id": fb.feedback_id, "instruction": c.instruction})
                for c in fb.confirmations:
                    snap = {"system_value": c.system_value, "system_confidence": c.system_confidence} if c.system_value is not None or c.system_confidence is not None else None
                    sv = system(c.target_stage, c.field, c.element_id, c.page_index, snap)
                    rows.append({"sample": s, "page_index": c.page_index, "stage": c.target_stage, "element_id": c.element_id, "field": c.field,
                                 **sv, "correct_value": sv["system_value"], "corrected": False, "kind": "confirmation",
                                 "feedback_id": fb.feedback_id, "instruction": c.comment})
        return rows

    def write_annotations(self, path: Path, resolver: Optional[Callable[[str], Any]] = None) -> int:
        rows = self.to_annotations(resolver)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return len(rows)


def read_annotations(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def correction_stats(annotations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每個（階段、欄位）被看過幾次、被修正幾次、修正比例。鍵為 "階段:欄位"。"""
    out: dict[str, dict[str, Any]] = {}
    for r in annotations:
        k = f"{r['stage']}:{r['field']}"
        d = out.setdefault(k, {"stage": r["stage"], "field": r["field"], "reviewed": 0, "corrected": 0})
        d["reviewed"] += 1
        d["corrected"] += int(bool(r["corrected"]))
    for d in out.values():
        d["correction_ratio"] = d["corrected"] / d["reviewed"] if d["reviewed"] else None
    return dict(sorted(out.items(), key=lambda kv: -(kv[1]["correction_ratio"] or 0)))


# ====================================================================== 套用回饋


def apply_feedback(pipe: Any, fb: Feedback | list[Feedback]) -> FidelityReport:
    """把回饋中的修正單送進 Pipeline 重跑（已加入過的同 id 修正單不重複加入）。"""
    fbs = fb if isinstance(fb, list) else [fb]
    have = {c.id for c in pipe.corrections}
    new = []
    for f in fbs:
        for c in f.corrections:
            if c.id not in have:
                new.append(c.model_copy(update={"source": CorrectionSource.USER}))
                have.add(c.id)
    if new:
        pipe.add_corrections(new)
    return pipe.run()


# ====================================================================== 信心值校準


@dataclass
class CalibrationBin:
    lo: float
    hi: float
    n: int
    mean_confidence: Optional[float]
    accuracy: Optional[float]  # 沒有被修正的比例


@dataclass
class CalibrationTable:
    bins: list[CalibrationBin]
    n_total: int
    sufficient: bool
    reason: str
    ece: Optional[float]  # 期望校準誤差（加權平均 |信心值 − 可靠度|）
    knots_x: list[float] = field(default_factory=list)  # 內插節點：平均信心值
    knots_y: list[float] = field(default_factory=list)  # 單調化後的可靠度
    scope: str = "all"

    def calibrate(self, confidence: float) -> float:
        """原始信心值 → 校準後信心值；資料不足時原樣交回。"""
        if not self.sufficient or not self.knots_x:
            return float(confidence)
        return float(np.interp(float(confidence), self.knots_x, self.knots_y))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pav(y: list[float], w: list[float]) -> list[float]:
    """加權的相鄰違反合併法：讓序列單調不減。"""
    blocks = [[yi, wi, 1] for yi, wi in zip(y, w)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            a, b = blocks[i], blocks[i + 1]
            tw = a[1] + b[1]
            blocks[i] = [(a[0] * a[1] + b[0] * b[1]) / tw, tw, a[2] + b[2]]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for v, _, n in blocks:
        out.extend([v] * n)
    return out


def build_calibration(pairs: Iterable[tuple[float, bool]], params: dict | None = None, scope: str = "all") -> CalibrationTable:
    """pairs：（系統信心值、是否正確（沒被修正））。"""
    p = _p(params)
    data = [(float(c), bool(ok)) for c, ok in pairs if c is not None]
    nb = int(p["calib_bins"])
    edges = np.linspace(0.0, 1.0, nb + 1)
    bins = []
    for k in range(nb):
        lo, hi = float(edges[k]), float(edges[k + 1])
        sel = [(c, ok) for c, ok in data if (lo <= c < hi) or (k == nb - 1 and c == 1.0)]
        bins.append(CalibrationBin(lo, hi, len(sel), float(np.mean([c for c, _ in sel])) if sel else None,
                                   float(np.mean([ok for _, ok in sel])) if sel else None))
    n = len(data)
    valid = [b for b in bins if b.n >= p["calib_min_per_bin"]]
    if n < p["calib_min_total"]:
        return CalibrationTable(bins, n, False, f"樣本不足：只有 {n} 筆，至少需要 {p['calib_min_total']} 筆才建校準表。", None, scope=scope)
    if len(valid) < p["calib_min_bins"]:
        return CalibrationTable(bins, n, False, f"有效分箱不足：只有 {len(valid)} 箱達到每箱 {p['calib_min_per_bin']} 筆（信心值太集中）。", None, scope=scope)
    ece = sum(b.n * abs(b.mean_confidence - b.accuracy) for b in bins if b.n) / n
    ys = _pav([b.accuracy for b in valid], [b.n for b in valid])
    return CalibrationTable(bins, n, True, "", float(ece), [b.mean_confidence for b in valid], [float(y) for y in ys], scope)


def calibration_from_annotations(annotations: list[dict[str, Any]], stage: Optional[int] = None, field_name: Optional[str] = None,
                                 params: dict | None = None) -> CalibrationTable:
    rows = [r for r in annotations if r.get("system_confidence") is not None and (stage is None or r["stage"] == stage)
            and (field_name is None or r["field"] == field_name)]
    scope = "all" if stage is None and field_name is None else f"{stage if stage is not None else '*'}:{field_name or '*'}"
    return build_calibration([(r["system_confidence"], not r["corrected"]) for r in rows], params, scope)
