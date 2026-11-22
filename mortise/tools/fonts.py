"""字型名稱處理：清掉子集前綴、拆出粗細與斜體、判斷是否為中日韓文字。"""

from __future__ import annotations

import re

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_STYLE_WORDS = [
    ("BoldItalic", True, True), ("BoldOblique", True, True), ("Bold-Italic", True, True),
    ("Bold", True, False), ("Italic", False, True), ("Oblique", False, True),
    ("Semibold", True, False), ("SemiBold", True, False), ("Demi", True, False), ("Heavy", True, False), ("Black", True, False),
    ("Regular", False, False), ("Roman", False, False), ("Medium", False, False), ("Light", False, False), ("Book", False, False),
]
_SUFFIXES = ("MT", "PSMT", "PS")


def clean_font_name(raw: str) -> str:
    """去掉子集字型的亂碼前綴（例如 ABCDEF+Calibri → Calibri）。"""
    return _SUBSET.sub("", raw.strip())


def split_style(name: str) -> tuple[str, bool, bool]:
    """把 PostScript 字型名稱拆成（字族、是否粗體、是否斜體）。

    例如 LiberationSans-Bold → ("Liberation Sans", True, False)。
    """
    base = clean_font_name(name)
    bold = italic = False
    family = base
    if "," in base:  # 例如 "Arial,Bold"
        family, style = base.split(",", 1)
    elif "-" in base:
        family, style = base.rsplit("-", 1)
    else:
        style = ""
        for word, b, i in _STYLE_WORDS:
            if base.endswith(word) and len(base) > len(word):
                family, style = base[: -len(word)], word
                break
    matched = False
    for word, b, i in _STYLE_WORDS:
        if style.replace(" ", "").lower() == word.lower():
            bold, italic, matched = b, i, True
            break
    if style and not matched:
        low = style.lower()
        if "bold" in low or "italic" in low or "oblique" in low:
            bold, italic = "bold" in low, ("italic" in low or "oblique" in low)
        else:
            family = base  # 連字號後面不是樣式字，整串當字族名
    for suf in _SUFFIXES:
        if family.endswith(suf) and len(family) > len(suf) + 2:
            family = family[: -len(suf)]
            break
    family = family.rstrip("-, ")
    # 駝峰拆字：LiberationSans → Liberation Sans；全大寫或含空白的不動
    if " " not in family and not family.isupper():
        family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family)
    return family.strip(), bold, italic


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF  # 部首、標點、假名、漢字
        or 0xAC00 <= o <= 0xD7AF  # 韓文
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE4F
        or 0xFF00 <= o <= 0xFFEF  # 全形符號
        or 0x20000 <= o <= 0x2FA1F
    )


def has_cjk(text: str) -> bool:
    return any(is_cjk(c) for c in text)


_INSTALLED: set[str] | None = None


def installed_families() -> set[str]:
    """這台機器上裝了哪些字族（mortise.tools.fontfind：有 fontconfig 用它查，沒有就掃描系統字型目錄；查不到就回傳空集合）。"""
    global _INSTALLED
    if _INSTALLED is None:
        try:
            from .fontfind import installed_families as _families

            _INSTALLED = _families()
        except Exception:
            _INSTALLED = set()
    return _INSTALLED


def verification_font(family: str, substitutes: dict[str, str], east_asian: bool) -> tuple[str, bool]:
    """決定驗證時實際用哪個字型。回傳（字型、是否為替代）。"""
    have = installed_families()
    if family in have:
        return family, False
    sub = substitutes.get(family)
    if sub and (sub in have or not have):
        return sub, True
    fallbacks = ["Noto Sans TC", "Noto Sans CJK TC", "WenQuanYi Zen Hei"] if east_asian else ["Liberation Sans", "DejaVu Sans"]
    for name in fallbacks:
        if name in have:
            return name, True
    return sub or family, True
