"""InDesign IDML 輸出器的測試。沒有 InDesign 可以開檔驗證，所以測試做到：交付 ZIP 與 IDML 封裝的結構、mimetype、
每個 XML 合法、對照 IDML 規格的必要元素與屬性、交互參照、以及從 XML 讀回的文字、字型、字級、顏色、位置、表格、形狀、圖片。"""

from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from mortise.artifacts import Align, Box, Color, Intent, Role, StyledDoc, StyledPage
from mortise.artifacts.styled_doc import Style
from mortise.config import Config
from mortise.exporters import idml
from mortise.pipeline import Pipeline
from mortise.pipeline.store import Store
from test_ai_pdf import EN_TEXT, build_doc, para, run, tbox  # 同目錄的測試用成品文件

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PKG = idml.PKG


def with_styles(doc: StyledDoc) -> StyledDoc:
    """加一頁套用段落樣式的文字（與第 1 頁同尺寸，應套用主版）。"""
    st = Style(id="s-title", name="標題", font_family="Liberation Sans", size_pt=28.0, bold=True, italic=False, color=Color(r=10, g=20, b=30), role=Role.TITLE, usage_count=1)
    page = StyledPage(page_index=3, width_pt=595.0, height_pt=842.0, text_boxes=[tbox("p4-styled", 40, 40, 400, 40, para("st1", run("Styled title", size=28, bold=True, color=(10, 20, 30)), style="s-title"))])
    return doc.model_copy(update={"styles": [st], "pages": list(doc.pages) + [page], "theme_colors": [Color(r=1, g=2, b=3)]})


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Store:
    return Store(tmp_path_factory.mktemp("idml-store"))


@pytest.fixture(scope="module")
def doc(store) -> StyledDoc:
    return with_styles(build_doc(store))


@pytest.fixture(scope="module")
def out(doc, store):
    return idml.export(doc, "test", Config.load(), store)


@pytest.fixture(scope="module")
def pkg(out, store) -> bytes:
    return store.read_blob(out.file)


@pytest.fixture(scope="module")
def checked(pkg):
    return idml.validate_package(idml.read_idml(pkg))


@pytest.fixture(scope="module")
def trees(checked):
    return checked[1]


@pytest.fixture(scope="module")
def emap(pkg) -> dict:
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        return json.loads(z.read("mortise-map.json"))


def find_self(trees, file, self_id):
    return next(e for e in trees[file].iter() if isinstance(e.tag, str) and e.get("Self") == self_id)


def matrix(s: str):
    return [float(v) for v in s.split()]


def apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def page_corners(trees, emap, element_id, page_w, page_h):
    """從 XML 讀回物件四角的頁面座標（頁面左上原點、y 向下）。"""
    info = emap[element_id]
    e = find_self(trees, info["spread"], info["self"])
    m = matrix(e.get("ItemTransform"))
    pts = []
    for pp in e.iter("PathPointType"):
        x, y = (float(v) for v in pp.get("Anchor").split())
        sx, sy = apply(m, x, y)
        pts.append((sx + page_w / 2, sy + page_h / 2))
    return e, pts


def story_of(trees, emap, element_id):
    return trees[emap[element_id]["story"]].find("Story")


# ---------------------------------------------------------------- 封裝結構


def test_outer_zip_and_links(pkg, emap, doc):
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        names = z.namelist()
    assert "test.idml" in names and "mortise-map.json" in names
    assert len([n for n in names if n.startswith("Links/")]) == 2  # 第 1 頁的圖片 + 第 3 頁的底圖
    for pg in doc.pages:
        for tb in pg.text_boxes:
            assert tb.id in emap


def test_mimetype_first_and_stored(pkg):
    z = zipfile.ZipFile(io.BytesIO(idml.read_idml(pkg)))
    first = z.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/vnd.adobe.indesign-idml-package"
    raw = idml.read_idml(pkg)
    assert raw[30:38] == b"mimetype"  # 本地檔頭之後緊接著檔名（ZIP 規格；讀取器靠這個辨認類型）


def test_every_xml_valid_and_references_resolve(checked, out):
    problems, trees = checked
    assert problems == []
    assert out.selfcheck_ok
    assert any("未經 InDesign 開啟驗證" in n for n in out.selfcheck_notes)
    assert all(isinstance(t, etree._Element) for t in trees.values())


def test_required_elements_per_spec(trees, doc):
    dm = trees["designmap.xml"]
    assert dm.tag == "Document" and dm.get("DOMVersion") and dm.get("Self") == "d"
    tags = [e.tag.split("}")[1] for e in dm if isinstance(e.tag, str) and e.tag.startswith(f"{{{PKG}}}")]
    for need in ("Graphic", "Fonts", "Styles", "Preferences", "Tags", "MasterSpread", "BackingStory"):
        assert need in tags
    assert tags.count("Spread") == 4 and tags.count("Story") == len(dm.get("StoryList").split())
    assert [e.get("Name") for e in dm.findall("Layer")] == ["內容", "背景"]
    assert dm.find("Section").get("Length") == "4"
    root = trees["META-INF/container.xml"]
    assert root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path") == "designmap.xml"
    g = trees["Resources/Graphic.xml"]
    selfs = {e.get("Self") for e in g}
    assert {"Color/Black", "Color/Paper", "Color/Registration", "Swatch/None", "StrokeStyle/$ID/Solid"} <= selfs
    assert "Color/R=1 G=2 B=3" in selfs  # 主題色寫成色票
    s = trees["Resources/Styles.xml"]
    for grp in ("RootCharacterStyleGroup", "RootParagraphStyleGroup", "RootCellStyleGroup", "RootTableStyleGroup", "RootObjectStyleGroup"):
        assert s.find(grp) is not None
    for sp_file, page in zip([e.get("src") for e in dm.findall(f"{{{PKG}}}Spread")], doc.pages):
        spread = trees[sp_file].find("Spread")
        pages = spread.findall("Page")
        assert len(pages) == 1 and spread.get("PageCount") == "1"
        W, H = page.width_pt, page.height_pt
        assert matrix(pages[0].get("GeometricBounds")) == pytest.approx([0, 0, H, W])
        assert matrix(pages[0].get("ItemTransform")) == pytest.approx([1, 0, 0, 1, -W / 2, -H / 2])
    pref = trees["Resources/Preferences.xml"].find("DocumentPreference")
    assert pref.get("FacingPages") == "false" and pref.get("PagesPerDocument") == "4"


def test_master_only_for_same_size_pages(trees):
    dm = trees["designmap.xml"]
    master = trees[dm.find(f"{{{PKG}}}MasterSpread").get("src")].find("MasterSpread").get("Self")
    applied = [trees[e.get("src")].find("Spread/Page").get("AppliedMaster") for e in dm.findall(f"{{{PKG}}}Spread")]
    assert applied == [master, "n", "n", master]


# ---------------------------------------------------------------- 文字：位置、字型、字級、顏色、段落


def test_text_frame_positions_unrotated(trees, emap, doc):
    page = doc.pages[0]
    for tb in page.text_boxes:
        e, pts = page_corners(trees, emap, tb.id, page.width_pt, page.height_pt)
        assert e.tag == "TextFrame"
        b = tb.box
        expect = [(b.x, b.y), (b.x, b.y + b.h), (b.x + b.w, b.y + b.h), (b.x + b.w, b.y)]
        for (x, y), (ex, ey) in zip(pts, expect):
            assert abs(x - ex) < 0.01 and abs(y - ey) < 0.01


def test_text_frame_rotated(trees, emap, doc):
    page = doc.pages[1]
    tb = page.text_boxes[0]
    e, pts = page_corners(trees, emap, tb.id, page.width_pt, page.height_pt)
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    assert abs(cx - (tb.box.x + tb.box.w / 2)) < 0.01 and abs(cy - (tb.box.y + tb.box.h / 2)) < 0.01
    tl, tr = pts[0], pts[3]
    assert math.degrees(math.atan2(tr[1] - tl[1], tr[0] - tl[0])) == pytest.approx(30.0, abs=0.01)  # y 向下：順時針 30 度
    assert math.hypot(tr[0] - tl[0], tr[1] - tl[1]) == pytest.approx(tb.box.w, abs=0.01)


def test_story_text_fonts_sizes_colors(trees, emap, doc, out):
    graphic = {e.get("Self"): e for e in trees["Resources/Graphic.xml"]}
    for page in doc.pages:
        for tb in page.text_boxes:
            story = story_of(trees, emap, tb.id)
            csrs = list(story.iter("CharacterStyleRange"))
            runs = [r for sp in tb.paragraphs for r in sp.runs]
            assert len(csrs) == len(runs)
            for csr, r in zip(csrs, runs):
                assert "".join(c.text or "" for c in csr.iter("Content")) == r.text
                assert float(csr.get("PointSize")) == pytest.approx(r.size_pt.value)
                assert csr.find("Properties/AppliedFont").text == r.font.family.value  # 推論的字型名稱，不是驗證字型
                assert csr.get("FontStyle") == {(False, False): "Regular", (True, False): "Bold", (False, True): "Italic", (True, True): "Bold Italic"}[(r.bold, r.italic)]
                col = graphic[csr.get("FillColor")]
                assert col.get("Space") == "RGB" and col.get("ColorValue") == f"{r.color.r} {r.color.g} {r.color.b}"
            assert len(list(story.iter("Br"))) == len(tb.paragraphs) - 1
    # 中文的推論字型（系統沒有）照寫在 AppliedFont；Fonts.xml 列出並標示未安裝
    mixed = story_of(trees, emap, "p1-mixed")
    assert [c.find("Properties/AppliedFont").text for c in mixed.iter("CharacterStyleRange")] == ["Liberation Sans", "Microsoft JhengHei", "Liberation Serif"]
    fams = {f.get("Name"): f for f in trees["Resources/Fonts.xml"].iter("FontFamily")}
    assert set(fams) == set(out.fonts_used) and "Microsoft JhengHei" in fams
    assert fams["Microsoft JhengHei"].find("Font").get("Status") == "NotAvailable"
    assert fams["Liberation Sans"].find("Font").get("Status") == "Installed"


def test_paragraph_attributes_leading_and_styles(trees, emap):
    en = story_of(trees, emap, "p1-en")
    psr = en.find("ParagraphStyleRange")
    assert psr.get("Justification") == "LeftJustified"
    assert en.find(".//Leading").text == "15" and en.find(".//Leading").get("type") == "unit"
    two = story_of(trees, emap, "p1-two")
    assert [p.get("SpaceAfter") for p in two.findall("ParagraphStyleRange")] == ["6", "0"]
    assert story_of(trees, emap, "p1-center").find("ParagraphStyleRange").get("Justification") == "CenterAlign"
    assert story_of(trees, emap, "p1-right").find("ParagraphStyleRange").get("Justification") == "RightAlign"
    styles = {e.get("Self"): e for e in trees["Resources/Styles.xml"].iter("ParagraphStyle")}
    assert "ParagraphStyle/標題" in styles
    st = styles["ParagraphStyle/標題"]
    assert st.get("PointSize") == "28" and st.get("FontStyle") == "Bold" and st.find("Properties/AppliedFont").text == "Liberation Sans"
    assert story_of(trees, emap, "p4-styled").find("ParagraphStyleRange").get("AppliedParagraphStyle") == "ParagraphStyle/標題"


def test_frame_preferences(trees, emap):
    single = find_self(trees, emap["p1-latin"]["spread"], emap["p1-latin"]["self"]).find("TextFramePreference")
    assert single.get("AutoSizingType") == "WidthOnly" and single.get("UseNoLineBreaksForAutoSizing") == "true"
    assert single.get("FirstBaselineOffset") == "FixedHeight"
    asc = idml._ascent_ratio("Liberation Sans")
    assert float(single.get("MinimumFirstBaselineOffset")) == pytest.approx(24 * asc, abs=0.01)
    wrapped = find_self(trees, emap["p1-en"]["spread"], emap["p1-en"]["self"]).find("TextFramePreference")
    assert wrapped.get("AutoSizingType") is None
    assert [li.text for li in wrapped.find("Properties/InsetSpacing")] == ["0"] * 4


# ---------------------------------------------------------------- 表格、形狀、圖片、底圖


def test_table_rows_cols_merges_text(trees, emap):
    story = story_of(trees, emap, "p001-table")
    t = story.find(".//Table")
    assert int(t.get("HeaderRowCount")) + int(t.get("BodyRowCount")) == 3 and t.get("HeaderRowCount") == "1" and t.get("ColumnCount") == "3"
    assert [float(r.get("SingleRowHeight")) for r in t.findall("Row")] == [30.0, 30.0, 30.0]
    assert [float(c.get("SingleColumnWidth")) for c in t.findall("Column")] == [60.0, 80.0, 100.0]
    cells = {c.get("Name"): c for c in t.findall("Cell")}
    assert set(cells) == {"0:0", "2:0", "0:1", "1:1", "2:1", "1:2"}  # 欄:列；被合併蓋住的儲存格不寫
    assert cells["0:0"].get("ColumnSpan") == "2" and cells["0:1"].get("RowSpan") == "2" and cells["1:2"].get("ColumnSpan") == "2"
    text = lambda c: "".join(x.text or "" for x in c.iter("Content"))  # noqa: E731
    assert text(cells["0:0"]) == "合併標題" and text(cells["2:1"]) == "25%" and text(cells["1:2"]) == "Bottom"
    assert cells["0:0"].get("FillColor") == "Color/R=31 G=58 B=95" and cells["0:0"].get("VerticalJustification") == "CenterAlign"
    assert cells["2:1"].get("TopEdgeStrokeWeight") == "2" and cells["2:1"].get("TopEdgeStrokeColor") == "Color/R=255 G=0 B=0"
    assert [cells["0:1"].get(k) for k in ("TopInset", "RightInset", "BottomInset", "LeftInset")] == ["2", "3", "2", "3"]
    assert cells["0:0"].find("ParagraphStyleRange").get("Justification") == "CenterAlign"
    assert emap["p001-table"]["table_self"] == t.get("Self")


def test_shapes_geometry(trees, emap, doc):
    page = doc.pages[0]
    rnd, _ = page_corners(trees, emap, "p1-round", page.width_pt, page.height_pt)
    assert rnd.tag == "Rectangle" and rnd.get("TopLeftCornerOption") == "RoundedCorner" and rnd.get("TopLeftCornerRadius") == "20"
    oval, pts = page_corners(trees, emap, "p1-oval", page.width_pt, page.height_pt)
    assert oval.tag == "Oval" and len(pts) == 4
    assert {(round(x, 3), round(y, 3)) for x, y in pts} == {(290.0, 450.0), (340.0, 480.0), (390.0, 450.0), (340.0, 420.0)}
    assert all(pp.get("LeftDirection") != pp.get("Anchor") for pp in oval.iter("PathPointType"))
    line, pts = page_corners(trees, emap, "p1-line", page.width_pt, page.height_pt)
    assert line.tag == "GraphicLine" and line.find(".//GeometryPathType").get("PathOpen") == "true"
    assert pts == [pytest.approx((50.0, 500.0)), pytest.approx((250.0, 500.0))]
    assert line.get("StrokeWeight") == "3" and line.get("StrokeColor") == "Color/R=255 G=0 B=0"
    poly, pts = page_corners(trees, emap, "p1-path", page.width_pt, page.height_pt)
    assert poly.tag == "Polygon" and poly.find(".//GeometryPathType").get("PathOpen") == "false"
    assert pts[0] == pytest.approx((410.0, 520.0)) and pts[2] == pytest.approx((460.0, 420.0))
    handles = [pp for pp in poly.iter("PathPointType") if pp.get("RightDirection") != pp.get("Anchor")]
    assert handles  # 三次貝茲的控制點寫進 Left/RightDirection
    assert poly.find("FillTransparencySetting/BlendingSetting").get("Opacity") == "80"


def test_image_link_and_scale(trees, emap, pkg):
    info = emap["p1-img"]
    rect = find_self(trees, info["spread"], info["self"])
    img = rect.find("Image")
    gb = img.find("Properties/GraphicBounds")
    assert (gb.get("Right"), gb.get("Bottom")) == ("40", "30")
    assert matrix(img.get("ItemTransform")) == pytest.approx([2, 0, 0, 2, -40, -30])
    uri = img.find("Link").get("LinkResourceURI")
    assert uri == f"file:{info['link']}"
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        assert info["link"] in z.namelist()


def test_background_layer(trees, emap):
    dm = trees["designmap.xml"]
    bg_layer = dm.findall("Layer")[1].get("Self")
    color = emap["background-color-2"]
    e = find_self(trees, color["spread"], color["self"])
    assert e.get("ItemLayer") == bg_layer and e.get("FillColor") == "Color/R=20 G=30 B=48"
    spread = trees[color["spread"]].find("Spread")
    items = [c for c in spread if c.tag in ("Rectangle", "TextFrame")]
    assert items[0] is e  # 底色在最底下
    image = emap["background-3"]
    assert find_self(trees, image["spread"], image["self"]).get("ItemLayer") == bg_layer


def test_degradations(out):
    concepts = {d.concept: d for d in out.degradations}
    assert concepts["文字透明度"].lossy and concepts["數字格式與公式"].lossy
    assert "字型內嵌" in concepts and "Microsoft JhengHei" in concepts["字型內嵌"].action
    assert "閱讀順序" in concepts


def test_story_list_follows_reading_order(trees, emap, doc):
    order = trees["designmap.xml"].get("StoryList").split()
    first_page = [emap[i]["story_self"] for i in doc.pages[0].reading_order]
    assert [s for s in order if s in first_page] == first_page


# ---------------------------------------------------------------- 真實流水線的成品文件


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    p = Pipeline(tmp_path_factory.mktemp("idml-job"))
    p.start(FIXTURES / "slide_basic.pdf", "slide_basic", Intent())
    p.run()
    doc = p.store.load("s5/styled_doc", StyledDoc)
    return doc, idml.export(doc, "slide_basic", Config.load(), p.store), p.store


def test_real_fixture_reads_back(real):
    doc, o, st = real
    assert o.selfcheck_ok, o.selfcheck_notes
    pkg = st.read_blob(o.file)
    problems, trees = idml.validate_package(idml.read_idml(pkg))
    assert problems == []
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        emap = json.loads(z.read("mortise-map.json"))
    page = doc.pages[0]
    worst = 0.0
    for tb in page.text_boxes:
        e, pts = page_corners(trees, emap, tb.id, page.width_pt, page.height_pt)
        worst = max(worst, abs(pts[0][0] - tb.box.x), abs(pts[0][1] - tb.box.y), abs(pts[2][0] - tb.box.x - tb.box.w), abs(pts[2][1] - tb.box.y - tb.box.h))
        story = story_of(trees, emap, tb.id)
        got = "".join(c.text or "" for c in story.iter("Content"))
        assert got == "".join(r.text for sp in tb.paragraphs for r in sp.runs)
        assert [c.find("Properties/AppliedFont").text for c in story.iter("CharacterStyleRange")] == [r.font.family.value for sp in tb.paragraphs for r in sp.runs]
    assert worst < 0.01
    assert sum(1 for n in trees if n.startswith("Spreads/")) == len(doc.pages)
