"""書寫系統先驗：繁體中文文件中，把辨識模型讀成的簡體字改回繁體字。

PP-OCR 的字典同時有繁簡字，「內」與「内」、「參」與「参」這類字形幾乎一樣，模型常讀成簡體。
做法：整頁的繁體專用字比簡體專用字多時，才把「只有一個繁體對應、或其繁體對應出現在候選字中」的簡體字改回繁體。
對照表取自 OpenCC（Apache-2.0，opencc-python-reimplemented 套件內附的 STCharacters.txt）；沒有安裝時不做任何事。
這是先驗推論，不是量測：改過的字會記下位置，信心值不提高。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, list[str]], set[str]]:
    try:
        import opencc  # opencc-python-reimplemented

        base = Path(opencc.__file__).parent / "dictionary"
    except Exception:
        return {}, set()
    st: dict[str, list[str]] = {}
    for line in (base / "STCharacters.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            st[parts[0]] = parts[1].split()
    trad_only: set[str] = set()
    ts_path = base / "TSCharacters.txt"
    if ts_path.exists():
        for line in ts_path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] not in parts[1].split():
                trad_only.add(parts[0])
    return st, trad_only


def available() -> bool:
    return bool(_tables()[0])


def simplified_only(ch: str) -> bool:
    st, _ = _tables()
    return ch in st and ch not in st[ch]


def page_is_traditional(texts: list[str], min_margin: int = 2) -> bool:
    """整頁繁體專用字比簡體專用字多（至少多 min_margin 個）。"""
    st, trad_only = _tables()
    if not st:
        return False
    t = sum(1 for s in texts for ch in s if ch in trad_only)
    s_ = sum(1 for s in texts for ch in s if simplified_only(ch))
    return t >= s_ + min_margin


def to_traditional(text: str, candidates: Optional[list[list[str]]] = None) -> tuple[str, list[int]]:
    """回傳（改過的文字、改過的位置）。candidates 是每個字的候選字（與 text 一一對應，可省略）。"""
    st, _ = _tables()
    if not st:
        return text, []
    out = list(text)
    changed = []
    for i, ch in enumerate(text):
        if not simplified_only(ch):
            continue
        options = st[ch]
        cands = candidates[i] if candidates and i < len(candidates) else []
        pick = next((o for o in options if o in cands), None)
        if pick is None and len(options) == 1:
            pick = options[0]
        if pick and pick != ch:
            out[i] = pick
            changed.append(i)
    return "".join(out), changed
