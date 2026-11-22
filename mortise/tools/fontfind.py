"""跨平台找字型：給字族名稱與粗體、斜體，找到字型檔。

為什麼要有這個模組：輸出器量字寬、⑦把成品畫出來打分數、驗證 SVG，都要找到實際的字型檔。
原本全部靠 fontconfig（fc-match、fc-list），那是 Linux 的工具；macOS 與 Windows 預設沒有，
結果在使用者自己的 Mac 上跑時「找不到任何可內嵌的字型」，⑦沒有分數、網頁也看不到成品預覽。

做法：
- 有 fontconfig 的機器（伺服器、Linux）照舊用 fc-match、fc-list，結果與以前完全相同。
- 沒有 fontconfig 的機器（macOS、Windows）直接掃描系統字型目錄，用 fontTools 讀字型檔的名稱表，
  自己比對字族、粗細、斜體；掃描結果快取在使用者的快取目錄，第二次起只重讀有變動的檔案。
- 找不到指定的字族時，依序退回：字寬相容的字型（例如 Arial、Liberation Sans、Helvetica 互通）、
  同類的常見字型（無襯線、襯線、等寬、中日韓），最後才是任何一個字型。回傳結果會標明是不是原本要的字族。

環境變數：
    MORTISE_FONT_DIRS       另外要掃描的字型目錄（用作業系統的路徑分隔字元分開），放在系統目錄前面
    MORTISE_NO_FONTCONFIG   設成 1 時不使用 fontconfig（測試用：模擬 macOS、Windows）
    MORTISE_CACHE_DIR       快取目錄（預設 ~/.cache/mortise，Windows 為 %LOCALAPPDATA%\\mortise）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")
_CACHE_VERSION = 1

# 字寬相容（量度相容）的字族：互相代替時行寬不變
METRIC_ALIASES: list[tuple[str, ...]] = [
    ("Arial", "Liberation Sans", "Arimo", "Helvetica", "Helvetica Neue"),
    ("Times New Roman", "Liberation Serif", "Tinos", "Times"),
    ("Courier New", "Liberation Mono", "Cousine", "Courier"),
    ("Calibri", "Carlito"),
    ("Cambria", "Caladea"),
]

# 同類的常見字型（依序找第一個有的）：Linux 伺服器、macOS、Windows 都列進來
GENERIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "sans": ("Liberation Sans", "Arial", "Helvetica", "Helvetica Neue", "DejaVu Sans", "Segoe UI", "Noto Sans", "Verdana"),
    "serif": ("Liberation Serif", "Times New Roman", "Times", "DejaVu Serif", "Georgia", "Noto Serif"),
    "mono": ("Liberation Mono", "Courier New", "Menlo", "DejaVu Sans Mono", "Consolas", "Courier"),
    "cjk_sans": ("Noto Sans CJK TC", "Noto Sans TC", "WenQuanYi Zen Hei", "PingFang TC", "Heiti TC", "STHeiti", "Hiragino Sans",
                 "Microsoft JhengHei", "Microsoft YaHei", "Arial Unicode MS"),
    "cjk_serif": ("Noto Serif CJK TC", "Noto Serif TC", "AR PL UMing TW", "Songti TC", "STSong", "PMingLiU", "MingLiU", "Arial Unicode MS"),
}
_GENERIC_NAMES = {
    "sans-serif": "sans", "sans": "sans", "serif": "serif", "monospace": "mono", "mono": "mono",
    "cjk-sans": "cjk_sans", "cjk_sans": "cjk_sans", "cjk-serif": "cjk_serif", "cjk_serif": "cjk_serif",
}
_CJK_WORDS = ("cjk", " tc", " sc", " jp", " kr", "hei", "song", "ming", "kai", "gothic", "pingfang", "hiragino", "yahei",
              "jhenghei", "wenquanyi", "黑", "宋", "明", "楷", "蘋方")
_SERIF_WORDS = ("serif", "times", "georgia", "garamond", "minion", "baskerville", "caslon", "bodoni", "cambria", "caladea", "mincho", "song", "ming")
_MONO_WORDS = ("mono", "courier", "code", "consolas", "menlo", "typewriter")


@dataclass(frozen=True)
class FontEntry:
    """一個字型（字型集合檔裡的每一個字型各算一個）。"""

    path: str
    index: int
    families: tuple[str, ...]  # 所有字族名稱：排版字族（名稱表第 16 號）、舊式字族（第 1 號），含各語言的本地化名稱
    style: str
    weight: int  # fontconfig 的刻度：80 一般、100 中等、180 半粗、200 粗
    slant: int  # 0 正體、100 斜體
    postscript: str = ""


@dataclass(frozen=True)
class Match:
    path: str
    index: int
    families: list[str]
    weight: int
    slant: int
    exact: bool  # 是否就是要找的字族（False 表示退回了別的字型）


def norm(name: str) -> str:
    """字族名稱比對用：忽略空白、底線、連字號與大小寫。"""
    return re.sub(r"[\s_\-]", "", name or "").casefold()


# ---------------------------------------------------------------- 字型目錄與快取


def system_font_dirs() -> list[str]:
    """這台電腦的字型目錄（只回傳存在的目錄）。MORTISE_FONT_DIRS 指定的放最前面。"""
    dirs: list[str] = [d for d in os.environ.get("MORTISE_FONT_DIRS", "").split(os.pathsep) if d]
    home = Path.home()
    if sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts", str(home / "Library/Fonts")]
    elif os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs += [os.path.join(windir, "Fonts")]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", str(home / ".fonts"), str(home / ".local/share/fonts")]
    seen, out = set(), []
    for d in dirs:
        d = os.path.expanduser(d)
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def cache_dir() -> Path:
    env = os.environ.get("MORTISE_CACHE_DIR")
    if env:
        return Path(env)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mortise"
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "mortise"


def fontconfig_available() -> bool:
    if os.environ.get("MORTISE_NO_FONTCONFIG") == "1":
        return False
    return shutil.which("fc-match") is not None and shutil.which("fc-list") is not None


# ---------------------------------------------------------------- 列舉字型


def _fc_weight(us_weight: int) -> int:
    """OpenType 的字重（100 到 900）換成 fontconfig 的刻度（一般 80、粗體 200）。"""
    table = [(100, 0), (200, 40), (300, 50), (350, 55), (400, 80), (500, 100), (600, 180), (700, 200), (800, 205), (900, 210)]
    if us_weight <= table[0][0]:
        return table[0][1]
    for (a, fa), (b, fb) in zip(table, table[1:]):
        if us_weight <= b:
            return round(fa + (fb - fa) * (us_weight - a) / (b - a))
    return table[-1][1]


def _read_names(font) -> tuple[list[str], str, str]:
    """字型的所有字族名稱、樣式名稱、PostScript 名稱。英文名稱排前面。"""
    names = font["name"]
    fam16, fam1, sub17, sub2, ps = [], [], "", "", ""
    for rec in names.names:
        try:
            s = rec.toUnicode().strip()
        except Exception:
            continue
        if not s:
            continue
        english = (rec.platformID == 3 and rec.langID == 0x409) or (rec.platformID == 1 and rec.langID == 0)
        if rec.nameID == 16:
            fam16.insert(0, s) if english else fam16.append(s)
        elif rec.nameID == 1:
            fam1.insert(0, s) if english else fam1.append(s)
        elif rec.nameID == 17 and (english or not sub17):
            sub17 = s
        elif rec.nameID == 2 and (english or not sub2):
            sub2 = s
        elif rec.nameID == 6 and (english or not ps):
            ps = s
    families: list[str] = []
    for f in fam16 + fam1:
        if f not in families:
            families.append(f)
    return families, (sub17 or sub2 or "Regular"), ps


def _scan_file(path: str) -> list[FontEntry]:
    import logging

    from fontTools.ttLib import TTCollection, TTFont

    logging.getLogger("fontTools").setLevel(logging.ERROR)  # 舊字型檔的建立日期欄位常是 0，fontTools 會印一堆無害的警告

    out: list[FontEntry] = []
    try:
        fonts = TTCollection(path, lazy=True).fonts if path.lower().endswith((".ttc", ".otc")) else [TTFont(path, lazy=True)]
    except Exception:
        return out
    for i, f in enumerate(fonts):
        try:
            families, style, ps = _read_names(f)
            if not families:
                continue
            os2 = f["OS/2"] if "OS/2" in f else None
            us_weight = int(os2.usWeightClass) if os2 is not None else (700 if "bold" in style.lower() else 400)
            italic = bool(os2 is not None and os2.fsSelection & 1) or bool(f["head"].macStyle & 2) or bool(re.search(r"italic|oblique", style, re.I))
            out.append(FontEntry(path=path, index=i, families=tuple(families), style=style, weight=_fc_weight(us_weight), slant=100 if italic else 0, postscript=ps))
        except Exception:
            continue
    return out


def _iter_font_files(dirs: Iterable[str]) -> list[str]:
    files = []
    for d in dirs:
        for root, _dirs, names in os.walk(d):
            for n in names:
                if n.lower().endswith(FONT_EXTENSIONS):
                    files.append(os.path.join(root, n))
    return sorted(set(files))


def _scan_dirs(dirs: list[str]) -> list[FontEntry]:
    """掃描字型目錄；每個檔案依（大小、修改時間）快取，沒變的不重讀。"""
    cache_path = cache_dir() / f"fontscan-v{_CACHE_VERSION}.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cached = {}
    fresh: dict[str, dict] = {}
    entries: list[FontEntry] = []
    for path in _iter_font_files(dirs):
        try:
            st = os.stat(path)
        except OSError:
            continue
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
        hit = cached.get(path)
        if hit and hit.get("stamp") == stamp:
            items = [FontEntry(path=path, index=e["index"], families=tuple(e["families"]), style=e["style"], weight=e["weight"], slant=e["slant"], postscript=e.get("postscript", "")) for e in hit["fonts"]]
        else:
            items = _scan_file(path)
        fresh[path] = {"stamp": stamp, "fonts": [{"index": e.index, "families": list(e.families), "style": e.style, "weight": e.weight, "slant": e.slant, "postscript": e.postscript} for e in items]}
        entries.extend(items)
    if fresh != cached:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache_path)
        except OSError:
            pass  # 快取寫不進去不影響結果
    return entries


def _fc_list() -> list[FontEntry]:
    try:
        out = subprocess.run(
            ["fc-list", "--format", "%{file}\t%{index}\t%{family}\t%{style[0]}\t%{weight}\t%{slant}\t%{postscriptname}\n"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    entries = []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) < 7 or not p[0].lower().endswith(FONT_EXTENSIONS):
            continue
        try:
            fams = tuple(f.strip().replace("\\-", "-") for f in p[2].split(",") if f.strip())
            entries.append(FontEntry(path=p[0], index=int(p[1] or 0), families=fams, style=p[3] or "Regular", weight=int(float(p[4] or 80)), slant=int(float(p[5] or 0)), postscript=p[6]))
        except ValueError:
            continue
    return entries


_LOCK = threading.Lock()
_FONTS: Optional[list[FontEntry]] = None
_FONTS_KEY: Optional[tuple] = None


def list_fonts() -> list[FontEntry]:
    """這台電腦上所有的字型。有 fontconfig 用 fc-list，否則掃描系統字型目錄。結果留在記憶體。"""
    global _FONTS, _FONTS_KEY
    key = (fontconfig_available(), os.environ.get("MORTISE_FONT_DIRS", ""))
    with _LOCK:
        if _FONTS is None or _FONTS_KEY != key:
            fonts = _fc_list() if key[0] else []
            extra = [d for d in os.environ.get("MORTISE_FONT_DIRS", "").split(os.pathsep) if d and os.path.isdir(d)]
            if not fonts:
                fonts = _scan_dirs(system_font_dirs())
            elif extra:
                fonts = _scan_dirs(extra) + fonts
            _FONTS, _FONTS_KEY = fonts, key
        return _FONTS


def reset_cache() -> None:
    """清掉記憶體裡的字型清單與比對結果（測試換環境變數時用）。"""
    global _FONTS, _FONTS_KEY
    with _LOCK:
        _FONTS, _FONTS_KEY = None, None
        _MATCH.clear()


def installed_families() -> set[str]:
    return {f for e in list_fonts() for f in e.families}


# ---------------------------------------------------------------- 比對


def category_of(family: str) -> str:
    """由字族名稱猜類別：sans、serif、mono、cjk_sans、cjk_serif。"""
    low = f" {(family or '').lower()} "
    if any(w in low for w in _CJK_WORDS) or any("\u3400" <= ch <= "\u9fff" for ch in family or ""):
        return "cjk_serif" if any(w in low for w in ("song", "ming", "serif", "宋", "明")) and "sans" not in low else "cjk_sans"
    if any(w in low for w in _MONO_WORDS):
        return "mono"
    if any(w in low for w in _SERIF_WORDS) and "sans" not in low:
        return "serif"
    return "sans"


def _style_cost(e: FontEntry, bold: bool, italic: bool) -> float:
    want = 200 if bold else 80
    return abs(e.weight - want) + (1000 if (e.slant > 0) != italic else 0)


def _best(entries: list[FontEntry], bold: bool, italic: bool) -> Optional[FontEntry]:
    if not entries:
        return None
    return min(entries, key=lambda e: (_style_cost(e, bold, italic), e.path, e.index))


def _by_family() -> dict[str, list[FontEntry]]:
    table: dict[str, list[FontEntry]] = {}
    for e in list_fonts():
        for f in e.families:
            table.setdefault(norm(f), []).append(e)
    return table


def _python_match(family: str, bold: bool, italic: bool) -> Optional[Match]:
    table = _by_family()
    key = norm(family)
    generic = _GENERIC_NAMES.get((family or "").strip().lower())
    if generic is None:
        hit = _best(table.get(key, []), bold, italic)
        if hit is not None:
            return Match(hit.path, hit.index, list(hit.families), hit.weight, hit.slant, exact=True)
        for group in METRIC_ALIASES:  # 字寬相容的字族
            if key in {norm(g) for g in group}:
                for alt in group:
                    hit = _best(table.get(norm(alt), []), bold, italic)
                    if hit is not None:
                        return Match(hit.path, hit.index, list(hit.families), hit.weight, hit.slant, exact=False)
        generic = category_of(family)
    for alt in GENERIC_FAMILIES[generic]:
        hit = _best(table.get(norm(alt), []), bold, italic)
        if hit is not None:
            return Match(hit.path, hit.index, list(hit.families), hit.weight, hit.slant, exact=False)
    hit = _best(list_fonts(), bold, italic)
    if hit is None:
        return None
    return Match(hit.path, hit.index, list(hit.families), hit.weight, hit.slant, exact=False)


def _fc_escape(name: str) -> str:
    return re.sub(r"([\\\-:,])", r"\\\1", name)


def _fontconfig_match(family: str, bold: bool, italic: bool) -> Optional[Match]:
    pattern = _fc_escape(family) + (":bold" if bold else "") + (":italic" if italic else "")
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}\t%{index}\t%{family}\t%{weight}\t%{slant}", pattern], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    p = out.strip().split("\t")
    if len(p) != 5 or not p[0]:
        return None
    fams = [f.strip().replace("\\-", "-") for f in p[2].split(",")]
    exact = norm(family) in {norm(f) for f in fams}
    return Match(p[0], int(p[1] or 0), fams, int(float(p[3] or 80)), int(float(p[4] or 0)), exact)


_MATCH: dict[tuple, Optional[Match]] = {}


def match(family: str, bold: bool = False, italic: bool = False) -> Optional[Match]:
    """找最接近的字型檔；沒有任何字型時回傳 None。family 也可以是通用名稱：sans-serif、serif、monospace、cjk-sans、cjk-serif。
    結果的 exact 表示是不是要找的字族（False 表示退回了字寬相容或同類的字型）。"""
    ck = (family, bool(bold), bool(italic), fontconfig_available(), os.environ.get("MORTISE_FONT_DIRS", ""))
    if ck not in _MATCH:
        m = None
        if fontconfig_available() and (family or "").strip().lower() not in ("cjk-sans", "cjk_sans", "cjk-serif", "cjk_serif"):
            m = _fontconfig_match(family, bold, italic)
            extra = os.environ.get("MORTISE_FONT_DIRS")
            if extra and (m is None or not m.exact):  # 另外指定的目錄裡可能有 fontconfig 不知道的字型
                alt = _python_match(family, bold, italic)
                if alt is not None and alt.exact:
                    m = alt
        if m is None:
            m = _python_match(family, bold, italic)
        _MATCH[ck] = m
    return _MATCH[ck]


def find_file(family: str, bold: bool = False, italic: bool = False) -> Optional[tuple[str, int]]:
    """只找這個字族本身（不退回別的字型）：回傳（路徑, 集合檔內的索引）或 None。"""
    m = match(family, bold, italic)
    return (m.path, m.index) if m is not None and m.exact else None
