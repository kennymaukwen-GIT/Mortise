"""文字辨識（OCR）的訓練資料：輸出 PaddleOCR 辨識模型（PP-OCR rec）的訓練格式。

    python -m mortise.eval ocr-dataset --out 目錄 [--from-fixtures] [--pdf 檔案或目錄 ...] [--from-feedback 回饋來源 ...]
                                       [--variants clean,screenshot,scan,photo] [--seed 0] [--max-pages 5] [--val 0.1]
                                       [--jobs-root 工作目錄根] [--include-unconsented]

這裡只產生資料，不訓練（訓練要 GPU，步驟見 docs/training.md）。

一、兩種來源
  (a) 原生 PDF 自我監督（source = "self_supervised"）：把頁面畫成影像，用 synth.degrade 加上截圖、掃描、手機翻拍的
      干擾（clean 表示不加），再用 PDF 文字層「確切的文字與每個字的位置」把每一行裁下來；標籤就是 PDF 裡的文字，不必人工標。
      解析度：clean、截圖、掃描畫成 150 dpi（與工作影像相同）；照片畫成 photo_dpi（300 dpi）再翻拍——真實手機照片裡的頁面
      解析度比 150 dpi 高很多，用 150 dpi 翻拍出來的字太小（行高中位數約 13 像素）又糊，人眼也讀不出來，只會是錯的標籤。
      干擾後行高低於 min_height_px（照片 min_height_px_photo）的行略過。
      每個字的位置取 PDFium 的寬鬆字框（字型的上緣到下緣，同一行高度一致），依換行符號與位置分行；
      旋轉的字（PDFium 的字元角度不是 0，例如直排的側標）整個略過，不會切成一字一行配上正立的標籤；
      太長的行在空白處（中文在字與字之間）切成不超過 max_label_chars 個字的段（PaddleOCR 辨識設定檔的 max_text_length：PP-OCRv3、PP-OCRv5 mobile、PP-OCRv6 small 的設定檔都是 25，2026-11-28 對照 GitHub 上的設定檔）。
      干擾的單應矩陣把行的四角對到干擾後的影像，以透視轉換裁成水平的長條（上下各留 pad_v、左右各留 pad_h 個行高）。
  (b) 使用者的修正（source = "user_correction"）：使用者改過的字，從那份工作的工作影像裁出那一行，標籤是使用者改的字。
      兩種修正都收：
        - ③文字行（element_id 含 "-t-"）：直接是那一行。
        - ⑤段落（網頁的文字編輯送的是段落編號）：用④的 line_ids 找回③的行。只有一行的段落直接用那一行；
          多行的段落把使用者改後的整段字和系統原本各行的字（依④的 joiners 接起來）做對齊（difflib），
          換行處左右兩邊都沒被改動、能明確切開時才收那一行；切不出來的行記進 skipped 並寫明原因。
          使用者在段落裡自己打了換行、而且行數相同時，直接依換行切。
      同一個欄位改了好幾次時只用最後一次。標籤超過 max_label_chars 個字的行切成幾段（split_long_line：依③量到的每個字的位置、
      只在沒改動的地方切；每段用目前的辨識模型重讀驗證），切不成的略過（訓練程式會把太長的標籤丟掉）。
      回饋來源可以是：
        - 回饋庫目錄（FeedbackStore 的根目錄；工作影像到 --jobs-root/<工作編號>/ 找）
        - 回饋資料庫的樣本目錄（jobs/_feedback/samples/：樣本檔旁有 .feedback.json）
        - 網頁匯出的回饋包（.mortise-feedback.zip）
        - 單一工作目錄（裡面有 job.json）
      工作目錄已經刪除（保留 30 天）但有原始檔時（回饋資料庫、回饋包），重新跑一次流水線找回工作影像與那一行
      （同一個引擎版本才找得到同一個編號；找不到就略過並記下原因）。
      只收「使用者同意用於改善辨識」的工作；回饋包裡沒有勾選同意的、或同意狀態不明的（自己做的回饋包），
      要加 --include-unconsented 才收（確定是你自己的資料時）。
      回饋包是不受信任的檔案：job_id、修正單 id 在 feedback.read_bundle 已檢查（只准英數、減號、底線），寫檔前也確認路徑在輸出目錄裡。

二、輸出（--out 目錄）
  images/<來源>/<編號>.png                  裁好的行影像
  rec_gt.txt、rec_gt_train.txt、rec_gt_val.txt   每行「相對路徑\\t標籤」（PaddleOCR SimpleDataSet 的格式；data_dir 設成這個目錄）
                                            依頁（或依工作）切訓練與驗證，同一頁的行不會分到兩邊；驗證集依「行數」逼近 --val 的比例
  dict.txt                                  目前辨識模型的字典（從 ONNX 的 metadata 讀出，一行一個字；微調時 character_dict_path 用它）
  out_of_dict.txt                           標籤裡出現、字典裡沒有的字（每行「字\\t次數\\tU+碼位」）：這些字現在的模型永遠認不出來
  manifest.json                             每一筆的來源、頁、干擾、是否經使用者同意、授權、切分；統計（含每種干擾的行高分佈）；
                                            略過的項目與原因

已知限制：
- 只有水平的行：直排、旋轉的文字（PDFium 的字元角度不是 0，或字框高大於寬很多）略過。
- 照片干擾是合成的（見 synth.py 的已知限制）；行高的分佈寫在 manifest 的 height_px，可以先看一下再決定要不要用。
- 自我監督的字型與版面只來自你給的 PDF；字型種類太少時，模型會偏向那幾種字型（要混入真實照片的修正資料）。
- 寬鬆字框依字型的上下緣，少數字型的上下緣很寬，裁出來的上下留白會偏多。
- PDF 文字層的文字不一定等於畫面（例如字型的對照表錯誤、隱藏文字）；畫面上看不到的字會變成錯誤標籤。
  自家 fixture 沒有這個問題；外來的 PDF 要先抽查幾張。
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import cv2
import numpy as np
from PIL import Image

from ..config import Config

SCHEMA = "mortise.ocr_dataset/1"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
FIXTURE_LICENSE = "專案自製的虛構測試資料（tests/fixtures/make_fixtures.py 產生）"
FIXTURE_LICENSE_OTHER = "專案自製的虛構測試資料（隨原始碼提交在 tests/fixtures，不是 make_fixtures.py 產生的；來源見版本紀錄）"
UNKNOWN_LICENSE = "使用者提供的 PDF：授權請自行確認（要可以拿來訓練）"
FEEDBACK_LICENSE = "使用者上傳並同意用於改善辨識的檔案"
FEEDBACK_LICENSE_UNCONSENTED = "使用者自己匯出的回饋包（上傳時沒有勾選同意；以 --include-unconsented 收入）"
FEEDBACK_LICENSE_UNKNOWN = "同意狀態不明的回饋（沒有工作資料，例如自己做的回饋包；以 --include-unconsented 收入，授權請自行確認）"


def fixture_license(path: Path) -> str:
    """tests/fixtures 裡的檔案的授權說明：make_fixtures.py 產生的（原始碼裡有它的檔名）與其他隨原始碼提交的分開寫。"""
    try:
        src = (Path(path).parent / "make_fixtures.py").read_text(encoding="utf-8")
    except OSError:
        src = ""
    return FIXTURE_LICENSE if Path(path).stem and f'"{Path(path).stem}.' in src else FIXTURE_LICENSE_OTHER

DEFAULT_PARAMS: dict[str, Any] = {
    "dpi": 150.0,  # 原生 PDF 畫成影像的解析度（dpi；與工作影像相同）：clean、截圖、掃描用
    "photo_dpi": 300.0,  # 照片干擾先畫成這個解析度再翻拍（dpi）：翻拍後頁面約 165 到 270 dpi，字才看得清楚
    "pad_v": 0.04,  # 裁切時上下各留幾個行高（寬鬆字框已含字型上下緣；行距緊的文件留多了會裁到上下行的字）
    "pad_h": 0.3,  # 左右各留幾個行高
    "min_height_px": 10,  # 裁出來的行高低於這個（像素）就略過：太小的字當訓練資料只會是雜訊
    "min_height_px_photo": 20,  # 照片干擾的行高下限（像素）：照片有模糊、雜訊、JPEG，比截圖更需要大一點的字
    "max_label_chars": 25,  # 一筆的標籤最多幾個字（PaddleOCR 辨識設定檔的 max_text_length 是 25；超過的行會被訓練程式丟掉）
    "split_verify_cer": 0.2,  # 使用者修正的長行切段後，用目前的辨識模型讀每一段，和系統原本讀到的那幾個字比，字錯率不超過這個才收（裁切位置量對了）
    "split_gap_em": 0.12,  # 切段時，每段的右界停在下一段第一個字的起點往左這麼多個行高（不裁進下一段的字）
    "max_aspect_vertical": 1.5,  # 字框高 ÷ 寬超過這個（而且不只一個字）視為直排或旋轉，略過
    "max_char_angle_deg": 1.0,  # 字元角度（PDFium）偏離 0 度超過這個就當成旋轉的字，整個略過（度）
    "line_overlap": 0.5,  # 同一行：字框與目前這一行的垂直重疊至少佔較矮者的這個比例
    "line_gap_em": 2.0,  # 同一行：與前一個字的水平間距不超過這麼多個行高（超過視為另一欄）
}


def _p(params: Optional[dict]) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


def _u01(*parts: Any) -> float:
    """穩定的 0 到 1 亂數（切分訓練與驗證用）。"""
    return zlib.crc32("|".join(str(x) for x in parts).encode("utf-8")) / 0xFFFFFFFF


# ====================================================================== 字典


def model_charset(cfg: Optional[Config] = None) -> tuple[list[str], str]:
    """目前辨識模型的字典（不含 CTC 的 blank）與模型檔名。字典放在 ONNX 的 metadata（character），與 recognition/ocr.py 相同。"""
    import onnxruntime as ort

    from ..engine import ocr_model_paths

    path = ocr_model_paths(cfg)["rec"]
    if path is None or not Path(path).exists():
        raise FileNotFoundError("找不到文字辨識模型檔（rapidocr 沒有安裝，設定裡也沒有指定 rec_model_path）")
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
    meta = sess.get_modelmeta().custom_metadata_map
    if "character" not in meta:
        raise RuntimeError("辨識模型的 metadata 沒有字典（character）")
    return meta["character"].splitlines(), Path(path).name


def out_of_dict(labels: Iterable[str], charset: Iterable[str]) -> list[dict[str, Any]]:
    """標籤裡出現、字典裡沒有的字（空白不算：PP-OCR 的空白是另外一類）。依次數由多到少。"""
    have = set(charset) | {" "}
    counts: dict[str, int] = {}
    for lab in labels:
        for ch in lab:
            if ch not in have:
                counts[ch] = counts.get(ch, 0) + 1
    return [{"char": ch, "count": n, "codepoint": f"U+{ord(ch):04X}", "name": unicodedata.name(ch, "")}
            for ch, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def clean_label(text: str) -> str:
    """標籤正規化：連字（ﬁ、ﬂ）拆開、定位字元與換行變空白、連續空白併成一個、去頭尾空白。
    不做整體 NFKC（會把全形標點變半形，中文的「（」要保留）。"""
    out = []
    for ch in text:
        if 0xFB00 <= ord(ch) <= 0xFB4F:  # 字母表達形式區（連字）
            out.append(unicodedata.normalize("NFKC", ch))
        elif ch in "\t\r\n   ":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf") and ch != " ":
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


# ====================================================================== 裁切


def crop_quad(img: np.ndarray, quad: np.ndarray, pad_v: float, pad_h: float) -> Optional[np.ndarray]:
    """把影像中的一行（四角：左上、右上、右下、左下，像素角落約定）以透視轉換裁成水平長條，上下左右依行高留白。"""
    q = np.asarray(quad, np.float64)
    w = 0.5 * (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3]))
    h = 0.5 * (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1]))
    if w < 1 or h < 1:
        return None
    ux = (q[1] - q[0] + q[2] - q[3]) / (2 * max(w, 1e-9))  # 行的方向
    uy = (q[3] - q[0] + q[2] - q[1]) / (2 * max(h, 1e-9))
    dx, dy = ux * pad_h * h, uy * pad_v * h
    src = np.array([q[0] - dx - dy, q[1] + dx - dy, q[2] + dx + dy, q[3] - dx + dy], np.float32) - 0.5  # OpenCV 用像素中心約定
    W = max(2, int(round(w + 2 * pad_h * h)))
    H = max(2, int(round(h + 2 * pad_v * h)))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# ====================================================================== (a) 原生 PDF


@dataclass
class PdfLine:
    text: str
    box: tuple[float, float, float, float]  # 影像像素 x0, y0, x1, y1（像素角落約定）
    xlim: tuple[float, float] = (-1e9, 1e9)  # 左右留白的界限：長行切段時不可越過與相鄰段之間的中點（免得裁進不在標籤裡的半個字）


def _char_angle(tp: Any, i: int) -> Optional[float]:
    """第 i 個字的角度（弧度，0 是正立；PDFium 的 FPDFText_GetCharAngle）。讀不到回 None。"""
    try:
        import pypdfium2.raw as raw

        a = float(raw.FPDFText_GetCharAngle(tp.raw, i))
    except Exception:  # noqa: BLE001
        return None
    return None if a < 0 else a


def pdf_lines(pdf_path: Path, page_index: int, dpi: float, params: Optional[dict] = None,
              stats: Optional[dict] = None) -> tuple[np.ndarray, list[PdfLine]]:
    """原生 PDF 的一頁 → （dpi 解析度的 RGB 影像、文字行）。依 PDFium 的字序與寬鬆字框分行，太長的行切段。
    旋轉的字（字元角度偏離 0 度超過 max_char_angle_deg）整個略過；stats 有給時記下略過幾個字（rotated_chars）。"""
    import pypdfium2 as pdfium

    p = _p(params)
    max_angle = np.radians(float(p["max_char_angle_deg"]))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        pg = doc[page_index]
        w_pt, h_pt = pg.get_size()
        k = dpi / 72.0
        img = np.ascontiguousarray(pg.render(scale=k).to_numpy()[..., :3])
        tp = pg.get_textpage()
        chars = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            a = _char_angle(tp, i)
            if a is not None and ch.strip() and min(a, 2 * np.pi - a) > max_angle:
                chars.append((ch, None))  # 旋轉的字：當成斷行並丟掉
                continue
            l, b, r, t = tp.get_charbox(i, loose=True)
            chars.append((ch, (l * k, (h_pt - t) * k, r * k, (h_pt - b) * k)))
    finally:
        doc.close()
    lines: list[list[tuple[str, tuple]]] = []
    cur: list[tuple[str, tuple]] = []
    n_rot = 0

    def flush():
        nonlocal cur
        if any(c.strip() for c, _ in cur):
            lines.append(cur)
        cur = []

    for ch, bx in chars:
        if bx is None:
            n_rot += 1
            flush()
            continue
        if ch in "\r\n":
            flush()
            continue
        x0, y0, x1, y1 = bx
        if cur and ch != " ":
            boxes = [b for c, b in cur if c != " "]
            if boxes:
                ly0, ly1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
                lh = max(ly1 - ly0, 1e-6)
                ov = min(y1, ly1) - max(y0, ly0)
                last_x1 = boxes[-1][2]
                if ov < p["line_overlap"] * min(lh, max(y1 - y0, 1e-6)) or x0 - last_x1 > p["line_gap_em"] * lh or x0 < boxes[-1][0] - 0.5 * lh:
                    flush()
        cur.append((ch, bx))
    flush()
    out: list[PdfLine] = []
    for ln in lines:
        segs = []
        for seg in _split(ln, int(p["max_label_chars"])):
            boxes = [b for c, b in seg if c.strip() and b[2] > b[0] and b[3] > b[1]]
            text = clean_label("".join(c for c, _ in seg))
            if text and boxes:
                segs.append(PdfLine(text, (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))))
        for i, sg in enumerate(segs):
            lo = 0.5 * (segs[i - 1].box[2] + sg.box[0]) if i > 0 else -1e9
            hi = 0.5 * (sg.box[2] + segs[i + 1].box[0]) if i + 1 < len(segs) else 1e9
            sg.xlim = (lo, hi)
        out += segs
    if stats is not None:
        stats["rotated_chars"] = stats.get("rotated_chars", 0) + n_rot
    return img, out


def _split(line: list[tuple[str, tuple]], max_chars: int) -> list[list[tuple[str, tuple]]]:
    """太長的行切段：優先在空白處切（英文不切斷單字）；沒有空白（中文）就在第 max_chars 個字切。"""
    segs, cur = [], []
    for item in line:
        cur.append(item)
        if len("".join(c for c, _ in cur).strip()) > max_chars:
            sp = max((i for i, (c, _) in enumerate(cur) if c == " "), default=-1)
            if sp > 0:
                segs.append(cur[:sp])
                cur = cur[sp + 1:]
            else:
                segs.append(cur[:-1])
                cur = cur[-1:]
    if cur:
        segs.append(cur)
    return [s for s in segs if "".join(c for c, _ in s).strip()]


def _apply_h(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    h = np.hstack([pts, np.ones((len(pts), 1))]) @ np.asarray(m, np.float64).T
    return h[:, :2] / h[:, 2:3]


@dataclass
class Item:
    image: str  # 相對於輸出目錄的路徑
    label: str
    source: str  # self_supervised、user_correction
    origin: dict[str, Any]
    consented: Optional[bool]
    license: str
    split_key: str
    split: str = "train"
    size: tuple[int, int] = (0, 0)


def self_supervised(pdfs: list[tuple[Path, str]], out_dir: Path, variants: list[str], seed: int, max_pages: int, params: Optional[dict] = None,
                    say: Callable[[str], None] = lambda s: None) -> tuple[list[Item], list[dict]]:
    """(a) 原生 PDF → 每頁每種干擾每一行一張圖。pdfs 是（路徑、授權說明）。
    clean、截圖、掃描用 dpi 畫頁面；照片用 photo_dpi（見 DEFAULT_PARAMS）。"""
    import pypdfium2 as pdfium

    from .runner import variant_seed
    from .synth import KINDS, degrade

    p = _p(params)
    for v in variants:
        if v != "clean" and v not in KINDS:
            raise ValueError(f"不認得的干擾種類：{v}（可用：clean、{'、'.join(KINDS)}）")
    items: list[Item] = []
    skipped: list[dict] = []
    for path, lic in pdfs:
        try:
            n_pages = len(pdfium.PdfDocument(str(path)))
        except Exception as e:  # noqa: BLE001
            skipped.append({"source": str(path.name), "reason": f"打不開：{type(e).__name__}: {e}"})
            continue
        for pi in range(min(n_pages, max_pages)):
            rendered: dict[float, tuple[np.ndarray, list[PdfLine]]] = {}
            stats: dict[str, int] = {}

            def page_at(dpi: float):
                if dpi not in rendered:
                    rendered[dpi] = pdf_lines(path, pi, dpi, p, stats if not rendered else None)
                return rendered[dpi]

            _, base_lines = page_at(float(p["dpi"]))
            if stats.get("rotated_chars"):
                skipped.append({"source": f"{path.name} 第 {pi + 1} 頁", "reason": f"旋轉的字 {stats['rotated_chars']} 個略過（直排或側放的字）"})
            if not base_lines:
                skipped.append({"source": f"{path.name} 第 {pi + 1} 頁", "reason": "沒有文字層（例如字轉成外框或整頁是圖）"})
                continue
            for v in variants:
                vseed = None
                dpi = float(p["photo_dpi"] if v == "photo" else p["dpi"])
                img, lines = page_at(dpi)
                if v == "clean":
                    dimg, mat = img, np.eye(3)
                else:
                    vseed = variant_seed(seed, path.name, pi, v)  # 與評測（runner）同一套種子規則
                    dimg, mat, _ = degrade(img, v, vseed, 1.0, {"source_dpi": dpi})
                min_h = float(p["min_height_px_photo"] if v == "photo" else p["min_height_px"])
                folder = out_dir / "images" / "self_supervised"
                folder.mkdir(parents=True, exist_ok=True)
                n_ok = n_small = 0
                for li, ln in enumerate(lines):
                    x0, y0, x1, y1 = ln.box
                    if (y1 - y0) > p["max_aspect_vertical"] * (x1 - x0) and len(ln.text) > 1:
                        continue
                    lh = y1 - y0  # 留白在原頁面座標加（左右不越過相鄰段的中點），再用干擾的矩陣對到干擾後的影像
                    px0, px1 = max(x0 - p["pad_h"] * lh, ln.xlim[0]), min(x1 + p["pad_h"] * lh, ln.xlim[1])
                    py0, py1 = y0 - p["pad_v"] * lh, y1 + p["pad_v"] * lh
                    quad = _apply_h(mat, np.array([[px0, py0], [px1, py0], [px1, py1], [px0, py1]], np.float64))
                    hpx = 0.5 * (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1]))
                    if hpx < min_h:
                        n_small += 1
                        continue
                    crop = crop_quad(dimg, quad, 0.0, 0.0)
                    if crop is None:
                        continue
                    name = f"{Path(path).stem}.p{pi + 1:03d}.{v}.l{li:04d}.png"
                    Image.fromarray(crop).save(folder / name)
                    items.append(Item(f"images/self_supervised/{name}", ln.text, "self_supervised",
                                      {"pdf": path.name, "page": pi + 1, "variant": v, "seed": vseed, "line": li, "dpi": dpi},
                                      None, lic, f"pdf|{path.name}|{pi}", size=(crop.shape[1], crop.shape[0])))
                    n_ok += 1
                if n_small:
                    skipped.append({"source": f"{path.name} 第 {pi + 1} 頁 {v}", "reason": f"{n_small} 行的行高低於 {min_h:.0f} 像素（太小，看不清楚）"})
                say(f"{path.name} 第 {pi + 1} 頁 {v}：{n_ok} 行")
    return items, skipped


# ====================================================================== (b) 使用者的修正


@dataclass
class _FeedbackJob:
    """一份有文字修正的工作：從哪裡來、修正、工作目錄或原始檔。"""

    label: str  # 來源說明（給 manifest 看）
    job_id: Optional[str]
    corrections: list[Any]  # Correction
    consented: Optional[bool]
    job_dir: Optional[Path] = None
    sample_path: Optional[Path] = None
    filename: str = ""
    snapshots: dict[str, dict] = field(default_factory=dict)  # 修正單 id → 系統當時的值（有記下的話）


def _is_line_id(eid: Optional[str]) -> bool:
    return bool(eid) and "-t-" in str(eid)


def _text_corrections(corrections: Iterable[Any]) -> list[Any]:
    """使用者對文字的修正：③文字行（-t-）與⑤段落或文字框（網頁的文字編輯）。同一個目標改了好幾次時只留最後一次。"""
    picked: dict[tuple, Any] = {}
    for c in corrections:
        if c.field != "text" or not c.element_id or getattr(c.source, "value", c.source) != "user" or not str(c.new_value or "").strip():
            continue
        if c.target_stage == 3 and not _is_line_id(c.element_id):
            continue
        if c.target_stage not in (3, 4, 5):
            continue
        key = (c.page_index or 0, c.element_id)
        picked.pop(key, None)
        picked[key] = c  # 後面的覆蓋前面的（使用者最後一次改的才是答案）
    return list(picked.values())


def _map_point(ops: list[tuple], b: int, side: Optional[str] = None) -> Optional[int]:
    """對齊結果（difflib 的 opcodes）裡，原字串的位置 b → 新字串的位置。b 落在被改動的區段中間時，
    無法確定那裡是哪一行的字，回 None。剛好有字插在 b 上時：side 是 "left" 表示插進來的字算在 b 的左邊
    （上一行的行尾，後面接著空白），"right" 表示算在右邊（下一行的行首）；沒給就是無法確定。"""
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" and i1 < b < i2:
            return None
        if tag == "insert" and i1 == b:
            return j2 if side == "left" else (j1 if side == "right" else None)
    for tag, i1, i2, j1, _j2 in ops:
        if tag == "equal" and i1 <= b <= i2:
            return j1 + (b - i1)
    for _tag, i1, i2, j1, j2 in ops:
        if i1 == b:
            return j1
        if i2 == b:
            return j2
    return None


def split_paragraph_text(new_text: str, line_texts: list[str], joiners: Optional[list[str]] = None) -> list[Optional[str]]:
    """使用者改過的整段字 → 每一行的字（切不出來的行是 None）。
    系統原本各行的字依 joiners（④的「行與行之間補的字元」）接起來，和使用者的字對齊；換行處（上一行最後、下一行開頭）
    沒被改動，而且兩行之間只有空白時，那一處才算切得開。使用者自己打了換行、而且行數相同時直接依換行切。"""
    n = len(line_texts)
    if n == 0:
        return []
    if n == 1:
        return [new_text]
    parts = new_text.split("\n")
    if len(parts) == n:
        return parts
    joiners = list(joiners or [])
    joiners += [""] * (n - 1 - len(joiners))
    full, starts, ends = "", [], []
    for k, t in enumerate(line_texts):
        if k:
            full += joiners[k - 1]
        starts.append(len(full))
        full += t
        ends.append(len(full))
    flat = new_text.replace("\n", " ")
    ops = difflib.SequenceMatcher(None, full, flat, autojunk=False).get_opcodes()
    cuts: list[Optional[tuple[int, int]]] = []  # 第 k 處換行：（上一行在新字串的終點、下一行的起點）
    for k in range(n - 1):
        # 兩行之間有補字元（英文的空白）時，插在行尾的字算上一行、插在下一行開頭的算下一行；中文（沒有補字元）的換行處插了字就切不開
        sides = ("left", "right") if joiners[k] else (None, None)
        a, b = _map_point(ops, ends[k], sides[0]), _map_point(ops, starts[k + 1], sides[1])
        cuts.append((a, b) if a is not None and b is not None and a <= b and not flat[a:b].strip() else None)
    out: list[Optional[str]] = []
    for k in range(n):
        lo = 0 if k == 0 else (cuts[k - 1][1] if cuts[k - 1] else None)
        hi = len(flat) if k == n - 1 else (cuts[k][0] if cuts[k] else None)
        out.append(flat[lo:hi] if lo is not None and hi is not None and lo <= hi else None)
    return out


def _collect_feedback(sources: list[Path], jobs_root: Optional[Path], tmp: Path) -> tuple[list[_FeedbackJob], list[dict]]:
    from ..artifacts import Correction
    from .feedback import FeedbackStore, read_bundle, safe_filename, safe_job_id
    from .runner import discover

    jobs: list[_FeedbackJob] = []
    skipped: list[dict] = []

    def job_dir_for(job_id: Optional[str]) -> Optional[Path]:
        if not job_id or jobs_root is None or not safe_job_id(job_id):  # 不像工作編號的不拿來組路徑
            return None
        d = Path(jobs_root) / job_id
        return d if (d / "job.json").exists() else None

    def job_id_of(sample_key: str) -> Optional[str]:
        head = Path(sample_key).name.split("__", 1)
        return head[0] if len(head) == 2 and safe_job_id(head[0]) else None

    for src in sources:
        src = Path(src)
        if src.is_file() and src.suffix.lower() == ".zip":
            try:
                b = read_bundle(src)
            except Exception as e:  # noqa: BLE001
                skipped.append({"source": src.name, "reason": f"讀不出回饋包：{e}"})
                continue
            cors = _text_corrections(b.feedback.corrections)
            if not cors:
                continue
            jid = b.feedback.job.get("job_id")  # read_bundle 已檢查過（不合格的已丟掉）
            sp = None
            if b.sample_bytes is not None:
                sp = tmp / "bundles" / hashlib.sha256(str(src.resolve()).encode("utf-8")).hexdigest()[:12] / safe_filename(b.sample_name)
                sp.parent.mkdir(parents=True, exist_ok=True)
                sp.write_bytes(b.sample_bytes)
            jobs.append(_FeedbackJob(src.name, jid, cors, b.feedback.job.get("consented") if b.feedback.job else None, job_dir_for(jid), sp,
                                     b.sample_name, b.feedback.snapshots))
        elif (src / "job.json").exists():  # 單一工作目錄
            state = json.loads((src / "job.json").read_text(encoding="utf-8"))
            cors = _text_corrections(Correction.model_validate(c) for c in state.get("corrections", []))
            if cors:
                from .feedback import correction_snapshots

                jid = src.name if safe_job_id(src.name) else None
                jobs.append(_FeedbackJob(src.name, jid, cors, bool(state.get("allow_training", False)), src, None, state.get("filename", ""),
                                         correction_snapshots(src / "feedback")))
        elif src.is_dir() and any(s.feedback_path for s in discover(src)):  # 樣本目錄（回饋資料庫的 samples/：自動收的都是同意過的）
            from .feedback import load_feedback

            for s in discover(src):
                if not s.feedback_path:
                    continue
                fb = load_feedback(s.feedback_path, s.key)
                cors = _text_corrections(fb.corrections)
                if cors:
                    jid = fb.job.get("job_id") or job_id_of(s.key)
                    # 回饋資料庫自動收的（檔名是 <工作編號>__<原檔名>）一定經過同意；其他的看回饋裡記的，沒記就是不明
                    consented = fb.job.get("consented", True if job_id_of(s.key) else None)
                    jobs.append(_FeedbackJob(s.key, jid, cors, consented, job_dir_for(jid), s.path, s.path.name.split("__", 1)[-1], fb.snapshots))
        elif src.is_dir():  # 回饋庫（FeedbackStore 的根目錄）：只有回饋，工作影像到 jobs_root 找
            store = FeedbackStore(src)
            for key in store.samples():
                for fb in store.get(key):
                    cors = _text_corrections(fb.corrections)
                    if not cors:
                        continue
                    jid = fb.job.get("job_id") or job_id_of(key)
                    jobs.append(_FeedbackJob(key, jid, cors, fb.job.get("consented", True if job_id_of(key) else None), job_dir_for(jid), None,
                                             Path(key).name.split("__", 1)[-1], fb.snapshots))
        else:
            skipped.append({"source": str(src), "reason": "不是回饋庫、樣本目錄、回饋包或工作目錄"})
    return jobs, skipped


def _artifacts_for(fj: _FeedbackJob, tmp: Path, cfg: Config):
    """那份工作的 RunArtifacts：先用工作目錄；沒有就用原始檔重新跑一次流水線（不套用任何修正）。"""
    from ..pipeline import Pipeline
    from .runner import load_artifacts, run_pipeline

    if fj.job_dir is not None:
        pipe = Pipeline(fj.job_dir, cfg)
        return load_artifacts(pipe), "工作目錄"
    if fj.sample_path is not None and fj.sample_path.exists():
        safe = hashlib.sha256(str(fj.label).encode("utf-8")).hexdigest()[:12]
        _, run = run_pipeline(tmp / "rerun" / safe, fj.sample_path, cfg, ["pdf"])
        return run, "重新跑流水線"
    return None, ""


def _targets(run: Any, page: int, c: Any) -> tuple[list[tuple[Any, str, bool]], list[str]]:
    """一筆文字修正 → [(③的行, 那一行的標籤, 是否被改動)] 與切不出來的原因。
    ③的行直接用；⑤段落（或只有一段的文字框）依④的 line_ids 找回③的行，多行時用 split_paragraph_text 切。"""
    from .runner import page_layout

    el = run.elements[page]
    by_id = {t.id: t for t in el.texts}
    new = str(c.new_value)
    if _is_line_id(c.element_id):
        line = by_id.get(c.element_id)
        return ([(line, new, line.text != new)] if line is not None else []), ([] if line is not None else ["找不到那一行"])
    lay = page_layout(run, page)
    if lay is None:
        return [], ["沒有④的版面，找不到段落裡的行"]
    paras = {p.id: p for p in lay.paragraphs}
    par = paras.get(c.element_id)
    if par is None:
        box = next((tb for tb in lay.text_boxes if tb.id == c.element_id), None)
        if box is not None and len(box.paragraph_ids) == 1:
            par = paras.get(box.paragraph_ids[0])
        elif box is not None:
            return [], [f"指名的是有 {len(box.paragraph_ids)} 段的文字框，不知道改的是哪一段"]
    if par is None:
        return [], ["找不到那一段（引擎版本不同時編號會變）"]
    lines = [by_id.get(lid) for lid in par.line_ids]
    if any(ln is None for ln in lines) or not lines:
        return [], ["段落裡的行找不齊"]
    parts = split_paragraph_text(new, [ln.text for ln in lines], list(par.joiners))
    out, why = [], []
    for k, (ln, part) in enumerate(zip(lines, parts)):
        if part is None:
            why.append(f"第 {k + 1} 行：改動落在換行處，切不出這一行的字")
            continue
        out.append((ln, part, clean_label(part) != clean_label(ln.text)))
    return out, why


def from_feedback(sources: list[Path], out_dir: Path, jobs_root: Optional[Path], cfg: Config, include_unconsented: bool = False,
                  params: Optional[dict] = None, say: Callable[[str], None] = lambda s: None) -> tuple[list[Item], list[dict]]:
    """(b) 使用者的文字修正（③的行、⑤的段落）→ 從工作影像裁出那一行，標籤用使用者改的字。"""
    from .feedback import inside, safe_id

    p = _p(params)
    items: list[Item] = []
    tmp = Path(tempfile.mkdtemp(prefix="mortise-ocrds-"))
    try:
        jobs, skipped = _collect_feedback(sources, jobs_root, tmp)
        seen: set[tuple[str, str]] = set()
        folder = out_dir / "images" / "user_correction"
        ocr_holder: dict[str, Any] = {}

        def engine_for_split(crop: np.ndarray) -> str:
            """長行切段的驗證：用目前設定的辨識模型讀一段（第一次用到才載入模型）。"""
            if "engine" not in ocr_holder:
                from ..recognition.ocr import OcrEngine

                ocr_holder["engine"] = OcrEngine(cfg.section("recognize").get("ocr_params"))
            # 四周補白，偵測器才找得到貼邊的字
            pad = max(8, crop.shape[0] // 2)
            canvas = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
            return " ".join(ln.text for ln in ocr_holder["engine"].read(canvas))
        for fj in jobs:
            if fj.consented is not True and not include_unconsented:
                why = "上傳時沒有勾選同意用於改善辨識" if fj.consented is False else "同意狀態不明（回饋裡沒有工作資料）"
                skipped.append({"source": fj.label, "reason": f"{why}（{len(fj.corrections)} 筆）；確定是你自己的資料、要拿來訓練時加 --include-unconsented"})
                continue
            try:
                run, how = _artifacts_for(fj, tmp, cfg)
            except Exception as e:  # noqa: BLE001
                skipped.append({"source": fj.label, "reason": f"找不回工作影像：{type(e).__name__}: {e}"})
                continue
            if run is None:
                skipped.append({"source": fj.label, "reason": "工作目錄已刪除（保留 30 天），回饋裡也沒有原始檔，找不回那一行的影像"})
                continue
            job_tag = fj.job_id if fj.job_id else "job-" + hashlib.sha256(fj.label.encode("utf-8")).hexdigest()[:8]
            for c in fj.corrections:
                key = (str(fj.job_id or fj.label), c.id)
                if key in seen:  # 同一筆修正從兩個來源進來（回饋庫與回饋包）：只收一次
                    continue
                page = c.page_index or 0
                if page >= len(run.specs):
                    skipped.append({"source": fj.label, "reason": f"第 {page + 1} 頁不存在", "correction": c.id})
                    continue
                targets, why = _targets(run, page, c)
                for w in why:
                    skipped.append({"source": fj.label, "reason": f"{w}（{how}）", "correction": c.id})
                if not targets:
                    continue
                spec = run.specs[page]
                img = np.asarray(Image.open(run.store.blob_path(spec.working_image)).convert("RGB"))
                W = float(spec.working_image_size[0])
                snap = fj.snapshots.get(c.id) or {}
                cid = c.id if safe_id(c.id) else "c-" + hashlib.sha256(str(c.id).encode("utf-8")).hexdigest()[:10]
                for line, part, changed in targets:
                    label = clean_label(part)
                    if not label:
                        continue
                    pieces: list[tuple[np.ndarray, str, str, str, float]] = []  # (裁好的圖, 標籤, 系統讀到的那幾個字, 檔名後綴, 段內是否改過)
                    if len(label) > int(p["max_label_chars"]):
                        segs, why_split = split_long_line(line, label, W, img, p, verify=engine_for_split)
                        if not segs:
                            skipped.append({"source": fj.label, "reason": f"標籤有 {len(label)} 個字，超過 {p['max_label_chars']} 個字（訓練程式會丟掉），"
                                            f"也切不成短段：{why_split}", "correction": c.id, "element_id": line.id})
                            continue
                        for k, (crop, sub_label, sub_sys) in enumerate(segs):
                            pieces.append((crop, sub_label, sub_sys, f".s{k + 1}", float(clean_label(sub_label) != clean_label(sub_sys))))
                        if why_split:
                            skipped.append({"source": fj.label, "reason": f"長行切段時有幾段沒收：{why_split}", "correction": c.id, "element_id": line.id})
                    else:
                        quad = np.array([[pt.x * W, pt.y * W] for pt in line.quad.points], np.float64)
                        crop = crop_quad(img, quad, p["pad_v"], p["pad_h"])
                        if crop is None:
                            continue
                        pieces.append((crop, label, line.text, "", float(changed)))
                    for crop, label, seg_sys, suffix, seg_changed in pieces:
                        self_name = (f"{job_tag}.{cid}" if _is_line_id(c.element_id) else f"{job_tag}.{cid}.{line.id if safe_id(line.id) else 'l'}") + suffix
                        _save_feedback_item(items, folder, self_name, crop, label, line, c, fj, page, how, snap, seg_sys if suffix else None,
                                            bool(seg_changed) if suffix else bool(changed))
                seen.add(key)
            say(f"{fj.label}：{sum(1 for it in items if it.origin.get('from') == fj.label)} 行（{how}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return items, skipped


def _save_feedback_item(items: list, folder: Path, base: str, crop: np.ndarray, label: str, line: Any, c: Any, fj: Any, page: int, how: str,
                        snap: dict, seg_sys: Optional[str], changed: bool) -> None:
    """存一張使用者修正的行圖（或長行切出來的一段，seg_sys 是系統在那一段讀到的字），記進 items。"""
    from .feedback import inside

    folder.mkdir(parents=True, exist_ok=True)
    name = base + ".png"
    Image.fromarray(crop).save(inside(folder, folder / name))
    # 系統當時讀到的字：③的行有快照就用快照；工作目錄裡的③可能已經套用過這筆修正（那時就是使用者的字，不算）。
    # ⑤段落的修正不會改③，③的行就是系統當時讀到的。長行切段時是系統在那一段讀到的字
    if seg_sys is not None:
        system_text = seg_sys
    elif _is_line_id(c.element_id):
        sv = snap.get("system_value")
        system_text = sv if isinstance(sv, str) else (line.text if line.text != str(c.new_value) else None)
    else:
        system_text = line.text
    origin = {"job_id": fj.job_id, "from": fj.label, "file": fj.filename, "page": page + 1, "element_id": line.id,
              "corrected_element": c.element_id, "correction_stage": c.target_stage, "line_changed": bool(changed),
              "correction_id": c.id, "system_text": system_text, "image_from": how}
    if seg_sys is not None:
        origin["segment_of_long_line"] = True
    items.append(Item(f"images/user_correction/{name}", label, "user_correction", origin,
                      fj.consented if fj.consented is not None else None,
                      {True: FEEDBACK_LICENSE, False: FEEDBACK_LICENSE_UNCONSENTED}.get(fj.consented, FEEDBACK_LICENSE_UNKNOWN),
                      f"job|{fj.job_id or fj.label}", size=(crop.shape[1], crop.shape[0])))


def _char_positions(line: Any) -> Optional[list[Optional[float]]]:
    """③的一行每個字的起點（從行的起點沿基線量的距離，相對單位；TextRun.char_x 只記不是空白的字時，空白記 None）。
    量不到（字數對不上）回 None。"""
    xs: list[Optional[float]] = []
    for r in line.runs:
        cx = list(r.char_x or [])
        if len(cx) == len(r.text):
            xs += [float(v) for v in cx]
        elif len(cx) == sum(1 for ch in r.text if not ch.isspace()):
            it = iter(cx)
            xs += [None if ch.isspace() else float(next(it)) for ch in r.text]
        else:
            return None
    return xs if len(xs) == len(line.text) else None


def split_long_line(line: Any, label: str, W: float, img: np.ndarray, params: Optional[dict] = None,
                    verify: Optional[Callable[[np.ndarray], str]] = None) -> tuple[list[tuple[np.ndarray, str, str]], str]:
    """使用者修正的一行太長（超過 max_label_chars 個字）時，切成幾段短的（中文常見：A4 一行 30 到 40 個字）。
    依據是量到的東西：③記下的每個字的起點（TextRun.char_x）決定在哪裡切；系統原本的字與使用者的字對齊（difflib），
    只在兩邊都沒改動的地方切（改動不會被切成兩半）；每段裁好後用目前的辨識模型（verify）讀一次，
    和系統原本在那一段讀到的字比，字錯率不超過 split_verify_cer 才收（證明裁切框住的就是那幾個字）。
    回傳 [(裁好的圖, 標籤, 系統在那一段讀到的字)] 與沒收的原因（白話，沒有就是空字串）。"""
    p = _p(params)
    max_chars = int(p["max_label_chars"])
    sys_text = line.text
    xs = _char_positions(line)
    if xs is None or not any(v is not None for v in xs):
        return [], "這一行沒有每個字的位置"
    n = len(sys_text)
    sm = difflib.SequenceMatcher(None, sys_text, label, autojunk=False)
    clean: dict[int, int] = {0: 0, n: len(label)}  # 系統字的切點 → 使用者字的切點（只在兩邊都沒改的地方）
    for a, b, size in sm.get_matching_blocks():
        for i in range(a + 1, a + size):
            clean[i] = b + (i - a)
    q = np.array([[pt.x, pt.y] for pt in line.quad.points], np.float64)  # 相對單位：左上、右上、右下、左下
    # char_x 是從行的起點沿基線量的距離（相對單位），換成沿行方向的比例 t（0 是行首、1 是行尾）
    left, right = 0.0, float(0.5 * (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])))
    h_rel = float(0.5 * (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])))
    if right - left <= 1e-9:
        return [], "這一行的位置量不到"

    def x_at(i: int) -> Optional[float]:
        for k in range(i, n):
            if xs[k] is not None:
                return xs[k]
        return None

    cuts, s0 = [0], 0
    while s0 < n:
        if len(clean_label(label[clean[s0]:])) <= max_chars and len(clean_label(sys_text[s0:])) <= max_chars:
            cuts.append(n)
            break
        cand = [e for e in clean if s0 < e < n and len(clean_label(sys_text[s0:e])) <= max_chars and len(clean_label(label[clean[s0]:clean[e]])) <= max_chars]
        if not cand:
            return [], "改動太長或沒有可以切的地方"
        best = max(cand)
        spaces = [e for e in cand if e >= best - 8 and (sys_text[e - 1].isspace() or sys_text[e].isspace())]  # 有空白就在空白處切（英文不切斷單字）
        e = max(spaces) if spaces else best
        cuts.append(e)
        s0 = e
    out, why = [], []
    for k in range(len(cuts) - 1):
        a, b = cuts[k], cuts[k + 1]
        sub_sys, sub_label = clean_label(sys_text[a:b]), clean_label(label[clean[a]:clean[b]])
        if not sub_label or not sub_sys:
            continue
        x0 = x_at(a)
        x1 = x_at(b) if b < n else None
        if x0 is None:
            why.append(f"第 {k + 1} 段量不到位置")
            continue
        t0 = (x0 - left) / (right - left) if a > 0 else 0.0
        t1 = ((x1 - p["split_gap_em"] * h_rel) - left) / (right - left) if x1 is not None else 1.0
        if a > 0:
            t0 = (x0 - p["split_gap_em"] * 0.5 * h_rel - left) / (right - left)
        t0, t1 = max(0.0, min(1.0, t0)), max(0.0, min(1.0, t1))
        if t1 - t0 < 1e-3:
            why.append(f"第 {k + 1} 段太窄")
            continue
        sub = np.array([q[0] + t0 * (q[1] - q[0]), q[0] + t1 * (q[1] - q[0]), q[3] + t1 * (q[2] - q[3]), q[3] + t0 * (q[2] - q[3])]) * W
        pad_h = p["pad_h"] if (a == 0 and b == n) else min(p["pad_h"], 0.5 * p["split_gap_em"])
        crop = crop_quad(img, sub, p["pad_v"], pad_h)
        if crop is None:
            why.append(f"第 {k + 1} 段裁不出來")
            continue
        if verify is not None:
            try:
                read = clean_label(verify(crop))
            except Exception as e:  # noqa: BLE001
                why.append(f"第 {k + 1} 段驗證失敗（{type(e).__name__}）")
                continue
            cer = _edit_distance(read.replace(" ", ""), sub_sys.replace(" ", "")) / max(1, len(sub_sys.replace(" ", "")))
            if cer > float(p["split_verify_cer"]):
                why.append(f"第 {k + 1} 段裁切驗證沒過（重讀的字錯率 {cer:.2f}）")
                continue
        out.append((crop, sub_label, sub_sys))
    return out, "；".join(why)


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


# ====================================================================== 把微調好的模型放回 Mortise


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f_bytes(field_no: int, b: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(b)) + b


def embed_dictionary(onnx_in: Path, dict_path: Path, onnx_out: Path) -> dict[str, Any]:
    """把字典寫進辨識模型 ONNX 的 metadata（鍵 character，一行一個字）：Mortise 的文字辨識從這裡讀字典
    （recognition/ocr.py；PaddleOCR 匯出、再用 paddle2onnx 轉出來的 ONNX 沒有這一項）。已有的 character 會被換掉。
    寫完用 onnxruntime 載入檢查：模型輸出的類別數要等於 字典字數 + 2（CTC 的 blank 與空白）。回傳檢查結果；對不上就丟 ValueError。
    只改最外層的 metadata_props（ModelProto 第 14 欄），不碰模型本體；不需要安裝 onnx 套件。"""
    import onnxruntime as ort

    data = Path(onnx_in).read_bytes()
    chars = [ln.rstrip("\r\n") for ln in Path(dict_path).read_text(encoding="utf-8").splitlines()]
    chars = [c for c in chars if c != ""]
    out = bytearray()
    i = 0
    while i < len(data):
        start = i
        tag, i = _read_varint(data, i)
        field_no, wire = tag >> 3, tag & 7
        if wire == 0:
            _, i = _read_varint(data, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            ln, i = _read_varint(data, i)
            body = data[i:i + ln]
            i += ln
            if field_no == 14:  # metadata_props：StringStringEntryProto（key = 1、value = 2）
                j, key = 0, None
                while j < len(body):
                    t, j = _read_varint(body, j)
                    if t & 7 != 2:
                        raise ValueError("ONNX 的 metadata 格式不對")
                    n, j = _read_varint(body, j)
                    if t >> 3 == 1:
                        key = body[j:j + n].decode("utf-8", "replace")
                    j += n
                if key == "character":
                    continue
        else:
            raise ValueError(f"不是 ONNX 模型檔（無法解析的欄位型別 {wire}）")
        out += data[start:i]
    entry = _f_bytes(1, b"character") + _f_bytes(2, "\n".join(chars).encode("utf-8"))
    out += _f_bytes(14, entry)
    Path(onnx_out).parent.mkdir(parents=True, exist_ok=True)
    Path(onnx_out).write_bytes(bytes(out))
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    sess = ort.InferenceSession(str(onnx_out), sess_options=so, providers=["CPUExecutionProvider"])
    meta = sess.get_modelmeta().custom_metadata_map.get("character", "").splitlines()
    n_out = sess.get_outputs()[0].shape[-1]
    info = {"dict_size": len(chars), "metadata_ok": meta == chars, "model_classes": n_out if isinstance(n_out, int) else None,
            "input": sess.get_inputs()[0].name, "input_shape": [d if isinstance(d, int) else str(d) for d in sess.get_inputs()[0].shape]}
    if not info["metadata_ok"]:
        raise ValueError("寫進去的字典讀不回來")
    if isinstance(n_out, int) and n_out != len(chars) + 2:
        Path(onnx_out).unlink(missing_ok=True)
        raise ValueError(f"字典有 {len(chars)} 個字，模型輸出 {n_out} 類（應該是字數 + 2）：字典和模型不是同一組，訓練時的 character_dict_path 要用同一個檔")
    return info


# ====================================================================== 組裝


def _assign_splits(items: list[Item], val_fraction: float) -> None:
    """依頁（或依工作）切訓練與驗證：同一頁的行不會分到兩邊。依穩定的抽籤順序把頁一頁一頁放進驗證集，
    直到驗證集的「行數」最接近 val_fraction（頁的行數差很多時，依頁抽籤的比例會偏很多）。至少留一頁在訓練集。"""
    keys = sorted({it.split_key for it in items})
    if not keys or val_fraction <= 0:
        return
    count: dict[str, int] = {}
    for it in items:
        count[it.split_key] = count.get(it.split_key, 0) + 1
    total = len(items)
    target = val_fraction * total
    order = sorted(keys, key=lambda k: (_u01("split", k), k))
    val: set[str] = set()
    n_val = 0
    for k in order:
        if len(val) >= len(keys) - 1:
            break
        if abs(n_val + count[k] - target) <= abs(n_val - target):  # 加進來比較接近目標才加（太大的頁跳過，試下一頁）
            val.add(k)
            n_val += count[k]
    if not val and len(keys) >= 2:  # 頁數少時至少留一頁當驗證（挑行數最少的，免得訓練集太少）
        k = min(order, key=lambda k: count[k])
        val.add(k)
    for it in items:
        it.split = "val" if it.split_key in val else "train"


def _height_stats(items: list[Item]) -> dict[str, dict[str, Any]]:
    """每種干擾（使用者修正另成一組）的行高分佈（像素）。"""
    groups: dict[str, list[int]] = {}
    for it in items:
        groups.setdefault(it.origin.get("variant") or it.source, []).append(int(it.size[1]))
    out = {}
    for g, hs in sorted(groups.items()):
        a = np.asarray(hs, np.float64)
        out[g] = {"n": len(hs), "min": int(a.min()), "p25": float(np.percentile(a, 25)), "median": float(np.median(a)),
                  "p75": float(np.percentile(a, 75)), "max": int(a.max()), "below_16px": float(np.mean(a < 16)), "below_24px": float(np.mean(a < 24))}
    return out


def build_dataset(out_dir: Path, from_fixtures: bool = False, pdfs: Iterable[Path] = (), feedback: Iterable[Path] = (), jobs_root: Optional[Path] = None,
                  variants: Optional[list[str]] = None, seed: int = 0, max_pages: int = 5, val_fraction: float = 0.1, include_unconsented: bool = False,
                  cfg: Optional[Config] = None, fixtures: Optional[Path] = None, params: Optional[dict] = None,
                  say: Callable[[str], None] = lambda s: None) -> dict[str, Any]:
    """產生訓練資料（見開頭說明）。回傳摘要（也寫在 manifest.json）。"""
    from .. import engine

    cfg = cfg or Config.load()
    out_dir = Path(out_dir)
    if (out_dir / "images").exists():  # 重產時清掉舊的圖（標籤檔會整個重寫，舊圖留著會對不上）
        shutil.rmtree(out_dir / "images")
    out_dir.mkdir(parents=True, exist_ok=True)
    variants = variants or ["clean", "screenshot", "scan", "photo"]
    sources: list[tuple[Path, str]] = []
    if from_fixtures:
        fx = Path(fixtures) if fixtures else FIXTURES
        sources += [(f, fixture_license(f)) for f in sorted(fx.glob("*.pdf"))]
    for p in pdfs:
        p = Path(p)
        sources += [(f, UNKNOWN_LICENSE) for f in (sorted(p.rglob("*.pdf")) if p.is_dir() else [p])]
    items, skipped = self_supervised(sources, out_dir, variants, seed, max_pages, params, say) if sources else ([], [])
    fb_sources = list(feedback)
    if fb_sources:
        jr = Path(jobs_root) if jobs_root else Path(os.environ.get("MORTISE_JOBS", "jobs"))
        more, sk = from_feedback(fb_sources, out_dir, jr, cfg, include_unconsented, params, say)
        items += more
        skipped += sk
    _assign_splits(items, val_fraction)
    for name, sel in (("rec_gt.txt", lambda it: True), ("rec_gt_train.txt", lambda it: it.split == "train"), ("rec_gt_val.txt", lambda it: it.split == "val")):
        (out_dir / name).write_text("".join(f"{it.image}\t{it.label}\n" for it in items if sel(it)), encoding="utf-8")
    try:
        charset, model = model_charset(cfg)
    except Exception as e:  # noqa: BLE001 —— 沒有模型時照樣輸出資料，只是不能列字典外的字
        charset, model = None, f"（讀不到模型字典：{e}）"
    ood = out_of_dict((it.label for it in items), charset) if charset is not None else []
    if charset is not None:
        (out_dir / "dict.txt").write_text("".join(ch + "\n" for ch in charset), encoding="utf-8")
    (out_dir / "out_of_dict.txt").write_text("".join(f"{o['char']}\t{o['count']}\t{o['codepoint']}\n" for o in ood), encoding="utf-8")
    by_source: dict[str, int] = {}
    for it in items:
        by_source[it.source] = by_source.get(it.source, 0) + 1
    eng = engine.describe(cfg)
    summary = {
        "schema": SCHEMA, "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "engine": {"version": eng["version"], "name": eng["name"]}, "rec_model": model, "dict_size": len(charset) if charset is not None else None,
        "format": "PaddleOCR 辨識（SimpleDataSet）：rec_gt*.txt 每行「相對路徑\\t標籤」；data_dir 設成這個目錄",
        "variants": variants, "seed": int(seed), "params": _p(params),
        "n_lines": len(items), "n_train": sum(it.split == "train" for it in items), "n_val": sum(it.split == "val" for it in items),
        "val_fraction_requested": float(val_fraction), "val_fraction_actual": (sum(it.split == "val" for it in items) / len(items)) if items else 0.0,
        "by_source": by_source, "n_chars": sum(len(it.label) for it in items), "height_px": _height_stats(items),
        "out_of_dict": ood, "skipped": skipped,
        "items": [{"image": it.image, "label": it.label, "source": it.source, "split": it.split, "consented": it.consented,
                   "license": it.license, "width": it.size[0], "height": it.size[1], **it.origin} for it in items],
    }
    (out_dir / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary
