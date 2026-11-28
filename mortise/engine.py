"""引擎版本：回答「這一版可以叫做一個模型嗎？」——可以，而且它有一個固定的名字。

在 Mortise 裡，「模型」不是單一個神經網路檔，而是整條流水線這一刻的樣子：
  規則（各階段的程式與版本號）＋門檻（設定檔 default.toml）＋模型檔（文字辨識 PP-OCR、攤平 UVDoc）。
這幾樣任何一樣變了，輸出就可能不同，所以引擎版本由它們一起組成：

    engine_version()   → "mortise-0.1.0+s1v3.s2v4.s3v3.s4v3.s5v6.s6v4.s7v6-cfg1a2b3c4-ocr9f8e7d-uv7e90-src5a6b7c8"
    short_name()       → "樟雀-07"（好記的短名，由版本字串的雜湊決定；同一個版本永遠是同一個短名）
    components()       → 每個組成部分的明細（給成績單與 GET /api/engine）

組成（依字串中的順序）：
  mortise-0.1.0      套件版本（mortise.__version__）
  s1v3…s7v6          七個階段各自的 VERSION（階段的作者改了演算法要遞增它；快取鍵也用它）
  cfg…               設定的內容雜湊（default.toml 加上 --config 覆寫檔之後的結果，所以調參的覆寫檔也算新版本）
  ocr…               文字辨識模型檔（偵測、辨識、方向分類三個 ONNX）的內容雜湊；換上微調過的模型就會變
  uv…                攤平用的 UVDoc 模型檔雜湊；快取裡沒有就是 uvnone，設定成不用（dewarp_uvdoc = "off"）是 uvoff
  src…               流水線程式碼（mortise/ 底下除了 eval、api 以外的 .py）的內容雜湊：有人改了程式卻忘了遞增 VERSION，版本照樣會變

不算在版本裡的（已知限制）：字型檔（使用者快取裡下載過哪些字型會影響字型比對，但不會改變規則或模型）、
LibreOffice 的版本（只影響⑦驗證的替身渲染，另記在 components 的 verify_engine）、大型 AI 服務端的模型（設定裡的模型名稱
有算進 cfg，但同一個名稱背後的服務端更新看不到）。

雜湊都是內容雜湊（SHA-256 取前幾碼）：同樣的檔案在任何機器上都得到同樣的版本字串。模型檔很大（約 30 MB），
算過的雜湊依（路徑、大小、修改時間）記在記憶體裡，同一個行程裡不會重算。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import threading
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .config import Config

STAGES = ("s1_input", "s2_page", "s3_recognize", "s4_structure", "s5_settings", "s6_export", "s7_verify")
# 不影響輸出的程式（評測、API、命令列）不算進 src 雜湊：改評測工具不會讓引擎變成「新模型」
_SRC_EXCLUDE = ("eval", "api")
_SRC_EXCLUDE_FILES = ("cli.py", "engine.py")

# 好記的短名：木材（榫卯的材料）× 鳥獸，再加兩位數字（16 × 16 × 100 = 25600 種；完整版本字串才是唯一的名字）
_WOODS = "樟檜楠櫸柚桐松柏杉檀榆楓梧樺栗橡"
_ANIMALS = "雀鷺鶴燕鹿狐貂獺鯨隼鷹龜兔鯉蝶蛙"

_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_LOCK = threading.Lock()


def _sha_file(path: Path) -> Optional[str]:
    """檔案內容的 SHA-256（完整 64 碼）；檔案不存在回 None。依（路徑、大小、修改時間）快取。"""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    with _LOCK:
        hit = _HASH_CACHE.get(key)
    if hit:
        return hit
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with _LOCK:
        _HASH_CACHE[key] = digest
    return digest


def _digest(obj: Any, n: int) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:n]


def stage_versions() -> dict[str, str]:
    """各階段的 VERSION（鍵為 s1 到 s7）。"""
    out = {}
    for name in STAGES:
        mod = importlib.import_module(f".stages.{name}", __package__)
        out[name.split("_")[0]] = str(getattr(mod, "VERSION", "?"))
    return out


def ocr_model_paths(cfg: Optional[Config] = None) -> dict[str, Optional[Path]]:
    """目前會載入的文字辨識模型檔路徑（與 recognition/ocr.py 的 OcrEngine 用同一套規則）：
    設定 [recognize] ocr_params 的 det_model_path、rec_model_path、cls_model_path；沒有設定就用 rapidocr 內附的 PP-OCR。"""
    cfg = cfg or Config.load()
    params = dict(cfg.section("recognize").get("ocr_params") or {})
    try:
        from .recognition.ocr import _model_dir

        mdir: Optional[Path] = _model_dir()
    except Exception:  # noqa: BLE001 —— 沒有裝 rapidocr：只回報設定裡指定的路徑
        mdir = None
    defaults = {"det": "PP-OCRv6_det_small.onnx", "rec": "PP-OCRv6_rec_small.onnx", "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}
    out: dict[str, Optional[Path]] = {}
    for k, fname in defaults.items():
        given = params.get(f"{k}_model_path")
        out[k] = Path(given).expanduser() if given else (mdir / fname if mdir else None)
    return out


def _ocr_component(cfg: Config) -> dict[str, Any]:
    files = {}
    for k, path in ocr_model_paths(cfg).items():
        sha = _sha_file(path) if path is not None else None
        bundled = bool(path is not None and "rapidocr" in path.parts)
        # 內附模型只記檔名（路徑因機器而異）；自己換上的模型記完整路徑，方便查是哪一個檔
        files[k] = {"file": path.name if path else None, "path": None if bundled or path is None else str(path), "sha256": sha, "bundled": bundled}
    return {"files": files, "hash": _digest({k: v["sha256"] for k, v in files.items()}, 8)}


def _uvdoc_component(cfg: Config) -> dict[str, Any]:
    """UVDoc（攤平用的學習式外框模型）。只看快取，不下載。"""
    mode = str(cfg.section("page").get("dewarp_uvdoc", "auto"))
    if mode == "off" or not cfg.section("page").get("dewarp_photos", True):
        return {"mode": mode, "sha256": None, "tag": "off"}
    try:
        from .recognition.dewarp import uvdoc_model_dir

        d = uvdoc_model_dir()
    except Exception:  # noqa: BLE001
        return {"mode": mode, "sha256": None, "tag": "none"}
    meta = d / "uvdoc2d.json"
    onnx = sorted(d.glob("uvdoc2d-*.onnx")) if d.exists() else []
    sha = None
    if onnx and meta.exists():
        try:
            sha = json.loads(meta.read_text(encoding="utf-8")).get("onnx_sha256")  # 下載時驗證過；這裡不重算 32 MB 的雜湊
        except (OSError, ValueError):
            sha = None
        if not sha:
            sha = _sha_file(onnx[-1])
    return {"mode": mode, "sha256": sha, "file": onnx[-1].name if onnx else None, "tag": sha[:4] if sha else "none"}


def _src_component() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    files = sorted(p for p in root.rglob("*.py") if p.relative_to(root).parts[0] not in _SRC_EXCLUDE and p.name not in _SRC_EXCLUDE_FILES
                   and "__pycache__" not in p.parts)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update((_sha_file(p) or "").encode("ascii") + b"\0")
    return {"files": len(files), "hash": h.hexdigest()[:8]}


def components(cfg: Optional[Config] = None) -> dict[str, Any]:
    """引擎版本的每個組成部分（可寫成 JSON）。"""
    cfg = cfg or Config.load()
    comp: dict[str, Any] = {
        "package": __version__,
        "stages": stage_versions(),
        "config": {"hash": _digest(cfg.data, 8), "sections": sorted(cfg.data.keys())},
        "ocr": _ocr_component(cfg),
        "uvdoc": _uvdoc_component(cfg),
        "source": _src_component(),
    }
    comp["verify_engine"] = _verify_engine()  # 不算進版本（見開頭說明）
    return comp


_VERIFY_ENGINE: dict[str, Optional[str]] = {}


def _verify_engine() -> Optional[str]:
    """⑦驗證用的 LibreOffice 版本（要啟動一次 soffice，約 1 秒，所以同一個行程只查一次）。"""
    if "v" not in _VERIFY_ENGINE:
        try:
            from .tools import libreoffice

            _VERIFY_ENGINE["v"] = libreoffice.version() if libreoffice.available() else None
        except Exception:  # noqa: BLE001
            _VERIFY_ENGINE["v"] = None
    return _VERIFY_ENGINE["v"]


def version_from_components(comp: dict[str, Any]) -> str:
    st = ".".join(f"{k}v{v}" for k, v in sorted(comp["stages"].items()))
    return f"mortise-{comp['package']}+{st}-cfg{comp['config']['hash']}-ocr{comp['ocr']['hash'][:6]}-uv{comp['uvdoc']['tag']}-src{comp['source']['hash']}"


def engine_version(cfg: Optional[Config] = None) -> str:
    """穩定的引擎版本字串（只含英數、點、加號、減號，可以直接當檔名）。"""
    return version_from_components(components(cfg))


def short_name(version: Optional[str] = None, cfg: Optional[Config] = None) -> str:
    """好記的短名（例如「樟雀-07」）。同一個版本字串永遠得到同一個短名；但短名只有 25600 種，不同版本會撞名
    （累積 100 個版本時，至少有兩個撞名的機率約 18%；300 個版本約 83%），所以短名只是方便口頭稱呼，比較、存檔一律以完整版本字串為準。"""
    v = version or engine_version(cfg)
    h = int(hashlib.sha256(v.encode("utf-8")).hexdigest(), 16)
    return f"{_WOODS[h % 16]}{_ANIMALS[(h >> 4) % 16]}-{(h >> 8) % 100:02d}"


def describe(cfg: Optional[Config] = None) -> dict[str, Any]:
    """版本、短名與組成（GET /api/engine 與成績單共用）。"""
    comp = components(cfg)
    v = version_from_components(comp)
    return {"version": v, "name": short_name(v), "components": comp}


if __name__ == "__main__":  # python -m mortise.engine：印出目前的引擎版本
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
