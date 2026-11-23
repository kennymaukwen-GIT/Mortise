"""Word 輸出：審查找到的問題的回歸測試（假資料）。

  1. 可重排：側欄比主欄寬（4:6）、三欄的履歷，原本被當成「報紙式分欄」，改用 Word 分欄加錨在段落上的浮動方塊
     （在 iPhone 上錯位的那種做法）。現在只有真正的報紙式分欄才用 Word 分欄，其他一律是看不見框線的表格。
  2. 可重排：第二頁的側欄底色從紙張上緣開始時，列的最小高度比文字區還高，多出一頁空白。
  3. 版面固定：多段共用一個文字方塊時，上面的段落在較寬的字型下多排幾行，下面的標題就一路被往下推；
     現在標題這類單行字各自一個方塊、釘在原圖的位置。項目符號的段落有懸掛縮排；被判成靠右的多行項目當成靠左。
  4. 被④併成一段的清單項目（側欄的技能），字變寬時項目接在一起：項目之間寫強制換行。
  5. 窄欄裡的單行標題（24 pt 粗體的姓名）在 Times 類字型下從字中間折斷：留寬度時也比 Times New Roman。
  6. 可重排：原圖只有一行的段落不寫右縮排（框寬是墨跡寬度，照它寫右縮排會把最後一個字擠到下一行）。
人名、經歷都是虛構的。
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from lxml import etree

from mortise.artifacts import (
    Align, Box, Color, FontChoice, LineSpacing, LineSpacingMode, StyledDoc, StyledPage, StyledParagraph, StyledRun, StyledShape,
    StyledTextBox, Style, TargetMode,
)
from mortise.artifacts.common import Role, exact
from mortise.config import Config
from mortise.exporters import docx_common, docx_fixed, docx_flow
from mortise.exporters.docx_common import q
from mortise.pipeline.store import Store
from mortise.tools import libreoffice
from mortise.tools.pdfium import PageReader

W = "test@1"
PAGE_W, PAGE_H = 595.276, 841.89
GREY = Color(r=243, g=242, b=238)
DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
needs_lo = pytest.mark.skipif(not libreoffice.available(), reason="沒有 LibreOffice")
needs_dejavu = pytest.mark.skipif(not DEJAVU_DIR.is_dir(), reason="沒有 DejaVu 字型目錄，無法模擬精簡字型")


def run(text, fam="Liberation Sans", size=9.5, bold=False, color=(34, 34, 34)) -> StyledRun:
    font = FontChoice(family=exact(fam, W), verification_family=fam, is_substituted=False)
    return StyledRun(text=text, font=font, size_pt=exact(size, W), bold=bold, color=Color(r=color[0], g=color[1], b=color[2]))


def para(pid, runs, align=Align.LEFT, lines=1, pitch=None, after=0.0, style="S1", role=Role.BODY, indent=0.0) -> StyledParagraph:
    ls = exact(LineSpacing(mode=LineSpacingMode.EXACT, value=pitch), W) if pitch else None
    return StyledParagraph(id=pid, runs=runs, align=align, line_spacing=ls, space_after_pt=after, source_line_count=lines, style_id=style,
                           role=role, first_line_indent_pt=indent)


def tb(tid, x, y, w, h, paras, wrap=True, z=2) -> StyledTextBox:
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h), z=z, paragraphs=paras, wrap=wrap)


def fill(sid, x, y, w, h) -> StyledShape:
    return StyledShape(id=sid, box=Box(x=x, y=y, w=w, h=h), z=0, geometry="rect", fill=GREY)


def make_doc(pages: list[StyledPage]) -> StyledDoc:
    styles = [Style(id="S1", name="內文", font_family="Liberation Sans", size_pt=9.5, bold=False, italic=False, color=Color(r=34, g=34, b=34), usage_count=20),
              Style(id="S2", name="標題", font_family="Liberation Sans", size_pt=9.0, bold=True, italic=False, color=Color(r=34, g=34, b=34), usage_count=5)]
    return StyledDoc(target_mode=exact(TargetMode.CROSS_SOURCE, W), source_app=exact("unknown", W), pages=pages, styles=styles)


def page(index, boxes, shapes=()) -> StyledPage:
    return StyledPage(page_index=index, width_pt=PAGE_W, height_pt=PAGE_H, text_boxes=boxes, shapes=list(shapes), reading_order=[b.id for b in boxes])


def heading(tid, x, y, text) -> StyledTextBox:
    return tb(tid, x, y, 6.0 * len(text), 9.0, [para(f"{tid}-p", [run(text, size=9.0, bold=True)], style="S2")], wrap=False)


def job_block(prefix, x, y, w) -> list[StyledTextBox]:
    """經歷：標題（左）、日期（右）、兩個項目符號（各兩行）。"""
    return [
        tb(f"{prefix}-h", x, y, 150.0, 10.0, [para(f"{prefix}-hp", [run("Example Bakery — baker", size=10.0, bold=True)], style="S2")], wrap=False),
        tb(f"{prefix}-d", x + w - 45.0, y, 45.0, 10.0, [para(f"{prefix}-dp", [run("2021 – 2023", size=9.0)])], wrap=False),
        tb(f"{prefix}-b", x + 2.0, y + 16.0, w - 2.0, 52.0, [
            para(f"{prefix}-b1", [run("• Checked incoming flour every morning and logged the results so the team could compare suppliers.")],
                 lines=2, pitch=12.7, after=1.6),
            para(f"{prefix}-b2", [run("• Built a small sheet that shows rejected batches per line and updates every Monday.")], lines=2, pitch=12.7)]),
    ]


def wide_sidebar_page() -> StyledPage:
    """側欄 240 pt、主欄 308 pt（4:6）：兩欄都比版心的六成窄，舊做法會當成報紙式分欄。"""
    boxes = [tb("name", 34.0, 39.0, 210.0, 24.0, [para("name-p", [run("TAYLOR BROOK", size=22.0, bold=True)], role=Role.TITLE, style="S2")], wrap=False),
             heading("s-h", 30.0, 144.0, "PROFILE"),
             tb("s-text", 30.0, 170.0, 190.0, 64.0, [para("s-t1", [run("Supervisor with six years in a regional warehouse, keen on tidy "
                                                                       "shift plans and calm handovers between teams.")], lines=4, pitch=14.0)]),
             heading("m-h", 262.0, 144.0, "EXPERIENCE")]
    boxes += job_block("j1", 262.0, 170.0, 308.0) + job_block("j2", 262.0, 250.0, 308.0)
    return page(0, boxes, [fill("side", 0.0, 122.0, 240.0, PAGE_H - 122.0)])


def three_column_page() -> StyledPage:
    """三欄：側欄（技能）、主欄（經歷）、右欄（獎項），各欄都有標題。"""
    boxes = [tb("name", 34.0, 39.0, 200.0, 24.0, [para("name-p", [run("ALEX MORROW", size=22.0, bold=True)], role=Role.TITLE, style="S2")], wrap=False),
             heading("a-h", 24.0, 144.0, "SKILLS"),
             tb("a-l", 24.0, 170.0, 100.0, 175.0, [para("a-l1", [run("Soil sampling GPS survey Report writing Lab safety Field notes Map reading")],
                                                     lines=6, pitch=12.5)]),
             heading("b-h", 170.0, 144.0, "EXPERIENCE"),
             heading("c-h", 440.0, 144.0, "AWARDS"),
             tb("c-l", 440.0, 170.0, 110.0, 175.0, [para("c-l1", [run("Best field report 2024 Volunteer of the year Dean's list 2021")],
                                                     lines=3, pitch=12.5)])]
    boxes += job_block("j1", 170.0, 170.0, 250.0) + job_block("j2", 170.0, 250.0, 250.0)
    assert min(b.box.y + b.box.h for b in boxes if b.id in ("a-l", "c-l")) - 144.0 >= 0.2 * PAGE_H  # 舊做法會當成三欄（見 _detect_columns）
    return page(0, boxes, [fill("side", 0.0, 122.0, 150.0, PAGE_H - 122.0)])


def xml_of(data: bytes, part: str = "word/document.xml"):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read(part))


def export(mod, doc, tmp_path):
    store = Store(tmp_path / f"store-{mod.__name__.rsplit('.', 1)[-1]}")
    out = mod.export(doc, "cv", Config.load(), store)
    return out, store.read_blob(out.file), store.blob_path(out.file)


def to_pdf(docx: Path, fc: Path | None = None) -> bytes:
    env = dict(os.environ)
    if fc is not None:
        env["FONTCONFIG_FILE"] = str(fc)
    with tempfile.TemporaryDirectory(prefix="mortise-lo-") as tmp:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp}/p", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", tmp, str(docx)],
                       capture_output=True, timeout=180, env=env, check=False)
        return next(Path(tmp).glob("*.pdf")).read_bytes()


def fonts_conf(tmp: Path) -> Path:
    """只留下 DejaVu 一套字型（模擬使用者的裝置上沒有 Arial 這類字型、被換成較寬的字型）。"""
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


def baselines(pdf: bytes, key: str) -> list[float]:
    """PDF 裡以 key 開頭（去空白）的位置的基線高度（pt，往下為正）。"""
    d = pdfium.PdfDocument(pdf)
    out = []
    for i in range(len(d)):
        r = PageReader(d, i)
        H = d[i].get_size()[1]
        chars = [(c.text, H - c.origin[1] + i * 10000) for c in r.chars() if c.text.strip() and not c.generated]
        r.close()
        s = "".join(c[0] for c in chars)
        out += [chars[m.start()][1] for m in re.finditer(re.escape(key), s)]
    return out


# ---------- 1. 可重排：只有報紙式分欄才用 Word 分欄 ----------


@pytest.mark.parametrize("make", [wide_sidebar_page, three_column_page], ids=["sidebar_4_6", "three_columns"])
def test_flow_side_by_side_layouts_are_tables_not_columns(make, tmp_path):
    out, data, _ = export(docx_flow, make_doc([make()]), tmp_path)
    assert out.selfcheck_ok, out.selfcheck_notes
    root = xml_of(data)
    assert not list(root.iter(q("wp:anchor"))), "可重排版不可以有浮動物件"
    assert not [c for c in root.iter(q("w:cols")) if c.get(q("w:num"))], "不可以用 Word 分欄"
    tables = list(root.find(q("w:body")).iter(q("w:tbl")))
    assert tables
    fills = [s.get(q("w:fill")) for s in root.iter(q("w:shd"))]
    assert "F3F2EE" in fills  # 側欄底色是儲存格底色
    assert any(d.concept == "分欄" and "不是報紙式分欄" in d.action for d in out.degradations)


def test_newspaper_columns_still_use_word_columns(tmp_path):
    """真正的報紙式分欄（兩欄同寬、上緣對齊、全是同一種內文、沒有底色）仍用 Word 分欄：改字時文字從一欄流到下一欄。"""
    body = "Plain body text that runs down the first column and continues at the top of the second column without a heading. "
    boxes = [tb("t", 60.0, 60.0, 475.0, 24.0, [para("t-p", [run("A Short Story", size=20.0, bold=True)], role=Role.TITLE, style="S2")], wrap=False),
             tb("l", 60.0, 110.0, 230.0, 400.0, [para("l1", [run(body * 3)], lines=12, pitch=13.0)]),
             tb("r", 305.0, 110.0, 230.0, 400.0, [para("r1", [run(body * 3)], lines=12, pitch=13.0)])]
    out, data, _ = export(docx_flow, make_doc([page(0, boxes)]), tmp_path)
    root = xml_of(data)
    assert any(c.get(q("w:num")) == "2" for c in root.iter(q("w:cols")))


# ---------- 2. 可重排：底色從紙張上緣開始的頁，列高不可以超過文字區 ----------


def two_page_doc() -> StyledDoc:
    p1 = [tb("name", 34.0, 39.0, 160.0, 24.0, [para("name-p", [run("ROBIN VALE", size=22.0, bold=True)], role=Role.TITLE, style="S2")], wrap=False),
          heading("p1-sh", 33.0, 144.0, "SKILLS"), heading("p1-mh", 198.0, 144.0, "EXPERIENCE")]
    p1 += [x for k in range(5) for x in job_block(f"p1j{k}", 198.0, 170.0 + 80.0 * k, 380.0)]
    p2 = [heading("p2-sh", 33.0, 44.0, "LANGUAGES"), heading("p2-mh", 198.0, 44.0, "EXPERIENCE (CONTINUED)"),
          tb("p2-l", 33.0, 70.0, 64.0, 30.0, [para("p2-l1", [run("English Portuguese (C1)")], lines=2, pitch=14.0)])]
    p2 += [x for k in range(3) for x in job_block(f"p2j{k}", 198.0, 70.0 + 80.0 * k, 380.0)]
    return make_doc([page(0, p1, [fill("side1", 0.0, 122.0, 177.0, PAGE_H - 122.0)]),
                     page(1, p2, [fill("side2", 0.0, 0.0, 177.0, PAGE_H)])])  # 第二頁的側欄底色從紙張上緣到底


def test_flow_row_height_fits_text_area(tmp_path):
    out, data, path = export(docx_flow, two_page_doc(), tmp_path)
    root = xml_of(data)
    mar = root.find(f".//{q('w:sectPr')}/{q('w:pgMar')}")
    area = PAGE_H - (int(mar.get(q("w:top"))) + int(mar.get(q("w:bottom")))) / 20.0
    heights = [int(h.get(q("w:val"))) / 20.0 for h in root.iter(q("w:trHeight"))]
    assert heights and max(heights) <= area, (heights, area)
    if libreoffice.available():
        assert len(pdfium.PdfDocument(to_pdf(path))) == 2  # 兩頁，不會多一頁空白


# ---------- 3. 版面固定：段落分開放、懸掛縮排、項目的對齊 ----------


def stacked_box_doc() -> StyledDoc:
    """一個框裡：兩個項目符號（各兩行）、單行的標題、再一個單行段落（仿照④把經歷的項目與下一個標題併在同一框）。"""
    text = "• Checked incoming flour every morning and logged the results so the whole team could compare suppliers week by week."
    w_arial = docx_common.text_width_pt(text, "Arial", 10.0) or 500.0
    box_w = 0.53 * w_arial  # Arial 排兩行，DejaVu Sans（寬約 12%）排三行
    paras = [para("b1", [run(text, size=10.0)], lines=2, pitch=13.0, after=2.0, indent=3.0),
             para("b2", [run(text, size=10.0)], Align.RIGHT, lines=2, pitch=13.0, after=10.0, indent=3.0),  # ⑤把這一項誤判成靠右
             para("h", [run("EDUCATION", size=9.0, bold=True)], pitch=13.0, after=6.0, style="S2"),
             para("d", [run("BSc Food Science — Example University", size=10.0)], pitch=13.0)]
    box = tb("box", 200.0, 300.0, box_w, 2 * 26.0 + 2.0 + 10.0 + 13.0 + 6.0 + 13.0, paras)
    return make_doc([page(0, [box])])


def _anchors(root) -> dict:
    out = {}
    for a in root.iter(q("wp:anchor")):
        name = a.find(q("wp:docPr")).get("name")
        x = int(a.find(f"{q('wp:positionH')}/{q('wp:posOffset')}").text) / 12700
        y = int(a.find(f"{q('wp:positionV')}/{q('wp:posOffset')}").text) / 12700
        out[name] = (x, y, a)
    return out


def test_fixed_paragraphs_split_into_boxes_at_source_positions(tmp_path):
    out, data, _ = export(docx_fixed, stacked_box_doc(), tmp_path)
    assert out.selfcheck_ok, out.selfcheck_notes
    an = _anchors(xml_of(data))
    # 兩個項目符號同一框；標題、最後一段各自一框（名稱「元素編號-p段落序號」）
    assert {"box", "box-p3", "box-p4"} <= set(an) and "box-p2" not in an
    y0 = an["box"][1]
    # 位置照目標引擎排這個框的方式推算：固定行距 13 pt，兩項各兩行、段後 2 與 10 pt
    assert an["box-p3"][1] - y0 == pytest.approx(2 * 13.0 + 2.0 + 2 * 13.0 + 10.0, abs=0.05)
    assert an["box-p4"][1] - y0 == pytest.approx(2 * 13.0 + 2.0 + 2 * 13.0 + 10.0 + 13.0 + 6.0, abs=0.05)
    assert any(d.element_id == "box" and d.concept == "段落各自一個文字方塊" for d in out.degradations)
    # 單行的標題框不換行、留足寬度
    head = an["box-p3"][2]
    assert head.find(f".//{q('wps:bodyPr')}").get("wrap") == "none"
    # 項目符號：懸掛縮排（左縮排 = 首行縮排 3 pt + 「• 」的寬度，首行凸出「• 」的寬度），⑤誤判成靠右的多行項目改成靠左
    marker = docx_common.text_width_pt("• ", "Liberation Sans", 10.0)
    inds = [p.find(f"{q('w:pPr')}/{q('w:ind')}") for p in an["box"][2].iter(q("w:p"))]
    assert all(i is not None for i in inds)
    for i in inds:
        assert int(i.get(q("w:hanging"))) == docx_common.twip(marker)
        assert int(i.get(q("w:left"))) == docx_common.twip(marker) + docx_common.twip(3.0)
    jcs = [p.find(f"{q('w:pPr')}/{q('w:jc')}").get(q("w:val")) for p in an["box"][2].iter(q("w:p"))]
    assert jcs == ["left", "left"]


def test_flow_misjudged_bullet_is_left_aligned(tmp_path):
    out, data, _ = export(docx_flow, stacked_box_doc(), tmp_path)
    root = xml_of(data)
    for p in root.iter(q("w:p")):
        if "".join(t.text or "" for t in p.iter(q("w:t"))).startswith("Checked"):
            jc = p.find(f"{q('w:pPr')}/{q('w:jc')}")
            assert jc is None or jc.get(q("w:val")) in ("left", "both")


@needs_lo
@needs_dejavu
def test_fixed_heading_stays_put_when_bullets_grow(tmp_path):
    """只剩 DejaVu 時項目符號各多排一行：標題仍在原圖的位置（舊做法整框一起排，標題被往下推一兩行）。"""
    doc = stacked_box_doc()
    sp = doc.pages[0].text_boxes[0].paragraphs[0]
    ctx = docx_common.make_ctx(doc, Config.load())
    w = doc.pages[0].text_boxes[0].box.w
    if docx_common.simulated_lines(sp, w, ctx, "Arial") != 2 or docx_common.simulated_lines(sp, w, ctx, "Arial", "DejaVu Sans") < 3:
        pytest.skip("伺服器上的字型量度和預期不同，無法構成「DejaVu 多排一行」的情況")
    conf = fonts_conf(tmp_path)
    _, _, path = export(docx_fixed, doc, tmp_path)
    normal, narrow = baselines(to_pdf(path), "EDUCATION"), baselines(to_pdf(path, conf), "EDUCATION")
    assert len(normal) == len(narrow) == 1
    assert abs(narrow[0] - normal[0]) <= 1.0, (normal, narrow)
    # 對照：不分開放時，標題被往下推
    docx_fixed.DEFAULT_PARAMS["split_paragraphs"] = "none"
    try:
        _, _, path2 = export(docx_fixed, doc, tmp_path / "none")
    finally:
        docx_fixed.DEFAULT_PARAMS["split_paragraphs"] = "groups"
    pushed = baselines(to_pdf(path2, conf), "EDUCATION")
    assert pushed[0] - normal[0] >= 10.0, (normal, pushed)


# ---------- 4. 清單項目被併成一段 ----------


def test_list_breaks_infers_item_starts():
    lb = docx_common.list_breaks
    sizes = docx_common.text_width_pt

    def sp(text, lines, align=Align.LEFT):
        return para("x", [run(text, size=9.0)], align, lines=lines, pitch=12.5)

    w = sizes("Report writing", "Liberation Sans", 9.0) + 1.0
    got = lb(sp("Soil sampling GPS survey Report writing", 3), w)
    assert got is not None and got.runs[0].text == "Soil sampling\nGPS survey\nReport writing"
    # 項目自己也折行：「Commercial dairy supplier review」在框寬裡排兩行，總共三行
    w2 = sizes("Field sampling & logging", "Liberation Sans", 9.0) + 1.0
    got = lb(sp("Field sampling & logging Commercial dairy supplier review", 3), w2)
    assert got is not None and got.runs[0].text == "Field sampling & logging\nCommercial dairy supplier review"
    # 括號裡、連接詞後面的大寫字不是項目開頭
    w3 = sizes("Portuguese (Native Speaker)", "Liberation Sans", 9.0) + 1.0
    got = lb(sp("English (IELTS Band) Portuguese (Native Speaker)", 2), w3)
    assert got is not None and got.runs[0].text == "English (IELTS Band)\nPortuguese (Native Speaker)"
    # 不是清單的不改：句子（有逗號、句點）、項目符號開頭、靠右、原圖一行
    assert lb(sp("Lab assistant with three years in a teaching lab, keen on tidy records.", 2), 150.0) is None
    assert lb(sp("• Checked samples Every morning", 2), 80.0) is None
    assert lb(sp("Soil sampling GPS survey Report writing", 3, Align.RIGHT), w) is None
    assert lb(sp("Soil sampling GPS survey", 1), 300.0) is None


def sidebar_list_doc() -> StyledDoc:
    items = [f"Skill item number {k}" for k in range(1, 9)]
    w = docx_common.text_width_pt(items[-1], "Liberation Sans", 9.0) + 1.0
    side = tb("list", 33.0, 170.0, w, 8 * 14.0, [para("list-p", [run(" ".join(items), size=9.0)], lines=8, pitch=14.0)])
    main = tb("main", 198.0, 170.0, 380.0, 26.0, [para("main-p", [run("Recent graduate looking for a first analyst role in a small team, "
                                                                     "comfortable with spreadsheets and simple dashboards.")], lines=2, pitch=13.0)])
    return make_doc([page(0, [heading("sh", 33.0, 144.0, "SKILLS"), side, heading("mh", 198.0, 144.0, "PROFILE"), main],
                          [fill("side", 0.0, 122.0, 177.0, PAGE_H - 122.0)])])


def test_merged_list_items_get_line_breaks(tmp_path):
    doc = sidebar_list_doc()
    out, data, _ = export(docx_flow, doc, tmp_path)
    root = xml_of(data)
    p = next(p for p in root.iter(q("w:p")) if "Skill item number 1" in "".join(t.text or "" for t in p.iter(q("w:t"))))
    assert len(list(p.iter(q("w:br")))) == 7  # 8 個項目之間 7 個強制換行
    assert any(d.concept == "清單項目" for d in out.degradations)
    # 版面固定：每一行都已經分開，框當成單行框（不換行、依最寬的常見字型留足寬度）
    out, data, _ = export(docx_fixed, doc, tmp_path)
    a = _anchors(xml_of(data))["list"]
    assert a[2].find(f".//{q('wps:bodyPr')}").get("wrap") == "none"
    wide = docx_common.text_width_pt("Skill item number 8", "DejaVu Sans", 9.0)
    assert int(a[2].find(q("wp:extent")).get("cx")) / 12700 >= wide


@needs_lo
@needs_dejavu
def test_merged_list_items_stay_apart_in_wider_font(tmp_path):
    """只剩 DejaVu 時，可重排版每個項目仍各自從新的一行開始（不會接成「number / 1 Skill item」）。"""
    _, _, path = export(docx_flow, sidebar_list_doc(), tmp_path)
    pdf = to_pdf(path, fonts_conf(tmp_path))
    ys = [baselines(pdf, f"Skillitemnumber{k}")[0] for k in range(1, 9)]
    assert all(b - a > 5.0 for a, b in zip(ys, ys[1:])), ys


# ---------- 5. 窄欄裡的單行標題：Times 類字型下不從字中間折斷 ----------


def name_in_sidebar_doc() -> StyledDoc:
    w = 141.3
    name = tb("name", 33.0, 40.0, w, 60.0, [para("name-p", [run("MAX HOLLOWAY", size=23.2, bold=True)], lines=2, pitch=28.0, role=Role.TITLE, style="S2")])
    side = tb("side-t", 33.0, 120.0, w, 28.0, [para("side-p", [run("Lab assistant with three years in a teaching laboratory.")], lines=2, pitch=14.0)])
    main = tb("main", 198.0, 40.0, 380.0, 26.0, [para("main-p", [run("Keen on tidy records, calm handovers and clear labels for every shelf.")],
                                                      lines=2, pitch=13.0)])
    return make_doc([page(0, [name, side, main], [fill("side", 0.0, 0.0, 177.0, PAGE_H)])])


def test_single_line_room_includes_times_new_roman(tmp_path):
    # 審查實測：23.2 pt 粗體的「HOLLOWAY」Arial 約 136 pt、Times New Roman 約 142 pt，框寬 141.3 pt
    times = docx_common.text_width_pt("HOLLOWAY", "Times New Roman", 23.2, True)
    arial = docx_common.text_width_pt("HOLLOWAY", "Arial", 23.2, True)
    if times is None or arial is None or not arial < 141.3 < times:
        pytest.skip("伺服器上沒有 Times New Roman 與 Arial 的字寬相容字型")
    doc = name_in_sidebar_doc()
    out, data, _ = export(docx_fixed, doc, tmp_path)
    a = _anchors(xml_of(data))["name"][2]
    assert a.find(f".//{q('wps:bodyPr')}").get("wrap") == "none"
    assert int(a.find(q("wp:extent")).get("cx")) / 12700 >= times
    # 可重排：格子右邊的內距縮小，HOLLOWAY 在 Times New Roman 粗體下也放得下
    out, data, _ = export(docx_flow, doc, tmp_path)
    root = xml_of(data)
    tc = next(tc for tc in root.iter(q("w:tc")) if "HOLLOWAY" in "".join(t.text or "" for t in tc.iter(q("w:t"))))
    width = int(tc.find(f"{q('w:tcPr')}/{q('w:tcW')}").get(q("w:w"))) / 20.0
    mar = tc.find(f"{q('w:tcPr')}/{q('w:tcMar')}")
    inner = width - sum(int(mar.find(q(f"w:{s}")).get(q("w:w"))) for s in ("left", "right")) / 20.0
    assert inner >= times, (inner, times)


# ---------- 6. 可重排：單行段落不寫右縮排 ----------


def test_flow_single_line_paragraph_has_no_right_indent(tmp_path):
    one = "Production lead with five years in a mid-size bakery, focused on steady quality and simple"
    w = docx_common.text_width_pt(one, "Liberation Sans", 9.5)
    box = tb("prof", 220.0, 170.0, w - 0.5, 30.0, [  # 框寬是墨跡寬度：比前進寬度窄一點
        para("prof-1", [run(one)], lines=1, pitch=13.0),
        para("prof-2", [run("records. Trained six new bakers on the morning shift and wrote the cleaning checklist used on every line today.")],
             lines=2, pitch=13.0)])
    side = tb("side-t", 30.0, 170.0, 150.0, 14.0, [para("side-p", [run("Dough scaling")])], wrap=False)
    doc = make_doc([page(0, [side, box], [fill("side", 0.0, 122.0, 200.0, PAGE_H - 122.0)])])
    _, data, _ = export(docx_flow, doc, tmp_path)
    root = xml_of(data)
    ps = {"".join(t.text or "" for t in p.iter(q("w:t")))[:10]: p for p in root.iter(q("w:p"))}
    ind1 = ps["Production"].find(f"{q('w:pPr')}/{q('w:ind')}")
    assert ind1 is None or ind1.get(q("w:right")) is None
