"""Word 輸出在別的裝置上不跑版：兩欄履歷（假資料）的測試。

背景：一份「左邊灰底側欄＋右邊主欄」的履歷，可重排版原本用錨在段落上的浮動文字方塊排並排的區塊，
在 iPhone 上打開時方塊錯位、文字互相重疊、1 頁變 3 頁，字型也全被換成襯線體。這裡驗證：
  1. 可重排：並排的區塊（標題列的左右兩塊、側欄＋主欄、經歷標題＋右邊的日期）是看不見框線的表格，沒有浮動方塊；
     側欄底色是儲存格底色；同一行的日期接在行尾（定位點）；文字完整、順序正確；表格不會被拆到看不見的地方。
  2. 字型：寫進檔案的是多數裝置都有的字型名稱；冷門字型（Loma、FreeSans、Inter Display）不寫入；夾在內文裡的等寬字改掉；
     word/fontTable.xml 每個字型都有 family、pitch、panose1、altName。
  3. 版面固定：單行框留足寬度（換成最寬的常見字型也不折行）；貼著右側紙邊的單行框改成靠右、往左長。
  4. LibreOffice 轉 PDF：正常字型、以及只剩 DejaVu 一套字型（模擬別台裝置沒有這些字型）兩種情況，文字行都不互相重疊，
     版面固定版維持 1 頁、不超出紙邊。
人名、經歷都是虛構的。
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document
from lxml import etree

from mortise.artifacts import (
    Align, Box, Color, FontChoice, Intent, LineSpacing, LineSpacingMode, StyledDoc, StyledPage, StyledParagraph, StyledRun,
    StyledShape, StyledTextBox, Style, TargetMode,
)
from mortise.artifacts.common import Role, exact
from mortise.config import Config
from mortise.exporters import docx_common, docx_fixed, docx_flow
from mortise.exporters.docx_common import extract_texts, font_category, norm_text, q
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from mortise.tools import libreoffice
from mortise.tools.pdfium import PageReader

FIXTURES = Path(__file__).parents[1] / "fixtures"
W = "test@1"
DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
needs_lo = pytest.mark.skipif(not libreoffice.available(), reason="沒有 LibreOffice")
needs_dejavu = pytest.mark.skipif(not DEJAVU_DIR.is_dir(), reason="沒有 DejaVu 字型目錄，無法模擬精簡字型")
ACCENT = (47, 93, 115)
PAGE_W, PAGE_H = 595.276, 841.89


# ---------- 假資料的兩欄履歷（成品文件） ----------


def run(text, fam="Inter Display", size=9.5, bold=False, color=(34, 34, 34), verify=None) -> StyledRun:
    font = FontChoice(family=exact(fam, W), verification_family=verify or fam, is_substituted=False)
    return StyledRun(text=text, font=font, size_pt=exact(size, W), bold=bold, color=Color(r=color[0], g=color[1], b=color[2]))


def para(pid, runs, align=Align.LEFT, lines=1, pitch=None, after=0.0, style="S1", role=Role.BODY) -> StyledParagraph:
    ls = exact(LineSpacing(mode=LineSpacingMode.EXACT, value=pitch), W) if pitch else None
    return StyledParagraph(id=pid, runs=runs, align=align, line_spacing=ls, space_after_pt=after, source_line_count=lines, style_id=style, role=role)


def tb(tid, x, y, w, h, paras, wrap=True, z=2) -> StyledTextBox:
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h), z=z, paragraphs=paras, wrap=wrap)


def two_column_doc() -> StyledDoc:
    """仿照真實履歷的結構（數值相近），字型故意用⑤常見的推論結果：Inter Display、Loma（泰文字型）、FreeSans、
    Carlito、夾在內文裡的 Liberation Mono；另有一個整框都是等寬字的框（程式碼），等寬字型要保留。"""
    boxes = [
        tb("box-name", 34.6, 29.4, 225.9, 35.8, [para("p-name", [run("JORDAN AVERY", "Carlito", 29.0, True)], style="S3", role=Role.TITLE)], wrap=False),
        tb("box-contact", 442.3, 58.1, 119.5, 44.8, [para("p-contact", [run("jordan.avery@example.com", "Loma", 9.5, color=ACCENT),
                                                                         run(" +1 555 0199 Springfield, Exampleland", "Loma", 9.5)],
                                                          align=Align.RIGHT, lines=3, pitch=14.8)]),
        tb("box-sub", 33.9, 66.8, 70.2, 15.3, [para("p-sub", [run("Jordan A.", size=13.0, color=ACCENT)])], wrap=False),
        tb("box-tag", 33.5, 87.3, 271.6, 16.5, [para("p-tag", [run("Quality control · Food science · Lab data", size=10.0)])], wrap=False),
        tb("box-skills", 33.2, 142.2, 36.0, 11.1, [para("p-skills", [run("S K I L L S", "Inter", 9.0, True, ACCENT)])], wrap=False),
        tb("box-main1", 198.3, 141.1, 377.5, 114.9, [
            para("p-profile", [run("P R O F I L E", "Carlito", 10.0, True, ACCENT)], pitch=12.9, after=13.1),
            para("p-profile-text", [run("Food science graduate with two years of laboratory work and a small shelf-life database "
                                        "built from scratch, including automated weekly reports for a regional dairy cooperative "
                                        "and a family-run cheese maker in the hills.")], Align.JUSTIFY, 3, 12.9, 10.8),
            para("p-exp", [run("E X P E R I E N C E", "Carlito", 11.0, True, ACCENT)], pitch=12.9, after=13.6),
            para("p-job1", [run("NORTHWIND FOODS — quality analyst, Springfield", "FreeSans", 10.0, True)], pitch=12.9)]),
        tb("box-sidebar1", 33.2, 167.9, 126.3, 120.0, [
            para("p-s1", [run("Lab & testing", size=9.0, bold=True)], pitch=14.2, after=2.6),
            para("p-s2", [run("Sample preparation", "Loma", 9.5)], pitch=14.2),
            para("p-s3", [run("Shelf-life studies", "Loma", 9.5)], pitch=14.2),
            para("p-s4", [run("Sensory panels", size=9.5)], pitch=14.2, after=9.8),
            para("p-s5", [run("LA N G U A G ES", "Liberation Mono", 10.0, color=ACCENT)], pitch=14.2, after=4.0),
            para("p-s6", [run("English (native) · French (B2)", size=9.5)], pitch=14.2)]),
        tb("box-date1", 530.1, 243.9, 64.0, 11.8, [para("p-date1", [run("2025 – present", "FreeSans", 10.0)])], wrap=False),
        tb("box-bul1", 200.1, 257.5, 375.9, 54.0, [
            para("p-b1", [run("• Tested incoming milk samples every morning and logged the results so the team could compare "
                              "suppliers week by week and by season.")], lines=2, pitch=12.7, after=1.6),
            para("p-b2", [run("• Built a small sheet that shows rejected batches per line and updates every Monday from the lab system.")],
                 lines=2, pitch=12.7)]),
        tb("box-job2", 198.4, 330.0, 160.8, 13.4, [para("p-job2", [run("Riverside Kitchen — part-time", "Inter", 11.0, True)])], wrap=False),
        tb("box-date2", 511.3, 331.9, 82.3, 11.7, [para("p-date2", [run("Jan 2024 · 3 months", "FreeSans", 9.0)])], wrap=False),
        tb("box-bul2", 198.2, 350.0, 397.0, 26.0, [para("p-b3", [run("• Designed and ran a shelf-life trial for a small farm shop, including a "
                                                                    "simple tasting form and a printed results card.")], lines=2, pitch=12.7)]),
        tb("box-code", 33.2, 320.0, 120.0, 12.0, [para("p-code", [run("SELECT * FROM shops", "Liberation Mono", 8.0)])], wrap=False),
        tb("box-edu", 198.5, 400.0, 369.2, 30.0, [
            para("p-edu", [run("BSc Food Science — Example University, 2021 – 2025", size=10.0, bold=True)], pitch=12.7, after=3.3),
            para("p-edu2", [run("• Dissertation on storage temperature and the shelf life of soft cheese.")], pitch=12.7)]),
    ]
    shapes = [
        StyledShape(id="shp-sidebar", box=Box(x=0.0, y=121.8, w=177.0, h=PAGE_H - 121.8), z=0, geometry="rect", fill=Color(r=243, g=242, b=238)),
        StyledShape(id="shp-rule-profile", box=Box(x=198.1, y=159.5, w=396.2, h=0.0), z=1, geometry="line", stroke=Color(r=ACCENT[0], g=ACCENT[1], b=ACCENT[2]), stroke_width_pt=0.8),
        StyledShape(id="shp-rule-skills", box=Box(x=33.1, y=159.5, w=127.1, h=0.0), z=1, geometry="line", stroke=Color(r=ACCENT[0], g=ACCENT[1], b=ACCENT[2]), stroke_width_pt=0.8),
    ]
    order = ["box-name", "box-sub", "box-tag", "box-contact", "box-skills", "box-sidebar1", "box-code", "box-main1", "box-date1", "box-bul1",
             "box-job2", "box-date2", "box-bul2", "box-edu"]
    page = StyledPage(page_index=0, width_pt=PAGE_W, height_pt=PAGE_H, text_boxes=boxes, shapes=shapes, reading_order=order)
    styles = [Style(id="S1", name="內文", font_family="Inter Display", size_pt=9.5, bold=False, italic=False, color=Color(r=34, g=34, b=34), usage_count=20),
              Style(id="S3", name="姓名", font_family="Carlito", size_pt=29.0, bold=True, italic=False, color=Color(r=34, g=34, b=34), role=Role.TITLE, usage_count=1)]
    return StyledDoc(target_mode=exact(TargetMode.CROSS_SOURCE, W), source_app=exact("unknown", W), pages=[page], styles=styles)


# ---------- LibreOffice 渲染與重疊檢查 ----------


def fonts_conf(tmp: Path) -> Path:
    """只留下 DejaVu 一套字型的 fontconfig 設定（模擬使用者的裝置上沒有 Arial、Calibri 這些字型）。"""
    conf = tmp / "fonts-dejavu.conf"
    conf.write_text(f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>{DEJAVU_DIR}</dir>
  <cachedir>{tmp}/fc-cache</cachedir>
  <include ignore_missing="yes">/etc/fonts/conf.d</include>
</fontconfig>
""", encoding="utf-8")
    return conf


def to_pdf(docx: Path, fc: Path | None = None) -> bytes:
    env = dict(os.environ)
    if fc is not None:
        env["FONTCONFIG_FILE"] = str(fc)
    with tempfile.TemporaryDirectory(prefix="mortise-lo-") as tmp:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp}/p", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", tmp, str(docx)],
                       capture_output=True, timeout=180, env=env, check=False)
        return next(Path(tmp).glob("*.pdf")).read_bytes()


def text_lines(pdf: bytes) -> list[list[dict]]:
    """每頁的文字行：同一條基線、左右相鄰的字併成一行。字框用基線與字級估計（不受各字型的量度定義影響）。"""
    doc = pdfium.PdfDocument(pdf)
    pages = []
    for i in range(len(doc)):
        r = PageReader(doc, i)
        H = doc[i].get_size()[1]
        # PDFium 把行尾的連字號回報成 \x02（它判斷那是斷字用的連字號）：換回連字號
        chars = sorted(((c.text.replace("\x02", "-"), c.origin[0], H - c.origin[1], c.size, c.loose_box) for c in r.chars()
                        if c.text.strip() and not c.generated),
                       key=lambda c: (round(c[2], 1), c[1]))
        r.close()
        lines: list[dict] = []
        for t, x, base, size, lb in chars:
            x1 = x + max(0.3 * size, (lb[2] - lb[0]) if lb else 0.5 * size)
            for ln in lines:
                if abs(ln["base"] - base) < 0.8 and ln["x0"] - 1.2 * size < x < ln["x1"] + 1.2 * size:
                    ln["text"] += t
                    ln["x0"], ln["x1"] = min(ln["x0"], x), max(ln["x1"], x1)
                    ln["y0"], ln["y1"] = min(ln["y0"], base - 0.72 * size), max(ln["y1"], base + 0.18 * size)
                    break
            else:
                lines.append({"text": t, "x0": x, "x1": x1, "y0": base - 0.72 * size, "y1": base + 0.18 * size, "base": base})
        pages.append(lines)
    return pages


def overlapping(lines: list[dict], min_ratio: float = 0.25) -> list[tuple[str, str]]:
    """兩行的字框交集超過較小者面積的 min_ratio：文字互相重疊。"""
    out = []
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            ix = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            iy = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
            small = min((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
            if ix > 0 and iy > 0 and ix * iy > min_ratio * max(small, 1e-6):
                out.append((a["text"], b["text"]))
    return out


def missing_chars(texts: list[str], pages: list[list[dict]]) -> str:
    """成品文件的字有哪些沒畫出來（只比字元的數量，不比順序：並排的兩欄在 PDF 裡同一高度的行會交錯）。
    項目符號改成 Word 的自動編號後由編號畫出，不一定是同一個字元，不比。"""
    want = Counter(norm_text("".join(texts)).replace("•", ""))
    got = Counter(norm_text("".join(ln["text"] for lines in pages for ln in lines)).replace("•", ""))
    return "".join(sorted((want - got).elements()))


def baseline_drift(doc: StyledDoc, src_pdf: bytes, out_pdf: bytes) -> dict[str, float]:
    """每個文字框第一段開頭幾個字：輸出（LibreOffice 轉的 PDF）與原圖 PDF 的基線高度差（pt，正值為往下）。
    只比兩邊都只出現一次的開頭。"""
    def chars(pdf):
        d = pdfium.PdfDocument(pdf)
        r = PageReader(d, 0)
        H = d[0].get_size()[1]
        out = [(c.text, H - c.origin[1]) for c in r.chars() if c.text.strip() and not c.generated]
        r.close()
        return out

    S, G = chars(src_pdf), chars(out_pdf)
    st, gt = "".join(c[0] for c in S), "".join(c[0] for c in G)
    out = {}
    for t in doc.pages[0].text_boxes:
        key = norm_text("".join(r.text for r in t.paragraphs[0].runs))[:6]
        if len(key) >= 3 and st.count(key) == 1 and gt.count(key) == 1:
            out[key] = G[gt.index(key)][1] - S[st.index(key)][1]
    return out


def outside(lines: list[dict], w: float, h: float) -> list[str]:
    return [ln["text"] for ln in lines if ln["x1"] > w + 0.5 or ln["x0"] < -0.5 or ln["y1"] > h + 0.5]


def xml_of(data: bytes, part: str = "word/document.xml"):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read(part))


def doc_texts(doc: StyledDoc) -> list[str]:
    return ["".join(r.text for r in sp.runs) for p in doc.pages for t in p.text_boxes for sp in t.paragraphs]


# ---------- 素材：只產生、只轉檔一次 ----------


@pytest.fixture(scope="module")
def two_col(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docx-two-col")
    store = Store(tmp / "store")
    doc = two_column_doc()
    cfg = Config.load()
    outs = {"flow": docx_flow.export(doc, "cv", cfg, store), "fixed": docx_fixed.export(doc, "cv", cfg, store)}
    data = {k: store.read_blob(o.file) for k, o in outs.items()}
    pdfs = {}
    if libreoffice.available():
        jobs = [(m, fc) for m in outs for fc in (["normal", "dejavu"] if DEJAVU_DIR.is_dir() else ["normal"])]
        conf = fonts_conf(tmp) if DEJAVU_DIR.is_dir() else None
        with ThreadPoolExecutor(4) as ex:
            res = ex.map(lambda j: to_pdf(store.blob_path(outs[j[0]].file), conf if j[1] == "dejavu" else None), jobs)
            pdfs = dict(zip(jobs, res))
    return {"doc": doc, "out": outs, "data": data, "pdf": pdfs}


# ---------- 可重排：表格，不是浮動方塊 ----------


def _body_children(data: bytes):
    body = xml_of(data).find(q("w:body"))
    return [c for c in body if c.tag != q("w:sectPr")]


def _cells(tbl) -> list:
    return tbl.findall(f"{q('w:tr')}/{q('w:tc')}")


def _text(e) -> str:
    return "".join(t.text or "" for t in e.iter(q("w:t")))


def test_flow_side_by_side_blocks_are_tables(two_col):
    out, data = two_col["out"]["flow"], two_col["data"]["flow"]
    assert out.selfcheck_ok, out.selfcheck_notes
    root = xml_of(data)
    assert not list(root.iter(q("wp:anchor"))), "可重排版不可以有浮動物件"
    assert not list(root.iter(q("w:tblpPr"))), "表格不可以是浮動表格"
    kids = _body_children(data)
    tables = [k for k in kids if k.tag == q("w:tbl")]
    assert len(tables) == 2  # 標題列（姓名｜聯絡方式）、側欄＋主欄
    header, body = tables
    assert [len(_cells(t)) for t in tables] == [2, 2]
    hl, hr = _cells(header)
    assert "JORDAN AVERY" in _text(hl) and "Jordan A." in _text(hl) and "jordan.avery@example.com" in _text(hr)
    side, main = _cells(body)
    assert side.find(f"{q('w:tcPr')}/{q('w:shd')}").get(q("w:fill")) == "F3F2EE"  # 側欄底色是儲存格底色
    assert main.find(f"{q('w:tcPr')}/{q('w:shd')}") is None
    assert "SKILLS" in norm_text(_text(side)) and "Samplepreparation" in norm_text(_text(side))
    assert "PROFILE" in norm_text(_text(main)) and "NORTHWIND" in _text(main) and "Samplepreparation" not in norm_text(_text(main))
    # 側欄底色貼齊紙邊：表格往左伸進邊界（負的縮排），左欄寬到填色矩形的右緣
    ind = int(body.find(f"{q('w:tblPr')}/{q('w:tblInd')}").get(q("w:w")))
    assert ind < 0
    assert abs(int(body.find(q("w:tblGrid"))[0].get(q("w:w"))) - 177.0 * 20) <= 2
    # 列高是「至少」（字變多會長高），不禁止跨頁拆列（一列比一頁高時不會被裁掉）
    for t in root.iter(q("w:tbl")):
        for tr in t.findall(q("w:tr")):
            h = tr.find(f"{q('w:trPr')}/{q('w:trHeight')}")
            assert h is None or h.get(q("w:hRule")) == "atLeast"
            assert tr.find(f"{q('w:trPr')}/{q('w:cantSplit')}") is None
    # 標題列與並排區塊之間一定隔著段落（兩個表格相鄰會被併成一個）
    assert kids.index(body) - kids.index(header) >= 2


def test_flow_same_line_date_and_nested_row(two_col):
    root = xml_of(two_col["data"]["flow"])
    job1 = next(p for p in root.iter(q("w:p")) if "NORTHWIND" in _text(p))
    assert "2025 – present" in _text(job1)  # 日期接在同一段的行尾
    assert job1.find(f".//{q('w:tab')}") is not None
    tab = job1.find(f"{q('w:pPr')}/{q('w:tabs')}/{q('w:tab')}")
    assert tab.get(q("w:val")) == "right"
    # 經歷標題與右邊的日期（兩個框、左右分開）：主欄裡的第二層表格，日期靠右
    nested = [t for t in root.iter(q("w:tbl")) if t.getparent().tag == q("w:tc")]
    assert len(nested) == 1 and len(_cells(nested[0])) == 2
    left, right = _cells(nested[0])
    assert "Riverside Kitchen" in _text(left) and _text(right) == "Jan 2024 · 3 months"
    assert right.find(f".//{q('w:jc')}").get(q("w:val")) == "right"


def test_flow_text_complete_and_in_order(two_col):
    doc, data = two_col["doc"], two_col["data"]["flow"]
    allt = norm_text("".join(extract_texts(data).values()))
    for t in doc_texts(doc):
        assert norm_text(t).lstrip("•") in allt, t  # 項目符號改成 Word 的自動編號，不在文字裡
    # 閱讀順序：標題列（左→右）、側欄、主欄（上→下）
    seq = ["JORDANAVERY", "Qualitycontrol", "jordan.avery", "SKILLS", "Lab&testing", "SELECT", "PROFILE", "NORTHWIND", "2025–present", "Testedincoming", "RiversideKitchen", "Jan2024", "Designedandran", "BScFood"]
    pos = [allt.index(norm_text(k)) for k in seq]
    assert pos == sorted(pos), list(zip(seq, pos))


def test_flow_rule_becomes_paragraph_border(two_col):
    root = xml_of(two_col["data"]["flow"])
    prof = next(p for p in root.iter(q("w:p")) if norm_text(_text(p)) == "PROFILE")
    b = prof.find(f"{q('w:pPr')}/{q('w:pBdr')}/{q('w:bottom')}")
    assert b is not None and b.get(q("w:color")) == "2F5D73"
    concepts = {(d.element_id, d.concept): d.lossy for d in two_col["out"]["flow"].degradations}
    assert concepts[("shp-sidebar", "形狀 rect")] is False and concepts[("shp-rule-profile", "形狀 line")] is False


def test_flow_tables_can_be_turned_off(tmp_path):
    """side_by_side_tables = false 時回到舊做法（浮動方塊），確認開關有效。"""
    store = Store(tmp_path)
    cfg = Config.load()
    cfg.data["export"]["docx_flow"] = {"side_by_side_tables": False}
    out = docx_flow.export(two_column_doc(), "cv", cfg, store)
    assert list(xml_of(store.read_blob(out.file)).iter(q("wp:anchor")))


# ---------- 字型 ----------

OBSCURE = {"Loma", "FreeSans", "Inter Display", "Inter", "Liberation Mono", "Carlito"}


def _written_fonts(data: bytes) -> set[str]:
    names = set()
    for part in ("word/document.xml", "word/styles.xml", "word/numbering.xml"):
        try:
            root = xml_of(data, part)
        except KeyError:
            continue
        for f in root.iter(q("w:rFonts")):
            names.update(v for k, v in f.attrib.items() if k in (q("w:ascii"), q("w:hAnsi"), q("w:eastAsia"), q("w:cs")))
    return names


@pytest.mark.parametrize("mode", ["flow", "fixed"])
def test_fonts_written_are_portable(two_col, mode):
    out, data = two_col["out"][mode], two_col["data"][mode]
    styles_root = xml_of(data, "word/styles.xml")
    used_styles = {s.get(q("w:styleId")) for s in styles_root if s.tag == q("w:style")}
    doc_names = set()
    for f in xml_of(data).iter(q("w:rFonts")):
        doc_names.update(f.get(q(k)) for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"))
    assert doc_names <= {"Arial", "Calibri", "Courier New"}, doc_names
    assert not (doc_names & OBSCURE)
    assert set(out.fonts_used) == {"Arial", "Calibri", "Courier New"}
    assert used_styles  # 樣式表還在
    concepts = {d.concept: d for d in out.degradations}
    for fam in ("Inter Display", "Loma", "FreeSans", "Inter"):
        d = concepts[f"字型「{fam}」"]
        assert d.lossy and fam in d.action and "Arial" in d.action  # 寫明原本推論的字型
    assert concepts["字型「Carlito」"].lossy is False and "Calibri" in concepts["字型「Carlito」"].action


@pytest.mark.parametrize("mode", ["flow", "fixed"])
def test_mono_only_kept_for_all_mono_box(two_col, mode):
    root = xml_of(two_col["data"][mode])

    def run_font(text):
        for r in root.iter(q("w:r")):  # 只看直接含字的文字段（版面固定的繪圖物件也是 w:r，裡面包著整個文字方塊）
            if text in "".join(t.text or "" for t in r.findall(q("w:t"))):
                f = r.find(f"{q('w:rPr')}/{q('w:rFonts')}")
                return f.get(q("w:ascii")) if f is not None else None
        raise AssertionError(text)

    lang = run_font("LA N G U A G ES")
    assert lang in (None, "Arial")  # 夾在內文裡的等寬字：改用同框內文的字型（可重排時與樣式相同，不寫直接格式）
    assert run_font("SELECT * FROM shops") == "Courier New"  # 整框都是等寬字（程式碼）：保留等寬字型
    assert any(d.concept == "等寬字型「Liberation Mono」" and d.element_id == "box-sidebar1" for d in two_col["out"][mode].degradations)


@pytest.mark.parametrize("mode", ["flow", "fixed"])
def test_font_table_family_pitch_panose_altname(two_col, mode):
    out, data = two_col["out"][mode], two_col["data"][mode]
    fonts = {f.get(q("w:name")): f for f in xml_of(data, "word/fontTable.xml")}
    assert set(fonts) == set(out.fonts_used)  # 只列實際用到的字型（範本的 Symbol、ＭＳ 明朝 這類拿掉）

    def val(name, tag):
        e = fonts[name].find(q(tag))
        return None if e is None else e.get(q("w:val"))

    assert (val("Arial", "w:family"), val("Arial", "w:pitch"), val("Arial", "w:panose1")) == ("swiss", "variable", "020B0604020202020204")
    assert "Liberation Sans" in val("Arial", "w:altName")
    assert (val("Courier New", "w:family"), val("Courier New", "w:pitch")) == ("modern", "fixed")
    assert val("Calibri", "w:altName") == "Carlito" and val("Calibri", "w:family") == "swiss"
    # 子元素順序照綱要：altName、panose1、charset、family、pitch
    order = [etree.QName(c).localname for c in fonts["Arial"]]
    assert order == ["altName", "panose1", "charset", "family", "pitch"]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "docProps/thumbnail.jpeg" not in z.namelist()  # 範本附的空白縮圖拿掉


def test_font_category_and_policy():
    assert font_category("Acme Code Mono", None, False) == "mono"
    assert font_category("Acme Serif Display", None, False) == "serif"
    assert font_category("Acme Sans Serif", None, False) == "sans"
    assert font_category("Noto Serif TC", None, True) == "cjk_serif"
    assert font_category("LXGW WenKai TC", None, True) == "cjk_kai"
    assert font_category("Some Hei", None, True) == "cjk_sans"
    cfg = Config.load()
    ctx = docx_common.make_ctx(two_column_doc(), cfg)
    assert ctx.name_of("Liberation Serif") == "Times New Roman" and ctx.name_of("Caladea") == "Cambria"
    assert ctx.name_of("Acme Serif Display") == "Times New Roman" and ctx.name_of("Noto Serif TC", east_asian=True) == "PMingLiU"
    cfg.data["export"]["docx_font_names"] = "inferred"  # 舊做法：照推論的名稱寫
    ctx2 = docx_common.make_ctx(two_column_doc(), cfg)
    assert ctx2.name_of("Loma") == "Loma"


# ---------- 版面固定：單行框留足寬度 ----------


def _anchor(root, name):
    return next(a for a in root.iter(q("wp:anchor")) if a.find(q("wp:docPr")).get("name") == name)


def _geom(a):
    x = int(a.find(f"{q('wp:positionH')}/{q('wp:posOffset')}").text) / 12700
    w = int(a.find(q("wp:extent")).get("cx")) / 12700
    return x, w


def test_fixed_single_line_boxes_have_room(two_col):
    root = xml_of(two_col["data"]["fixed"])
    doc = two_col["doc"]
    boxes = {t.id: t for t in doc.pages[0].text_boxes}
    for name in ("box-skills", "box-name", "box-job2"):
        a = _anchor(root, name)
        x, w = _geom(a)
        need = docx_common.worst_case_width_pt(boxes[name].paragraphs[0], ["Arial", "Calibri"])
        assert abs(x - boxes[name].box.x) < 0.01  # 靠左：起點不動
        assert w >= need, (name, w, need)  # 換成最寬的常見字型也放得下（不折行）
        assert a.find(f".//{q('wps:bodyPr')}").get("wrap") == "none"
    # 貼著右側紙邊的日期：改成靠右對齊、右緣不動、往左加寬，不會超出紙邊
    for name in ("box-date1", "box-date2"):
        a = _anchor(root, name)
        x, w = _geom(a)
        b = boxes[name].box
        assert abs((x + w) - (b.x + b.w)) < 0.01 and w > b.w and x + w <= PAGE_W
        assert a.find(f".//{q('w:jc')}").get(q("w:val")) == "right"


def test_fixed_condenses_when_written_font_is_wider(two_col):
    """寫入的字型（Arial ≈ Liberation Sans）比量測用的 Inter Display 寬：緊縮字距，換行位置才和原圖一樣。"""
    if docx_common.font_file_info("Inter Display") is None or docx_common.text_width_pt("a", "Arial", 10) is None:
        pytest.skip("伺服器上沒有 Inter Display 或 Arial 的相容字型")
    root = xml_of(two_col["data"]["fixed"])
    r = next(r for r in _anchor(root, "box-bul1").iter(q("w:r")) if "Tested" in "".join(t.text or "" for t in r.iter(q("w:t"))))
    sp = r.find(f"{q('w:rPr')}/{q('w:spacing')}")
    assert sp is not None and -20 < int(sp.get(q("w:val"))) < 0


# ---------- LibreOffice：正常字型與精簡字型 ----------


@needs_lo
@pytest.mark.parametrize("fc", ["normal", pytest.param("dejavu", marks=needs_dejavu)])
@pytest.mark.parametrize("mode", ["flow", "fixed"])
def test_libreoffice_no_overlap(two_col, mode, fc):
    pdf = two_col["pdf"][(mode, fc)]
    pages = text_lines(pdf)
    size = pdfium.PdfDocument(pdf)[0].get_size()
    for lines in pages:
        assert not overlapping(lines), overlapping(lines)[:5]
        assert not outside(lines, *size)
    if mode == "fixed":
        assert len(pages) == 1
    else:
        assert len(pages) <= 2  # 可重排：字變寬可以變長，但不可重疊
    assert not missing_chars(doc_texts(two_col["doc"]), pages)


# ---------- 完整流水線：假資料的兩欄履歷 fixture ----------


@needs_lo
@needs_dejavu
def test_fixture_cv_two_column_end_to_end(tmp_path):
    p = Pipeline(tmp_path / "job")
    p.start(FIXTURES / "cv_two_column.pdf", "cv_two_column.pdf", Intent())
    p.run()
    doc = p.store.load("s5/styled_doc", StyledDoc)
    cfg = Config.load()
    outs = {"flow": docx_flow.export(doc, "cv", cfg, p.store), "fixed": docx_fixed.export(doc, "cv", cfg, p.store)}
    assert all(o.selfcheck_ok for o in outs.values())
    assert not list(xml_of(p.store.read_blob(outs["flow"].file)).iter(q("wp:anchor")))
    conf = fonts_conf(tmp_path)
    jobs = [(m, fc) for m in outs for fc in ("normal", "dejavu")]
    with ThreadPoolExecutor(4) as ex:
        pdfs = dict(zip(jobs, ex.map(lambda j: to_pdf(p.store.blob_path(outs[j[0]].file), conf if j[1] == "dejavu" else None), jobs)))
    for (mode, fc), pdf in pdfs.items():
        pages = text_lines(pdf)
        for lines in pages:
            assert not overlapping(lines), (mode, fc, overlapping(lines)[:5])
        if mode == "fixed":
            assert len(pages) == 1, (mode, fc)
        assert not missing_chars(doc_texts(doc), pages), (mode, fc)
    # 正常字型下每個文字框開頭的基線位置和原圖比：可重排也要放回原位（空隙從「排出來的」行上下緣算，
    # 不從貼著字的框算；原本每個框多一兩點，一頁累積 17 pt）
    src = (FIXTURES / "cv_two_column.pdf").read_bytes()
    for mode in ("fixed", "flow"):
        drift = baseline_drift(doc, src, pdfs[(mode, "normal")])
        assert len(drift) >= 20 and max(abs(d) for d in drift.values()) <= 3.0, (mode, drift)


# ---------- 版面固定：行數多於原圖時緊縮字距 ----------


def test_wrapped_line_count_simulation():
    ch = lambda t, w=1.0: [(c, w, 0.0) for c in t]  # noqa: E731
    f = docx_common.wrapped_line_count
    assert f(ch("aaa bbb"), 7) == 1 and f(ch("aaa bbb"), 6.9) == 2  # 行尾的空白不算寬度
    assert f(ch("aaa bbb ccc"), 11) == 1 and f(ch("aaa bbb ccc"), 10.9) == 2
    assert f(ch("aaa\nbbb"), 10) == 2  # 強制換行
    assert f(ch("aaaaaaaaaa"), 4) == 3  # 一個字比整行還寬：在字中間斷
    assert f(ch("中文字中文字"), 3) == 2  # 中日韓文字每個字都可以斷
    assert f([(c, 1.0, -0.1) for c in "aaa bbb"], 6.5) == 1  # 字距（負值為緊縮）算進寬度


def test_fit_source_lines_condenses_only_when_needed():
    """原圖一行的字，寫入的字型（Arial ≈ Liberation Sans）量起來比框寬一點點：緊縮字距讓它回到一行；
    寬太多（超過上限）就不硬擠，照實回報行數。"""
    text = "Presented the weekly test results to the plant manager and the buyers."
    w = docx_common.text_width_pt(text, "Arial", 10.0)
    if w is None:
        pytest.skip("伺服器上沒有與 Arial 字寬相容的字型")
    ctx = docx_common.make_ctx(two_column_doc(), Config.load())
    sp = para("p-fit", [run(text, "Liberation Sans", 10.0)])
    same, before, after = docx_common.fit_source_lines(sp, w + 2.0, ctx, "Arial", [0], 0.04)
    assert (same, before, after) == ([0], 1, 1)  # 放得下：不改
    new, before, after = docx_common.fit_source_lines(sp, w * 0.99, ctx, "Arial", [0], 0.04)
    assert before == 2 and after == 1 and -8 <= new[0] < 0  # 緊縮不到字級的 4%（10 pt → 每字最多 0.4 pt = 8 twip）
    kept, before, after = docx_common.fit_source_lines(sp, w * 0.9, ctx, "Arial", [0], 0.04)
    assert kept == [0] and before == after == 2


def test_fixed_fits_paragraph_back_to_source_lines(tmp_path):
    """仿照真實案例的那一段（文字是虛構的）：⑤配的驗證字型比原圖寬、第二行量起來超過框寬 → 版面固定在那一段緊縮字距，行數回到原圖，
    框不會往下長、蓋到下面的框（原本一段經歷的最後一行蓋到下一個經歷標題）。"""
    line1 = "• The dairy wanted fewer rejected batches; as more buyers now ask for test results with"
    line2 = " every order, I proposed a shared results sheet (updated after each test) alongside labels."
    w2 = docx_common.text_width_pt(line2.strip(), "Arial", 10.0)
    if w2 is None:
        pytest.skip("伺服器上沒有與 Arial 字寬相容的字型")
    box_w = w2 * 0.99
    doc = two_column_doc()
    page = doc.pages[0]
    extra = tb("box-fit", 200.1, 460.0, box_w, 26.0, [para("p-fit", [run(line1, "Loma", 9.5), run(line2, "Loma", 10.0)], Align.RIGHT, 2, 12.7)])
    page = page.model_copy(update={"text_boxes": page.text_boxes + [extra], "reading_order": page.reading_order + ["box-fit"]})
    doc = doc.model_copy(update={"pages": [page]})
    store = Store(tmp_path)
    out = docx_fixed.export(doc, "cv", Config.load(), store)
    root = xml_of(store.read_blob(out.file))
    rs = [r for r in _anchor(root, "box-fit").iter(q("w:r")) if r.findall(q("w:t"))]
    vals = [int(r.find(f"{q('w:rPr')}/{q('w:spacing')}").get(q("w:val"))) for r in rs]
    assert all(v < 0 for v in vals)
    d = next(d for d in out.degradations if d.element_id == "box-fit" and d.concept == "換行")
    assert not d.lossy and "2 行" in d.action and "3 行" in d.action


# ---------- 內嵌字型（設定 docx_embed_fonts，預設關） ----------


def test_obfuscate_font_follows_ecma376():
    key = "{00112233-4455-6677-8899-AABBCCDDEEFF}"
    data = bytes(40)
    ob = docx_common.obfuscate_font(data, key)
    assert ob[0] == 0xFF and ob[15] == 0x00 and ob[16] == 0xFF and ob[31] == 0x00  # 鑰匙是 GUID 的 16 個位元組反序
    assert ob[32:] == data[32:]  # 只混淆前 32 個位元組
    assert docx_common.obfuscate_font(ob, key) == data


def test_embed_fonts_off_by_default_and_on_when_set(tmp_path):
    store = Store(tmp_path / "store")
    cfg = Config.load()
    assert cfg.section("export")["docx_embed_fonts"] is False
    off = docx_fixed.export(two_column_doc(), "cv", cfg, store)
    with zipfile.ZipFile(store.blob_path(off.file)) as z:
        assert not [n for n in z.namelist() if n.startswith("word/fonts/")]
    if docx_common.embeddable_face("Arial", False, False)[0] is None:
        pytest.skip("伺服器上沒有可以內嵌、與 Arial 字寬相容的字型")
    cfg.data["export"]["docx_embed_fonts"] = True
    out = docx_fixed.export(two_column_doc(), "cv", cfg, store)
    assert out.selfcheck_ok, out.selfcheck_notes
    data = store.read_blob(out.file)
    fonts = {f.get(q("w:name")): f for f in xml_of(data, "word/fontTable.xml")}
    reg, bold = fonts["Arial"].find(q("w:embedRegular")), fonts["Arial"].find(q("w:embedBold"))
    assert reg is not None and bold is not None  # 「S K I L L S」是粗體：粗體字面也內嵌
    assert fonts["Arial"].find(q("w:embedItalic")) is None  # 沒用到斜體：不內嵌
    order = [etree.QName(c).localname for c in fonts["Arial"]]
    assert order[-2:] == ["embedRegular", "embedBold"] and order.index("pitch") < order.index("embedRegular")  # 綱要順序
    rels = {r.get("Id"): r for r in xml_of(data, "word/_rels/fontTable.xml.rels")}
    rel = rels[reg.get(q("r:id"))]
    assert rel.get("Type").endswith("/relationships/font")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        raw = z.read("word/" + rel.get("Target"))
        types = z.read("[Content_Types].xml").decode()
    plain = docx_common.obfuscate_font(raw, reg.get(q("w:fontKey")))
    assert plain[:4] in (b"\x00\x01\x00\x00", b"true") and raw[:4] != plain[:4]
    assert "application/vnd.openxmlformats-officedocument.obfuscatedFont" in types
    assert xml_of(data, "word/settings.xml").find(q("w:embedTrueTypeFonts")) is not None
    assert any(d.concept == "內嵌字型「Arial」" and "內嵌" in d.action for d in out.degradations)
    if libreoffice.available() and DEJAVU_DIR.is_dir():
        # 只留 DejaVu 的環境：LibreOffice 用內嵌的字型排版（PDF 裡出現 Liberation Sans），不是退回 DejaVu Sans
        pdf = to_pdf(store.blob_path(out.file), fonts_conf(tmp_path))
        assert b"LiberationSans" in pdf
