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
         "system_value": "…", "system_confidence": 0.9}],
      "issues": [                            ← （可選，2026-11 追加）使用者標的問題（網頁的「回報錯誤」與標籤）
        {"page_index": 0, "element_id": "p001-…", "tags": ["font_wrong"], "comment": "…", "correct": false,
         "system_value": "…", "system_confidence": 0.8}],   ← （可選）被標錯的元素當時的值與信心值（⑦報告的元素信心值）
      "snapshot_missing": ["u-…"],           ← （可選，2026-11 追加）當時沒有記下系統值的修正單 id（這幾筆不能拿來校準信心值）
      "job": {"job_id": "…", "filename": "季報.pdf", "consented": false},   ← （可選）來自哪一份工作
      "exported_with": {"version": "mortise-0.1.0+…", "name": "樟雀-07"}   ← （可選）匯出時的引擎版本
    }
  修正單的 id 可省略（依內容自動產生，重複匯入不會重複）；source 一律是 user。
  追加的欄位都是頂層的選填欄位（issues、snapshot_missing、job、exported_with）：舊檔案照常讀；舊版程式讀新檔案時會略過
  這幾個頂層欄位（所以修正單裡不放新的鍵：修正單是 Correction 格式，多一個鍵舊版程式就讀不了）。
  2026-11-23 以前匯出的回饋包，修正單裡可能有 "system_value_missing": true（同 snapshot_missing），照樣讀得懂。
  不受信任的檔案（例如別人交來的回饋包）：job_id、修正單 id、feedback_id 只接受英數、減號與底線（job_id 只接受英數與減號，
  與工作編號相同），不合格的 job_id 丟掉、id 換成內容雜湊；樣本名只取檔名。寫檔前一律確認路徑還在目標目錄底下。
  field 用該階段產物中的欄位名稱，例如 ③ 的 text、font_size、bold、color；④ 的 role、alignment；
  ⑤ 的 font_family、size_pt；② 的 size（頁面實際尺寸）；"element" 表示整個元素（網頁的「這個元素對／錯」）。

二、FeedbackStore：存、依樣本讀、轉成標註資料（JSONL）、統計每個欄位被修正的比例。
  標註資料每一列：sample、page_index、stage、element_id、field、system_value、system_confidence、
  correct_value、corrected（是否被修正）、kind（correction／confirmation）、feedback_id、instruction。
  系統當時的值：優先用回饋裡的快照；沒有快照時，到該樣本的流水線工作目錄裡找
  （resolve_value：在該階段的產物中找 id 相符的元素，再找欄位；找不到就留空並標 resolved=false）。
  文字（field = text）取整行或整段的字（不是第一個樣式段），信心值用③的文字信心值（擷取產線是精確的，記 1.0；
  段落取各行最低的）；整個元素（field = element）取⑦報告的元素信心值（網頁上顯示的那個）。
  網頁的「這個元素是錯的」（issues 裡 correct = false 而且有 element_id）也變成標註資料（kind = flag、corrected = true），
  和「這個元素是對的」（confirmation）一起，就是元素層級的校準資料（calibrate --field element）。
  同一份工作從兩個來源進來（自動收的回饋庫、使用者匯出的回饋包）時：修正依修正單 id、確認與標錯依內容只算一次；
  評分、標記、意見要合在一起看時用 merge_feedback（依內容去重）。

三、apply_feedback：把回饋的修正單送進 Pipeline 重跑（使用者修正鎖定，之後重跑不會被覆蓋）。

三之一、回饋包（網頁「匯出回饋」下載的 <原檔名>.mortise-feedback.zip，見 write_bundle）：
    README.txt                   白話說明
    report.json                  還原度報告（工作還沒處理完或失敗時沒有這個檔）
    sample/<原檔>                使用者上傳的原始檔
    sample/<原檔主檔名>.feedback.json   回饋（本格式）；和原檔放在同一個目錄，所以 sample/ 本身就是一個樣本目錄
  讀回：FeedbackStore.import_bundle(zip)，或 python -m mortise.eval feedback-import 回饋包.zip --store 回饋庫目錄。

四、信心值校準：由（系統當時的信心值、是否被修正）做分箱可靠度表（等寬分箱），
  可靠度 = 該箱中「沒有被修正」的比例；用加權的相鄰違反合併法（PAV）把各箱可靠度修成單調不減，
  再於各箱平均信心值之間線性內插，得到「原始信心值 → 校準後信心值」的對應。
  樣本總數或有效箱數不足時回報 sufficient=false 與原因，calibrate() 原樣交回原始信心值，不硬算。
  另外「被修正的」與「沒被修正的」（看過、確認是對的）各至少 calib_min_each 筆才建表：只有修正、沒有確認的資料
  有選擇偏差（只看到錯的，會把任何信心值都校準成 0），反過來也一樣。

已知限制：
- 只有「明確」的修正與確認才算數；使用者看過但沒動的欄位不會自動當成正確（那會讓可靠度偏高）。
- resolve_value 是通用的產物搜尋：同一元素下有多個同名欄位（例如一行有多個樣式段的字型）時取第一個（文字除外，見上）。
- 校準表是全域或依欄位分開建；尚未依輸入類型（截圖／照片）分層。

可單獨升級的點：分層校準（依階段×欄位×輸入類型）；改用 isotonic regression 或 Platt scaling；
回饋介面直接寫入系統值快照（就不必回頭查工作目錄）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
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
    "calib_min_each": 5,  # 「被修正的」與「沒被修正的」各至少幾筆（筆）：只有一種時有選擇偏差，不建表
}


def _p(params: dict | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:12]


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SAFE_JOB_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def safe_id(value: Any) -> bool:
    """修正單 id、feedback_id：只接受英數、減號與底線（最多 64 字），可以直接當檔名的一部分。"""
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None


def safe_job_id(value: Any) -> bool:
    """工作編號：只接受英數與減號（與 api/jobs.py 的 _safe_id 相同）。"""
    return isinstance(value, str) and _SAFE_JOB_ID.fullmatch(value) is not None


def safe_filename(name: Any, default: str = "upload") -> str:
    """不受信任的檔名：只取最後一段（去掉目錄與 ..），去掉控制字元；剩下空的或是 . 與 .. 時用 default。"""
    base = str(name or "").replace("\\", "/").split("/")[-1]
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f").strip()
    return default if base in ("", ".", "..") else base


def inside(root: Path, path: Path) -> Path:
    """確認 path 在 root 底下（解析符號連結與 .. 之後）；不在就丟 ValueError。回傳 path。"""
    r, q = Path(root).resolve(), Path(path).resolve()
    if q != r and r not in q.parents:
        raise ValueError(f"路徑跑到目標目錄外面了：{path}")
    return Path(path)


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
class FlaggedIssue:
    """使用者標的問題（「這裡有錯」與標籤）。tags 的建議值見 TAGS。"""

    page_index: Optional[int] = None
    element_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    comment: str = ""
    correct: Optional[bool] = None  # False：使用者說這個元素是錯的；None：只是整頁的標籤或意見
    system_value: Any = None  # （選填）被標錯的元素當時的值（文字框是它的字）
    system_confidence: Optional[float] = None  # （選填）被標錯的元素當時的信心值（⑦報告的元素信心值）


# 網頁「回報錯誤」的標籤建議值（與回饋 API 契約一致）→ 白話說明
TAGS = {
    "text_wrong": "字認錯了", "font_wrong": "字型不對", "size_wrong": "字的大小不對", "color_wrong": "顏色不對",
    "position_wrong": "位置不對", "missing": "少了東西", "extra": "多了東西", "table_wrong": "表格不對",
    "layout_broken": "版面亂掉", "corners_wrong": "紙張四角找錯", "other": "其他",
}


@dataclass
class Feedback:
    sample: str
    corrections: list[Correction] = field(default_factory=list)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)  # 修正單 id → {"system_value", "system_confidence"}（或 {"system_value_missing": True}）
    confirmations: list[Confirmation] = field(default_factory=list)
    page_ratings: list[PageRating] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    author: str = ""
    created: str = ""
    feedback_id: str = ""
    issues: list[FlaggedIssue] = field(default_factory=list)
    job: dict[str, Any] = field(default_factory=dict)
    exported_with: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cors, missing = [], []
        for c in self.corrections:
            d = c.model_dump(mode="json")
            snap = self.snapshots.get(c.id, {})
            if snap.get("system_value_missing"):
                missing.append(c.id)  # 放頂層（舊版程式讀修正單時不接受多的鍵）
            else:
                d.update({k: v for k, v in snap.items() if k in ("system_value", "system_confidence")})
            cors.append(d)
        out = {"schema": SCHEMA, "sample": self.sample, "feedback_id": self.feedback_id, "author": self.author, "created": self.created,
               "comments": self.comments, "page_ratings": [asdict(r) for r in self.page_ratings], "corrections": cors,
               "confirmations": [asdict(c) for c in self.confirmations]}
        # 追加的選填欄位：沒有內容就不寫，舊格式的檔案存回去仍是原樣（feedback_id 也不變）
        if missing:
            out["snapshot_missing"] = missing
        if self.issues:
            out["issues"] = [{k: v for k, v in asdict(i).items() if v is not None or k not in ("system_value", "system_confidence")} for i in self.issues]
        if self.job:
            out["job"] = self.job
        if self.exported_with:
            out["exported_with"] = self.exported_with
        return out


def parse_feedback(data: dict[str, Any], sample: Optional[str] = None) -> Feedback:
    """由 dict 建立回饋（補上修正單 id 與來源）。"""
    sample = data.get("sample") or sample or ""
    corrections, snaps = [], {}
    missing = {str(x) for x in (data.get("snapshot_missing") or [])}
    for n, c in enumerate(data.get("corrections", [])):
        c = dict(c)
        snap = {k: c.pop(k) for k in ("system_value", "system_confidence", "system_value_missing") if k in c}
        c.setdefault("source", CorrectionSource.USER.value)
        auto = "fb-" + _digest(sample, c.get("target_stage"), c.get("page_index"), c.get("element_id"), c.get("field"), c.get("new_value"), n)[:10]
        given = c.get("id")
        if given is None or not safe_id(given):  # 不受信任的 id（可能拿來組檔名）：換成內容雜湊
            c["id"] = auto if given is None else "fb-" + _digest(str(given), auto)[:10]
        cor = Correction.model_validate(c)
        corrections.append(cor)
        if given is not None and str(given) in missing:
            snap["system_value_missing"] = True
        if snap.get("system_value_missing"):
            snap = {"system_value_missing": True}
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
        issues=[FlaggedIssue(**{k: v for k, v in i.items() if k in FlaggedIssue.__dataclass_fields__}) for i in data.get("issues") or []],
        job=dict(data.get("job") or {}) if isinstance(data.get("job") or {}, dict) else {},
        exported_with=dict(data.get("exported_with") or {}) if isinstance(data.get("exported_with") or {}, dict) else {},
    )
    if "job_id" in fb.job and not safe_job_id(fb.job.get("job_id")):
        fb.job.pop("job_id")  # 不像工作編號（可能拿來組路徑）：丟掉
    if "filename" in fb.job:
        fb.job["filename"] = safe_filename(fb.job.get("filename"))
    if not safe_id(fb.feedback_id):
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


_UNRESOLVED = {"resolved": False, "system_value": None, "system_confidence": None}


def _text_of(obj: Any) -> Optional[str]:
    """一個元素的整串文字：文字行（③，各樣式段接起來）、段落（⑤，各段字接起來）、文字框（各段落以換行接起來）。不是文字元素回 None。"""
    runs = getattr(obj, "runs", None)
    if isinstance(runs, list) and all(hasattr(r, "text") for r in runs):
        return "".join(str(r.text) for r in runs)
    paras = getattr(obj, "paragraphs", None)
    if isinstance(paras, list) and paras and all(_text_of(p) is not None for p in paras):
        return "\n".join(_text_of(p) for p in paras)
    return None


def _line_confidence(artifacts: Any, page: int, line: Any) -> Optional[float]:
    """③一行文字內容的信心值：影像產線用 text_confidence；擷取產線的文字是精確的，記 1.0。"""
    conf = getattr(line, "text_confidence", None)
    if isinstance(conf, (int, float)):
        return float(conf)
    try:
        if getattr(artifacts.elements[page].production_line, "value", None) == "extract":
            return 1.0
    except (AttributeError, IndexError, TypeError):
        pass
    return None


def _text_confidence(artifacts: Any, page: int, obj: Any, element_id: Optional[str]) -> Optional[float]:
    """文字的信心值：文字行直接取；段落或文字框（④⑤的編號）依④的 line_ids 找回③的行，取最低的。"""
    if hasattr(obj, "text_confidence"):
        return _line_confidence(artifacts, page, obj)
    try:
        lay = next(pl for pl in artifacts.layout.pages if pl.page_index == page)
        lines = {t.id: t for t in artifacts.elements[page].texts}
    except (AttributeError, IndexError, StopIteration, TypeError):
        return None
    paras = {p.id: p for p in lay.paragraphs}
    pids = [element_id] if element_id in paras else next((list(tb.paragraph_ids) for tb in lay.text_boxes if tb.id == element_id), [])
    confs = [_line_confidence(artifacts, page, lines[lid]) for pid in pids if pid in paras for lid in paras[pid].line_ids if lid in lines]
    confs = [c for c in confs if c is not None]
    return min(confs) if confs else None


def _element_value(artifacts: Any, page: int, element_id: Optional[str]) -> dict[str, Any]:
    """整個元素（網頁的「這個元素對／錯」）：信心值取⑦報告的元素信心值（網頁上顯示的那個），值取⑤的字（文字框）或元素種類。"""
    rep = getattr(artifacts, "report", None)
    if rep is None or not element_id:
        return dict(_UNRESOLVED)
    pages = sorted(rep.pages, key=lambda pr: pr.page_index != page)  # 先找指定的頁
    er = next((e for pr in pages for e in pr.elements if e.element_id == element_id), None)
    if er is None:
        return dict(_UNRESOLVED)
    obj = _find_by_id(artifacts.styled, element_id) if getattr(artifacts, "styled", None) is not None else None
    text = _text_of(obj) if obj is not None else None
    return {"resolved": True, "system_value": text if text is not None else er.kind, "system_confidence": float(er.confidence)}


def resolve_value(artifacts: Any, stage: int, field_name: str, element_id: Optional[str] = None, page_index: Optional[int] = None) -> dict[str, Any]:
    """在一次流水線結果（runner.RunArtifacts）中找出某階段某元素某欄位的值與信心值。
    文字（text）取整串字與文字信心值（見開頭「二」）；整個元素（element）取⑦報告的元素信心值。"""
    page = page_index or 0
    if field_name == "element":
        return _element_value(artifacts, page, element_id)
    try:
        root = {1: lambda: artifacts.doc, 2: lambda: artifacts.specs[page], 3: lambda: artifacts.elements[page],
                4: lambda: artifacts.layout, 5: lambda: artifacts.styled, 7: lambda: artifacts.report}.get(stage, lambda: None)()
    except (IndexError, AttributeError):
        root = None
    if root is None:
        return dict(_UNRESOLVED)
    obj = _find_by_id(root, element_id) if element_id else root
    if obj is None:
        return dict(_UNRESOLVED)
    if field_name == "text" and element_id:
        text = _text_of(obj)
        if text is not None:  # 不要只拿到第一個樣式段的字
            return {"resolved": True, "system_value": text, "system_confidence": _text_confidence(artifacts, page, obj, element_id)}
    ok, v = _find_field(obj, FIELD_ALIASES.get(field_name, [field_name]))
    if not ok:
        return dict(_UNRESOLVED)
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
        """樣本的目錄：斜線換成 __；名字是空的、. 或 .. 時前面加 _（樣本名可能來自別人交來的回饋包）。"""
        name = "".join(ch for ch in str(sample).replace("\\", "/").replace("/", "__") if ch >= " ")
        if name in ("", ".", ".."):
            name = "_" + name
        return inside(self.root, self.root / name)

    def add(self, fb: Feedback | dict, sample: Optional[str] = None) -> Path:
        if isinstance(fb, dict):
            fb = parse_feedback(fb, sample)
        if sample:
            fb.sample = sample
        if not fb.created:
            fb.created = _dt.datetime.now().isoformat(timespec="seconds")
        if not safe_id(fb.feedback_id):
            fb.feedback_id = _digest(fb.to_dict())
        d = self._dir(fb.sample)
        d.mkdir(parents=True, exist_ok=True)
        path = inside(d, d / f"{fb.feedback_id}.json")
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

    def import_bundle(self, path: Path, samples_dir: Optional[Path] = None) -> Feedback:
        """匯入網頁匯出的回饋包（.mortise-feedback.zip）。samples_dir 有給時，另把原始檔與 .feedback.json 放進那個樣本目錄
        （之後可以 python -m mortise.eval run 那個目錄）。回傳匯入的回饋。"""
        b = read_bundle(path)
        # 回饋庫裡的樣本名與「同意後自動收進回饋庫」的相同（<工作編號>__<原檔名>）：同一份工作從兩個來源進來時，
        # 同一筆修正只算一次（見 to_annotations）。job_id 與檔名在 read_bundle 已檢查過（不會帶目錄或 ..）
        jid = str(b.feedback.job.get("job_id") or "")
        key = f"{jid}__{b.sample_name}" if jid else safe_filename(b.feedback.sample or b.sample_name)
        b.feedback.sample = key
        self.add(b.feedback, key)
        if samples_dir is not None and b.sample_bytes is not None:
            d = Path(samples_dir)
            d.mkdir(parents=True, exist_ok=True)
            inside(d, d / key).write_bytes(b.sample_bytes)
            inside(d, d / (Path(key).stem + ".feedback.json")).write_text(json.dumps(b.feedback.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return b.feedback

    # ---- 標註資料
    def to_annotations(self, resolver: Optional[Callable[[str], Any]] = None, samples: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
        """把回饋轉成標註資料。resolver(樣本) 回傳該樣本的 RunArtifacts（找系統當時的值用），可省略。"""
        rows: list[dict[str, Any]] = []
        cache: dict[str, Any] = {}
        seen: set[tuple[str, str]] = set()
        seen_rows: dict[tuple, int] = {}  # 確認與標錯依內容去重：（樣本、種類、階段、頁、元素、欄位）→ rows 的位置
        user_values: dict[str, dict[tuple, Any]] = {}

        def add_once(key: tuple, row: dict[str, Any]) -> None:
            """同一個確認或標錯從兩個來源進來時只算一次；後來的有信心值、先前的沒有時換成後來的。"""
            if key in seen_rows:
                i = seen_rows[key]
                if rows[i].get("system_confidence") is None and row.get("system_confidence") is not None:
                    rows[i] = row
                return
            seen_rows[key] = len(rows)
            rows.append(row)

        for s in (list(samples) if samples is not None else self.samples()):
            for fb in self.get(s):
                def system(stage, fld, eid, page, snap):
                    if snap and snap.get("system_value_missing"):
                        return {"resolved": False, "system_value": None, "system_confidence": None}
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
                    # 同一筆修正（同一個修正單 id）可能從兩個來源進來（同意後收進回饋庫的、使用者自己匯出的回饋包）：只算一次
                    if (s, c.id) in seen:
                        continue
                    seen.add((s, c.id))
                    sv = system(c.target_stage, c.field, c.element_id, c.page_index, fb.snapshots.get(c.id))
                    target = (c.target_stage, c.page_index, c.element_id, c.field)
                    # 同一個欄位改第二次時，「系統當時的值」其實是使用者上一次改的值（信心值 1.0），不是系統的判斷：標出來，校準時不算
                    was_user = target in user_values.get(s, {}) and _jsonable(user_values[s][target]) == sv.get("system_value")
                    user_values.setdefault(s, {})[target] = c.new_value
                    rows.append({"sample": s, "page_index": c.page_index, "stage": c.target_stage, "element_id": c.element_id, "field": c.field,
                                 **sv, "correct_value": _jsonable(c.new_value), "corrected": True, "kind": "correction",
                                 "feedback_id": fb.feedback_id, "instruction": c.instruction, **({"system_was_user": True} if was_user else {})})
                for c in fb.confirmations:
                    snap = {"system_value": c.system_value, "system_confidence": c.system_confidence} if c.system_value is not None or c.system_confidence is not None else None
                    sv = system(c.target_stage, c.field, c.element_id, c.page_index, snap)
                    add_once((s, "confirmation", c.target_stage, c.page_index, c.element_id, c.field),
                             {"sample": s, "page_index": c.page_index, "stage": c.target_stage, "element_id": c.element_id, "field": c.field,
                              **sv, "correct_value": sv["system_value"], "corrected": False, "kind": "confirmation",
                              "feedback_id": fb.feedback_id, "instruction": c.comment})
                for i in fb.issues:  # 「這個元素是錯的」：元素層級的負面資料（和確認同一個階段與欄位，校準時可以一起看）
                    if i.correct is not False or not i.element_id:
                        continue
                    snap = {"system_value": i.system_value, "system_confidence": i.system_confidence} if i.system_value is not None or i.system_confidence is not None else None
                    sv = system(4, "element", i.element_id, i.page_index, snap)
                    add_once((s, "flag", 4, i.page_index, i.element_id, "element"),
                             {"sample": s, "page_index": i.page_index, "stage": 4, "element_id": i.element_id, "field": "element",
                              **sv, "correct_value": None, "corrected": True, "kind": "flag", "feedback_id": fb.feedback_id,
                              "instruction": "、".join(i.tags) + (f"：{i.comment}" if i.comment else "")})
        return rows

    def write_annotations(self, path: Path, resolver: Optional[Callable[[str], Any]] = None) -> int:
        rows = self.to_annotations(resolver)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return len(rows)


def merge_feedback(fbs: Iterable[Feedback]) -> Feedback:
    """把同一個樣本的多份回饋合成一份（依內容去重）：修正依修正單 id；確認、評分、標記、意見依內容。
    同一份工作從兩個來源進來（同意後自動收的、使用者匯出的回饋包）時，評分與標記不會算兩次。"""
    fbs = list(fbs)
    out = Feedback(sample=fbs[0].sample if fbs else "")
    seen: set = set()

    def once(key: Any) -> bool:
        k = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
        if k in seen:
            return False
        seen.add(k)
        return True

    for fb in fbs:
        for c in fb.corrections:
            if once(("c", c.id)):
                out.corrections.append(c)
                if c.id in fb.snapshots:
                    out.snapshots[c.id] = fb.snapshots[c.id]
            elif c.id in fb.snapshots and out.snapshots.get(c.id, {}).get("system_value_missing") and "system_value" in fb.snapshots[c.id]:
                out.snapshots[c.id] = fb.snapshots[c.id]  # 另一個來源有系統當時的值：用有的那個
        for c in fb.confirmations:
            if once(("k", c.target_stage, c.page_index, c.element_id, c.field)):
                out.confirmations.append(c)
            elif c.system_confidence is not None:  # 另一個來源有信心值：用有的那個
                for n, x in enumerate(out.confirmations):
                    if (x.target_stage, x.page_index, x.element_id, x.field) == (c.target_stage, c.page_index, c.element_id, c.field) and x.system_confidence is None:
                        out.confirmations[n] = c
        for r in fb.page_ratings:
            if once(("r", r.page_index, float(r.score), float(r.max_score), r.comment or "")):
                out.page_ratings.append(r)
        for i in fb.issues:
            if once(("i", i.page_index, i.element_id, sorted(i.tags), i.comment, i.correct)):
                out.issues.append(i)
        for t in fb.comments:
            if once(("t", t)):
                out.comments.append(t)
        out.job = out.job or dict(fb.job)
        out.exported_with = out.exported_with or dict(fb.exported_with)
        out.author = out.author or fb.author
        out.created = min([x for x in (out.created, fb.created) if x], default="")
    out.feedback_id = _digest({k: v for k, v in out.to_dict().items() if k != "feedback_id"})
    return out


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


# ====================================================================== 工作目錄 → 回饋；回饋包


def job_feedback_records(job_dir: Path) -> list[dict[str, Any]]:
    """工作目錄 feedback/ 裡使用者送出的回饋（POST /api/jobs/{id}/feedback 存的檔案），依時間排序。
    每筆加上 id（檔名）與 created（ISO 時間）。不是回饋的檔案（例如修正單的快照）略過。"""
    folder = Path(job_dir) / "feedback"
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "received_at" not in d or not any(k in d for k in ("rating", "comment", "tags", "correct")):
            continue
        t = d.get("received_at")
        created = _dt.datetime.fromtimestamp(float(t)).isoformat(timespec="seconds") if isinstance(t, (int, float)) else str(t or "")
        order = float(t) if isinstance(t, (int, float)) else 0.0
        out.append((order, {"id": f.stem, "created": created, **{k: d.get(k) for k in ("rating", "page", "element_id", "correct", "comment")},
                            "tags": list(d.get("tags") or [])}))
    return [r for _, r in sorted(out, key=lambda x: (x[0], x[1]["created"], x[1]["id"]))]  # 依收到的時間（到小數秒）排序


def correction_snapshots(*sources: Path) -> dict[str, dict[str, Any]]:
    """從檔案裡找「系統當時的值」快照：修正單 id → {"system_value", "system_confidence"}。
    接受 .json（單一修正、修正清單、或有 "corrections" 清單的回饋檔）與 .jsonl（每行一筆修正）；目錄就讀裡面的這兩種檔。
    只收有 id 而且有 system_value 的項目；後讀到的不覆蓋先讀到的（先給的來源優先）。"""
    out: dict[str, dict[str, Any]] = {}

    def take(item: Any) -> None:
        if isinstance(item, dict) and item.get("id") and "system_value" in item and item["id"] not in out:
            out[item["id"]] = {"system_value": item.get("system_value"), "system_confidence": item.get("system_confidence")}

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for x in obj:
                take(x)
        elif isinstance(obj, dict):
            take(obj)
            for x in obj.get("corrections", []) or []:
                take(x)

    for src in sources:
        src = Path(src)
        files = sorted(list(src.glob("*.json")) + list(src.glob("*.jsonl"))) if src.is_dir() else ([src] if src.exists() else [])
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
                if f.suffix == ".jsonl":
                    for line in text.splitlines():
                        if line.strip():
                            walk(json.loads(line))
                else:
                    walk(json.loads(text))
            except (OSError, ValueError):
                continue
    return out


def feedback_from_job(sample: str, records: list[dict[str, Any]], corrections: list[Correction], snapshots: dict[str, dict[str, Any]],
                      job: Optional[dict[str, Any]] = None, exported_with: Optional[dict[str, Any]] = None, created: str = "",
                      resolver: Optional[Callable[[int, str, Optional[str], Optional[int]], dict[str, Any]]] = None) -> Feedback:
    """把一份工作的評分、意見、標籤與修正單整理成一份回饋（mortise.feedback/1）。
    轉法與 api/jobs.py 收進回饋庫時相同（評分 → page_ratings；「這個元素是對的」→ confirmations；意見 → comments），
    另外把標籤與「這個元素是錯的」存成結構化的 issues。修正單沒有系統當時的值時記進 snapshot_missing。
    resolver(階段, 欄位, 元素編號, 頁) 回傳 resolve_value 的結果：有給時，「這個元素是對的／錯的」記下元素當時的值與信心值
    （校準要「看過但沒改」的資料，見開頭「四」）。"""

    def snap(eid: Optional[str], page_index: int) -> dict[str, Any]:
        if resolver is None or not eid:
            return {}
        try:
            r = resolver(4, "element", eid, page_index)
        except Exception:  # noqa: BLE001 —— 查不到不影響匯出
            return {}
        return {"system_value": r.get("system_value"), "system_confidence": r.get("system_confidence")} if r.get("resolved") else {}

    comments: list[str] = []
    ratings: list[PageRating] = []
    confirms: list[Confirmation] = []
    issues: list[FlaggedIssue] = []
    for r in records:
        page_index = (r.get("page") or 1) - 1
        if r.get("comment"):
            comments.append(str(r["comment"]))
        if r.get("tags"):
            comments.append("標籤：" + "、".join(r["tags"]))
        if r.get("rating") is not None:
            ratings.append(PageRating(page_index, float(r["rating"]), 5.0, r.get("comment") or ""))
        if r.get("element_id") and r.get("correct") is True:
            confirms.append(Confirmation(target_stage=4, field="element", element_id=r["element_id"], page_index=page_index, comment=r.get("comment") or "",
                                         **snap(r["element_id"], page_index)))
        if r.get("tags") or r.get("correct") is False:
            extra = snap(r.get("element_id"), page_index) if r.get("correct") is False else {}
            issues.append(FlaggedIssue(page_index if (r.get("page") or r.get("element_id")) else None, r.get("element_id"), list(r.get("tags") or []),
                                       r.get("comment") or "", r.get("correct"), **extra))
    cors, snaps = [], {}
    for c in corrections:
        if c.source != CorrectionSource.USER:
            continue
        cors.append(c)
        snaps[c.id] = snapshots.get(c.id) or {"system_value_missing": True}
    fb = Feedback(sample=sample, corrections=cors, snapshots=snaps, confirmations=confirms, page_ratings=ratings, comments=comments,
                  author="user", created=created, issues=issues, job=dict(job or {}), exported_with=dict(exported_with or {}))
    fb.feedback_id = _digest({k: v for k, v in fb.to_dict().items() if k != "exported_with"})
    return fb


BUNDLE_SUFFIX = ".mortise-feedback.zip"


def bundle_readme(filename: str, fb: Feedback, has_report: bool) -> str:
    stem = Path(filename).stem
    n_missing = sum(1 for c in fb.corrections if fb.snapshots.get(c.id, {}).get("system_value_missing"))
    n_seen = sum(1 for c in fb.confirmations if c.system_confidence is not None)
    n_flag = sum(1 for i in fb.issues if i.correct is False and i.system_confidence is not None)
    lines = [
        "Mortise 回饋包",
        "=" * 14,
        "",
        f"這是你在 Mortise 處理「{filename}」時留下的評價與修改，連同原始檔一起打包。這是你自己的資料，",
        "可以留著、交給開發者，或拿來評測新版本的引擎。",
        "",
        "裡面有什麼",
        "----------",
        f"- sample/{filename}：你上傳的原始檔。",
        f"- sample/{stem}.feedback.json：你的評分（{len(fb.page_ratings)} 筆）、修正（{len(fb.corrections)} 筆）、"
        f"標記（{len(fb.issues)} 筆）與意見。修正記著系統當時給的值與信心值"
        + (f"（其中 {n_missing} 筆當時沒有記下，這幾筆不能拿來校準）" if n_missing else "") + "；"
        f"「這個元素是對的」{n_seen} 筆、「這個元素是錯的」{n_flag} 筆記著元素當時的信心值。",
        "  校準信心值要兩種資料都有：你改掉或標錯的，以及你看過、確認是對的；只有其中一種時程式會拒絕建表。",
        "- report.json：還原度報告（每個欄位是精確擷取、量測、AI、生成還是你指定的，以及⑦驗證的分數）。" if has_report
        else "- （沒有 report.json：匯出時這份工作還沒處理完，或處理失敗。）",
        "",
        "怎麼用（在 Mortise 的原始碼目錄執行）",
        "--------------------------------------",
        "1. 解壓縮後，用目前的引擎重跑一次、打分數：",
        "     python -m mortise.eval run 解壓目錄/sample --out 評測結果",
        "2. 收進回饋庫，轉成標註資料，再建信心值校準表（資料夠多時才會建，不夠會說明還差多少）：",
        f"     python -m mortise.eval feedback-import {stem}{BUNDLE_SUFFIX} --store 回饋庫",
        "     python -m mortise.eval annotations --store 回饋庫 --out 標註.jsonl",
        "     python -m mortise.eval calibrate 標註.jsonl",
        "   （元素層級的校準：calibrate 標註.jsonl --field element）",
        "3. 你改過的文字可以變成文字辨識的訓練資料（只收你勾了同意的工作；確定是自己的資料時加 --include-unconsented）：",
        f"     python -m mortise.eval ocr-dataset --from-feedback {stem}{BUNDLE_SUFFIX} --out 訓練資料",
        "",
        "詳細說明見原始碼裡的 docs/training.md。",
        "",
    ]
    return "\n".join(lines)


def write_bundle(dest: Path, sample_file: Path, filename: str, fb: Feedback, report: Optional[dict[str, Any]] = None) -> Path:
    """寫出回饋包（zip，格式見開頭「三之一」）。"""
    import zipfile

    name = Path(filename).name or Path(sample_file).name
    stem = Path(name).stem
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", bundle_readme(name, fb, report is not None))
        if report is not None:
            z.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=1, default=str))
        z.write(sample_file, f"sample/{name}")
        z.writestr(f"sample/{stem}.feedback.json", json.dumps(fb.to_dict(), ensure_ascii=False, indent=1, default=str))
    return dest


@dataclass
class Bundle:
    feedback: Feedback
    sample_name: str
    sample_bytes: Optional[bytes]
    report: Optional[dict[str, Any]]


def read_bundle(path: Path) -> Bundle:
    """讀回饋包。只讀約定的幾個檔（不解壓整包、不信任 zip 裡的路徑）。"""
    import zipfile

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        fbs = [n for n in names if n.startswith("sample/") and n.endswith(".feedback.json") and n.count("/") == 1]
        if not fbs:
            raise ValueError("這不是 Mortise 的回饋包：找不到 sample/*.feedback.json")
        data = json.loads(z.read(fbs[0]).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("回饋檔的內容不是 JSON 物件")
        if data.get("schema") not in (None, SCHEMA):
            raise ValueError(f"回饋格式不是 {SCHEMA}：{data.get('schema')}")
        stem = fbs[0][len("sample/"):-len(".feedback.json")]
        samples = [n for n in names if n.startswith("sample/") and n.count("/") == 1 and not n.endswith(".feedback.json")
                   and Path(n).stem == stem]
        job = data.get("job") if isinstance(data.get("job"), dict) else {}
        sample_name = safe_filename(Path(samples[0]).name if samples else (job.get("filename") or stem))
        sample_bytes = z.read(samples[0]) if samples else None
        report = json.loads(z.read("report.json").decode("utf-8")) if "report.json" in names else None
    return Bundle(parse_feedback(data), sample_name, sample_bytes, report)


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
    n_ok = sum(1 for _, ok in data if ok)
    if min(n_ok, n - n_ok) < p["calib_min_each"]:
        which = "沒被修正（看過、確認是對的）" if n_ok < n - n_ok else "被修正（改掉或標錯）"
        return CalibrationTable(bins, n, False, f"選擇偏差：{n} 筆裡「{which}」的只有 {min(n_ok, n - n_ok)} 筆，兩種各至少需要 {p['calib_min_each']} 筆。"
                                "只看到被改掉的（或只看到對的），會把任何信心值都校準成 0（或 1）；要同時收「看過但沒改」的資料。", None, scope=scope)
    ece = sum(b.n * abs(b.mean_confidence - b.accuracy) for b in bins if b.n) / n
    ys = _pav([b.accuracy for b in valid], [b.n for b in valid])
    return CalibrationTable(bins, n, True, "", float(ece), [b.mean_confidence for b in valid], [float(y) for y in ys], scope)


def calibration_from_annotations(annotations: list[dict[str, Any]], stage: Optional[int] = None, field_name: Optional[str] = None,
                                 params: dict | None = None) -> CalibrationTable:
    rows = [r for r in annotations if r.get("system_confidence") is not None and not r.get("system_was_user") and (stage is None or r["stage"] == stage)
            and (field_name is None or r["field"] == field_name)]
    scope = "all" if stage is None and field_name is None else f"{stage if stage is not None else '*'}:{field_name or '*'}"
    return build_calibration([(r["system_confidence"], not r["corrected"]) for r in rows], params, scope)
