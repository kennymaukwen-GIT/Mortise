"""可編輯 PDF 輸出器的測試：文字是真文字（Type0／Identity-H、ToUnicode、一段一個文字物件、每行一個字串）、
字型內嵌（子集、整個、CFF）、向量圖（路徑、填色規則、筆畫、漸層）、結構標記、中繼資料與降級紀錄、逐行對位。
全部用 pikepdf 讀檔案結構、用 PDFium（回讀驗證 verify/pdf_target.py）讀回文字與物件並重新畫出來比對。"""

from __future__ import annotations

import copy
import io
import json
import re
from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from mortise.artifacts import (
    Align, Border, Box, Color, FontChoice, LineSpacing, LineSpacingMode, OutputFormat, Provenance, Role, StyledCell, StyledDoc, StyledImage,
    StyledMaster, StyledPage, StyledParagraph, StyledRun, StyledShape, StyledTable, StyledTextBox, TargetMode, measured,
)
from mortise.artifacts.element_list import Gradient, GradientStop, VectorPrimitive
from mortise.artifacts.styled_doc import StyledVector, StyledVectorPath
from mortise.config import Config
from mortise.exporters import ai_pdf, pdf_editable
from mortise.pipeline.store import Store
from mortise.tools import pdfium as mp
from mortise.verify import pdf_target

W = "test@1"
INTER = Path("/usr/share/fonts/opentype/inter/Inter-Regular.otf")


# ---------------------------------------------------------------- 建構測試用成品文件


def run(text, family="Liberation Sans", size=12.0, bold=False, italic=False, color=(0, 0, 0), ea=False, a=1.0, verify=None):
    font = FontChoice(family=measured(family, 0.9, Provenance.INFERRED, W), verification_family=verify or family, is_substituted=verify is not None and verify != family, east_asian=ea)
    return StyledRun(text=text, font=font, size_pt=measured(float(size), 0.9, Provenance.MEASURED, W), bold=bold, italic=italic, color=Color(r=color[0], g=color[1], b=color[2], a=a))


def zh(text, size=12.0, **kw):
    return run(text, "WenQuanYi Zen Hei", size, ea=True, **kw)


def para(pid, *runs, align=Align.LEFT, lines=1, exact=None, role=Role.BODY, after=0.0):
    ls = measured(LineSpacing(mode=LineSpacingMode.EXACT, value=exact), 0.8, Provenance.MEASURED, W) if exact else None
    return StyledParagraph(id=pid, runs=list(runs), align=align, source_line_count=lines, line_spacing=ls, role=role, space_after_pt=after)


def tbox(tid, x, y, w, h, *paras, z=1, rot=0.0, wrap=False):
    return StyledTextBox(id=tid, box=Box(x=x, y=y, w=w, h=h, rotation=rot), z=z, paragraphs=list(paras), wrap=wrap)


def png_blob(store: Store, color=(200, 30, 30, 255), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return store.put_blob(buf.getvalue(), "image/png", "png")


def doc_of(*pages, masters=()) -> StyledDoc:
    return StyledDoc(target_mode=measured(TargetMode.CROSS_SOURCE, 0.5, Provenance.INFERRED, W), source_app=measured("unknown", 0.0, Provenance.INFERRED, W),
                     pages=list(pages), masters=list(masters))


def config(**export) -> Config:
    cfg = Config(copy.deepcopy(Config.load().data))
    cfg.data["export"].update(export)
    return cfg


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path)


def export(doc, store, cfg=None, adjust=None):
    out = pdf_editable.export(doc, "sample", cfg or config(), store, adjust=adjust)
    return out, store.blob_path(out.file)


def ops(path, page=0) -> list[tuple[str, list]]:
    """內容串流的運算（運算元名稱、運算元）。"""
    with pikepdf.open(path) as pdf:
        return [(str(op), list(operands)) for operands, op in pikepdf.parse_content_stream(pdf.pages[page])]


def text_of(path, page=0) -> str:
    d = pdfium.PdfDocument(str(path))
    tp = d[page].get_textpage()
    return re.sub(r"\s+", "", tp.get_text_range())


def render(path, page=0, scale=2.0) -> np.ndarray:
    d = pdfium.PdfDocument(str(path))
    return np.asarray(d[page].render(scale=scale).to_pil().convert("RGB")).astype(int)


def px(img: np.ndarray, x: float, y: float, scale=2.0):
    return tuple(int(v) for v in img[int(round(y * scale)), int(round(x * scale))])


def near(a, b, tol=12) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def verify(out, doc, store, cfg=None):
    """沒有來源頁面時也能跑回讀驗證（不比相似度、不與來源比對）。"""
    return pdf_target.verify(out, [], [], None, doc, cfg or config(), store)


def errors(report):
    return [(i.code, i.message) for pg in report.pages for i in pg.issues if i.severity.value == "error"]


CJK = "".join(chr(0x4E00 + 7 * i) for i in range(320))  # 320 個不同的中文字（超過 8 位元字型的 256 個字形上限）


# ---------------------------------------------------------------- 文字與字型


def test_cjk_text_is_one_type0_font_and_one_string_per_line(store):
    page = StyledPage(page_index=0, width_pt=500, height_pt=500, text_boxes=[tbox("tb", 40, 40, 404, 400, para("p1", zh(CJK, 20), lines=16, exact=24.0), wrap=True)])
    doc = doc_of(page)
    out, path = export(doc, store)
    assert out.selfcheck_ok, out.selfcheck_notes
    assert out.format == OutputFormat.PDF and out.fonts_used == ["WenQuanYi Zen Hei"]
    with pikepdf.open(path) as pdf:
        fonts = dict(pdf.pages[0].obj.Resources.Font)
        assert len(fonts) == 1  # 320 個不同的字仍是一個字型物件（ReportLab 的 8 位元字型會拆成兩個子集）
        f = next(iter(fonts.values()))
        assert f.Subtype == "/Type0" and f.Encoding == "/Identity-H" and "/ToUnicode" in f
        cid = f.DescendantFonts[0]
        assert cid.Subtype == "/CIDFontType2" and cid.CIDToGIDMap == "/Identity"
        assert "/FontFile2" in cid.FontDescriptor
        assert re.fullmatch(r"/[A-Z]{6}\+WenQuanYiZenHei", str(f.BaseFont))  # 子集前綴加字型檔自己的 PostScript 名稱，沒有 -0 後綴
        assert len(bytes(cid.FontDescriptor.FontFile2.read_bytes())) < 400_000  # 只內嵌用到的字形
    shows = [o for o in ops(path) if o[0] in ("Tj", "TJ")]
    assert len(shows) == 16 and all(o[0] == "Tj" for o in shows)  # 每行一個字串，行內不切換字型
    assert sum(1 for o in ops(path) if o[0] == "Tf") == 1
    assert text_of(path) == CJK  # 複製出來是正確的 Unicode
    report = verify(out, doc, store)
    assert report.ok and report.pages[0].text_match_ratio == 1.0 and report.pages[0].position_error_pt < 0.01, errors(report)


def test_paragraph_is_one_text_object_with_relative_line_moves(store):
    p1 = para("p1", zh("第一段的內文會折成好幾行，用來確認整段寫成同一個文字物件而不是每行各一個。", 14), lines=3, exact=20.0, after=8.0)
    p2 = para("p2", run("Second paragraph stays in its own text object and wraps as well.", size=14), lines=2, exact=20.0)
    page = StyledPage(page_index=0, width_pt=400, height_pt=300, text_boxes=[tbox("tb", 30, 30, 240, 200, p1, p2, wrap=True)])
    out, path = export(doc_of(page), store)
    names = [o[0] for o in ops(path)]
    assert names.count("BT") == 2 and names.count("Tm") == 2  # 一段一個文字物件，各只有開頭一次文字矩陣
    assert names.count("T*") == 3 and names.count("Td") == 0  # 行與行用 TL 加 T*
    assert names.count("Tj") == 5 and names.count("TJ") == 0
    cfg = config(pdf_text_object="line")
    out2, path2 = export(doc_of(page), store, cfg)
    names2 = [o[0] for o in ops(path2)]
    assert names2.count("BT") == 5 and names2.count("Tm") == 5 and names2.count("T*") == 0  # 設定成一行一個文字物件
    assert text_of(path) == text_of(path2)
    assert any("一段一個" in d.action for d in out.degradations if d.concept == "段落與文字框")


def test_centered_lines_use_relative_moves_and_mixed_fonts_stay_in_one_object(store):
    p = para("p", run("Mortise ", size=16, bold=True), zh("季度報告與", 16), run("Review of the whole quarter", size=16), align=Align.CENTER, lines=2, exact=22.0)
    page = StyledPage(page_index=0, width_pt=400, height_pt=200, text_boxes=[tbox("tb", 20, 20, 260, 80, p, wrap=True)])
    doc = doc_of(page)
    out, path = export(doc, store)
    names = [o[0] for o in ops(path)]
    assert names.count("BT") == 1 and names.count("Tm") == 1 and names.count("Td") == 1  # 置中的兩行起點不同：用 Td 相對位移，不重下 Tm
    assert sorted(out.fonts_used) == ["Liberation Sans", "WenQuanYi Zen Hei"]
    report = verify(out, doc, store)
    assert report.ok and report.pages[0].position_error_pt < 0.01, errors(report)


def test_justify_uses_space_shifts_or_uniform_char_spacing(store):
    text = "The quick brown fox jumps over the lazy dog again and again and again"
    p = para("p", run(text, size=12), align=Align.JUSTIFY, lines=3, exact=16.0)
    page = StyledPage(page_index=0, width_pt=300, height_pt=200, text_boxes=[tbox("tb", 20, 20, 180, 80, p, wrap=True)])
    doc = doc_of(page)
    out, path = export(doc, store)
    names = [o[0] for o in ops(path)]
    assert names.count("TJ") == 2 and names.count("Tj") == 1 and names.count("Tc") == 0  # 前兩行撐開空白，末行靠左
    out2, path2 = export(doc, store, config(pdf_justify_word_space=False))
    names2 = [o[0] for o in ops(path2)]
    assert names2.count("TJ") == 0 and names2.count("Tc") >= 1  # 完全不用 TJ，改整行一致的字距
    for o, pth in ((out, path), (out2, path2)):
        assert text_of(pth) == re.sub(r"\s+", "", text)
    r1, r2 = verify(out, doc, store), verify(out2, doc, store, config(pdf_justify_word_space=False))
    assert r1.ok and r2.ok and r1.pages[0].position_error_pt < 0.01 and r2.pages[0].position_error_pt < 0.01


def test_substitute_font_keeps_its_own_name(store):
    p = para("p", run("微軟正黑體的字", "Microsoft JhengHei", 18, ea=True, verify="WenQuanYi Zen Hei"))
    page = StyledPage(page_index=0, width_pt=300, height_pt=100, text_boxes=[tbox("tb", 20, 20, 200, 30, p)])
    out, path = export(doc_of(page), store)
    assert out.fonts_used == ["WenQuanYi Zen Hei"]  # 照實列出內嵌的字型
    with pikepdf.open(path) as pdf:
        base = str(next(iter(pdf.pages[0].obj.Resources.Font.values())).BaseFont)
        assert "WenQuanYiZenHei" in base and "JhengHei" not in base  # 不把替代字型冒名成推論的字型
        assert json.loads(str(pdf.docinfo["/MortiseFonts"])) == {"Microsoft JhengHei": "WenQuanYi Zen Hei"}
    assert any(d.concept == "字型" and "Microsoft JhengHei" in d.action and d.lossy for d in out.degradations)
    assert any("Microsoft JhengHei → WenQuanYi Zen Hei" in n for n in out.selfcheck_notes)


def test_full_embedding_option(store):
    page = StyledPage(page_index=0, width_pt=300, height_pt=100, text_boxes=[tbox("tb", 20, 20, 200, 30, para("p", run("Full embed", size=18)))])
    _, sub = export(doc_of(page), store)
    _, full = export(doc_of(page), store, config(pdf_font_embed="full"))
    with pikepdf.open(sub) as a, pikepdf.open(full) as b:
        fa, fb = (next(iter(x.pages[0].obj.Resources.Font.values())) for x in (a, b))
        assert re.fullmatch(r"/[A-Z]{6}\+LiberationSans", str(fa.BaseFont)) and str(fb.BaseFont) == "/LiberationSans"
        size = lambda f: len(f.DescendantFonts[0].FontDescriptor.FontFile2.read_bytes())  # noqa: E731
        assert size(fb) > 10 * size(fa)
    assert text_of(full) == "Fullembed"


@pytest.mark.skipif(not INTER.exists(), reason="需要 Inter（CFF 外框的 OpenType 字型）")
def test_cff_font_is_embedded_as_cid_font(store):
    page = StyledPage(page_index=0, width_pt=400, height_pt=100, text_boxes=[tbox("tb", 20, 20, 300, 30, para("p", run("CFF outlines: Inter 0123", "Inter", 20)))])
    doc = doc_of(page)
    out, path = export(doc, store)
    assert out.selfcheck_ok and out.fonts_used == ["Inter"]  # ReportLab 不吃 CFF；這裡不必退回替代字型
    assert not any("CFF" in d.concept for d in out.degradations)
    with pikepdf.open(path) as pdf:
        f = next(iter(pdf.pages[0].obj.Resources.Font.values()))
        cid = f.DescendantFonts[0]
        assert cid.Subtype == "/CIDFontType0" and "/FontFile3" in cid.FontDescriptor and cid.FontDescriptor.FontFile3.Subtype == "/OpenType"
    assert text_of(path) == "CFFoutlines:Inter0123"
    report = verify(out, doc, store)
    assert report.ok and report.pages[0].position_error_pt < 0.01, errors(report)
    img = render(path)
    assert (img[40:100, 40:600].min(axis=2) < 80).sum() > 500  # 字真的畫出來了


def test_characters_outside_the_basic_plane_round_trip(store):
    text = "擴充字\U00020547尾"  # 文泉驛正黑有這個擴充 B 區的字；ToUnicode 要寫成代理對
    page = StyledPage(page_index=0, width_pt=300, height_pt=100, text_boxes=[tbox("tb", 20, 20, 200, 30, para("p", zh(text, 18)))])
    doc = doc_of(page)
    out, path = export(doc, store)
    assert out.selfcheck_ok and text_of(path) == text
    report = verify(out, doc, store)
    assert report.ok and report.pages[0].text_match_ratio == 1.0, errors(report)


def test_denied_font_is_not_embedded(store):
    page = StyledPage(page_index=0, width_pt=300, height_pt=100, text_boxes=[tbox("tb", 20, 20, 200, 30, para("p", run("• bullet", "Unifont", 18)))])
    out, path = export(doc_of(page), store)
    assert "Unifont" not in out.fonts_used and out.fonts_used  # 列在 pdf_font_deny_prefixes：不內嵌，改用後備字型
    assert any(d.concept == "字型的嵌入許可" and "Unifont" in d.action for d in out.degradations) or not Path("/usr/share/fonts/opentype/unifont/unifont.otf").exists()
    assert out.selfcheck_ok and text_of(path) == "•bullet"
    with pikepdf.open(path) as pdf:
        assert all("Unifont" not in str(f.BaseFont) for f in pdf.pages[0].obj.Resources.Font.values())


def test_missing_glyph_keeps_the_real_text_in_actual_text(store):
    page = StyledPage(page_index=0, width_pt=300, height_pt=100, text_boxes=[tbox("tb", 20, 20, 200, 30, para("p", run("A\ue000B", size=18)))])
    out, path = export(doc_of(page), store)
    spans = [o for o in ops(path) if o[0] == "BDC" and str(o[1][0]) == "/Span"]
    assert spans and "/ActualText" in spans[0][1][1]  # 所有候選字型都沒有這個字：畫的是缺字方框，另外記下真正的文字
    assert any(d.concept == "缺字" and "U+E000" in d.action for d in out.degradations)
    assert out.selfcheck_ok, out.selfcheck_notes


def test_rotated_box_and_half_transparent_text_do_not_leak_into_images(store):
    red = png_blob(store, (255, 0, 0, 255))
    page = StyledPage(
        page_index=0, width_pt=300, height_pt=200,
        text_boxes=[tbox("t", 20, 20, 200, 30, para("p", run("Half alpha", size=20, color=(0, 0, 255), a=0.5)), z=1),
                    tbox("r", 150, 120, 120, 30, para("q", run("Rotated", size=18)), z=4, rot=30.0)],
        images=[StyledImage(id="im", box=Box(x=50, y=80, w=100, h=60), z=2, image=red)],
    )
    doc = doc_of(page)
    out, path = export(doc, store)
    assert px(render(path), 100, 110) == (255, 0, 0)  # 半透明的字之後畫的圖片仍是不透明的
    report = verify(out, doc, store)
    assert report.ok and report.pages[0].text_match_ratio == 1.0 and report.pages[0].position_error_pt < 0.01, errors(report)


# ---------------------------------------------------------------- 向量圖


def square(x0, y0, x1, y1, reverse=False):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if reverse:
        pts = pts[::-1]
    return [[0, *pts[0]], [1, *pts[1]], [1, *pts[2]], [1, *pts[3]], [3]]


def circle(cx, cy, r):
    k = 0.5522847498 * r
    return [[0, cx + r, cy], [2, cx + r, cy + k, cx + k, cy + r, cx, cy + r], [2, cx - k, cy + r, cx - r, cy + k, cx - r, cy],
            [2, cx - r, cy - k, cx - k, cy - r, cx, cy - r], [2, cx + k, cy - r, cx + r, cy - k, cx + r, cy], [3]]


def stop(offset, r, g, b):
    return GradientStop(offset=offset, color=Color(r=r, g=g, b=b))


def vector_doc(store: Store) -> StyledDoc:
    """一個向量圖：線性漸層的底、奇偶規則的方框（中間有洞）、只有筆畫的折線、放射漸層的圓、非零規則的雙層方塊。
    對應的點陣圖是整張紅色：如果輸出器畫的是圖片而不是向量圖，畫面會是紅的。"""
    paths = [
        StyledVectorPath(path=square(0, 0, 240, 160), fill=Color(r=0, g=128, b=128),
                         gradient=Gradient(kind="linear", coords=[0, 0, 240, 0], stops=[stop(0, 0, 0, 255), stop(1, 0, 255, 0)]),
                         primitive=VectorPrimitive(kind="rect", cx=120, cy=80, w=240, h=160)),
        StyledVectorPath(path=square(20, 20, 100, 100) + square(45, 45, 75, 75), fill=Color(r=255, g=255, b=0), fill_rule="evenodd"),
        StyledVectorPath(path=[[0, 20, 140], [1, 60, 115], [1, 100, 140]], stroke=Color(r=0, g=0, b=0), stroke_width_pt=6.0, stroke_cap="round", stroke_join="round"),
        StyledVectorPath(path=circle(180, 50, 30), fill=Color(r=255, g=128, b=128),
                         gradient=Gradient(kind="radial", coords=[180, 50, 30], stops=[stop(0, 255, 255, 255), stop(1, 255, 0, 0)])),
        StyledVectorPath(path=square(150, 100, 220, 150) + square(170, 115, 200, 135), fill=Color(r=255, g=0, b=255), fill_rule="nonzero",
                         stroke=Color(r=0, g=0, b=0), stroke_width_pt=1.5, stroke_cap="square", stroke_join="bevel"),
    ]
    box = Box(x=40, y=60, w=240, h=160)
    vec = StyledVector(id="vec-1", image_id="im-vec", box=box, z=2, paths=paths, origin="pdf_paths", kind="logo")
    page = StyledPage(
        page_index=0, width_pt=400, height_pt=300,
        text_boxes=[tbox("title", 40, 15, 300, 30, para("p-title", zh("向量圖測試 Vector", 20), role=Role.TITLE))],
        images=[StyledImage(id="im-vec", box=box, z=2, image=png_blob(store, (255, 0, 0, 255)), vector_id="vec-1"),
                StyledImage(id="im-plain", box=Box(x=300, y=60, w=60, h=40), z=3, image=png_blob(store, (0, 0, 0, 255)))],
        vectors=[vec], reading_order=["title", "im-vec", "im-plain"],
    )
    return doc_of(page)


def check_vector_pixels(path):
    img = render(path)
    at = lambda x, y: px(img, 40 + x, 60 + y)  # noqa: E731  向量圖框內的座標
    assert near(at(5, 80), (0, 5, 250), 14) and near(at(235, 5), (0, 250, 5), 14)  # 線性漸層：左藍右綠
    assert near(at(120, 150), (0, 128, 128), 14)  # 中間是兩色各半
    assert near(at(30, 30), (255, 255, 0))  # 奇偶規則的方框
    mid = at(60, 60)
    assert mid[0] < 20 and mid[2] > 150  # 方框中間的洞露出底下的漸層（不是黃色）
    assert near(at(60, 116), (0, 0, 0), 40)  # 折線的轉角有畫出來（圓角接合）
    assert near(at(180, 50), (255, 245, 245), 20) and at(180, 76)[1] < 80  # 放射漸層：中心白、邊緣紅
    assert near(at(185, 125), (255, 0, 255))  # 非零規則：內層方塊也填滿
    assert near(px(img, 330, 80), (0, 0, 0))  # 另一張沒有向量圖的圖片照常畫


def test_vector_is_drawn_as_paths_with_gradients(store):
    doc = vector_doc(store)
    out, path = export(doc, store)
    assert out.selfcheck_ok, out.selfcheck_notes
    check_vector_pixels(path)
    pdoc = mp.open_document(str(path))
    reader = mp.PageReader(pdoc, 0)
    objects = pdf_target.read_objects(reader)
    tree = pdf_target.read_struct_tree(reader)
    reader.close()
    figure = next(n for top in tree for n in top.walk() if n.id == "vec-1")
    assert figure.type == "Figure" and figure.alt.startswith("標誌")
    mine = [o for o in objects if o.mcid in figure.mcids]
    assert [o.kind for o in mine] == ["shading", "path", "path", "shading", "path"]  # 逐條路徑，漸層是漸層物件
    donut, line, block = (o for o in mine if o.kind == "path")
    assert donut.filled and donut.fill_rule == "evenodd" and not donut.stroked
    assert line.stroked and not line.filled and line.stroke_width == pytest.approx(6.0) and (line.stroke_cap, line.stroke_join) == ("round", "round")
    assert block.filled and block.fill_rule == "nonzero" and block.stroked and block.stroke_width == pytest.approx(1.5) and (block.stroke_cap, block.stroke_join) == ("square", "bevel")
    assert donut.bounds == pytest.approx((60, 300 - 160, 140, 300 - 80), abs=0.01)
    assert sum(1 for o in objects if o.kind == "image") == 1  # 向量圖對應的那張圖片沒有畫
    report = verify(out, doc, store)
    assert report.ok and not errors(report), errors(report)
    assert any(d.concept == "向量圖的基本圖形" and not d.lossy for d in out.degradations)


def test_verify_catches_wrong_vector(store):
    doc = vector_doc(store)
    out, _ = export(doc, store)
    page = doc.pages[0]
    vec = page.vectors[0]
    fewer = vec.model_copy(update={"paths": vec.paths[:-1]})
    changed = doc.model_copy(update={"pages": [page.model_copy(update={"vectors": [fewer]})]})
    assert any(code == "vector_path_count" for code, _ in errors(verify(out, changed, store)))  # 路徑數對不上
    wider = vec.model_copy(update={"paths": [*vec.paths[:2], vec.paths[2].model_copy(update={"stroke_width_pt": 2.0}), *vec.paths[3:]]})
    changed = doc.model_copy(update={"pages": [page.model_copy(update={"vectors": [wider]})]})
    assert any(code == "vector_path_mismatch" and "筆畫寬" in msg for code, msg in errors(verify(out, changed, store)))


def test_vectors_can_be_turned_off(store):
    doc = vector_doc(store)
    cfg = config(pdf_vectors=False)
    out, path = export(doc, store, cfg)
    assert near(px(render(path), 160, 140), (255, 0, 0))  # 照常畫那張（紅色的）圖片
    assert verify(out, doc, store, cfg).ok


def test_neutral_renderer_draws_vectors_too(store):
    """ai_pdf（7A 中性渲染）也把向量圖畫成向量：描圖描壞了會直接反映在中性渲染的相似度上。"""
    doc = vector_doc(store)
    out = ai_pdf.export(doc, "neutral", config(), store)
    path = store.blob_path(out.file)
    assert out.selfcheck_ok, out.selfcheck_notes
    check_vector_pixels(path)
    reader = mp.PageReader(mp.open_document(str(path)), 0)
    kinds = [o.kind for o in pdf_target.read_objects(reader)]
    reader.close()
    assert kinds.count("shading") == 2 and kinds.count("path") == 3 and kinds.count("image") == 1


# ---------------------------------------------------------------- 結構標記


def table() -> StyledTable:
    b4 = [Border(color=Color(r=0, g=0, b=0), width_pt=0.75)] * 4
    cells = [
        StyledCell(row=0, col=0, header=True, paragraphs=[para("c00", zh("項目", 11))], borders=b4, fill=Color(r=220, g=220, b=220)),
        StyledCell(row=0, col=1, header=True, paragraphs=[para("c01", zh("金額", 11))], borders=b4, fill=Color(r=220, g=220, b=220)),
        StyledCell(row=1, col=0, paragraphs=[para("c10", zh("訂閱", 11))], borders=b4),
        StyledCell(row=1, col=1, paragraphs=[para("c11", run("1,200", size=11), align=Align.RIGHT)], borders=b4),
        StyledCell(row=2, col=0, col_span=2, paragraphs=[para("c20", zh("合計見下頁", 11), align=Align.CENTER)], borders=b4),
    ]
    return StyledTable(id="tbl", box=Box(x=40, y=330, w=200, h=75), z=6, col_widths_pt=[100.0, 100.0], row_heights_pt=[25.0, 25.0, 25.0], cells=cells, header_rows=1)


def tagged_page(store: Store, index=0) -> StyledPage:
    n = index + 1
    return StyledPage(
        page_index=index, width_pt=420, height_pt=595, background_color=Color(r=250, g=250, b=245),
        text_boxes=[
            tbox(f"body{n}", 40, 130, 340, 90,
                 para(f"b1-{n}", zh("第一段內文：這份文件用來確認結構標記的種類與閱讀順序。", 12), lines=2, exact=18.0, after=6.0),
                 para(f"b2-{n}", run("The second paragraph is written in English.", size=12), lines=1, exact=18.0), z=1, wrap=True),
            tbox(f"title{n}", 40, 40, 340, 36, para(f"t-{n}", zh("年度報告", 26, bold=True), role=Role.TITLE), z=5),
            tbox(f"sub{n}", 40, 84, 340, 24, para(f"s-{n}", zh("營運摘要", 16), role=Role.SUBTITLE), z=4),
            tbox(f"foot{n}", 40, 560, 200, 16, para(f"f-{n}", zh("內部資料", 9), role=Role.FOOTER), z=7),
            tbox(f"num{n}", 360, 560, 30, 16, para(f"n-{n}", run(str(n), size=9), role=Role.PAGE_NUMBER), z=7),
        ],
        images=[StyledImage(id=f"photo{n}", box=Box(x=260, y=240, w=120, h=80), z=3, image=png_blob(store, (30, 90, 200, 255)))],
        shapes=[StyledShape(id=f"rule{n}", box=Box(x=40, y=116, w=340, h=2), z=2, geometry="rect", fill=Color(r=200, g=0, b=0))],
        tables=[table()] if index == 0 else [],
        reading_order=[f"title{n}", f"sub{n}", f"body{n}", f"photo{n}"] + (["tbl"] if index == 0 else []) + [f"foot{n}", f"num{n}"],
    )


def shape_of(node) -> list:
    return [node.type, *[shape_of(k) for k in node.kids]] if node.kids else node.type


def test_structure_tree_follows_roles_and_reading_order(store):
    doc = doc_of(tagged_page(store))
    out, path = export(doc, store)
    assert out.selfcheck_ok, out.selfcheck_notes
    pdoc = mp.open_document(str(path))
    assert pdf_target.is_tagged(pdoc)
    reader = mp.PageReader(pdoc, 0)
    tree = pdf_target.read_struct_tree(reader)
    objects = pdf_target.read_objects(reader)
    reader.close()
    assert len(tree) == 1
    # 內容串流依圖層繪製（內文在標題之前畫），結構樹依閱讀順序：標題、副標、兩段內文、圖、表格
    assert shape_of(tree[0]) == ["Document", "H1", "H2", "P", "P", "Figure",
                                 ["Table", ["TR", ["TH", "P"], ["TH", "P"]], ["TR", ["TD", "P"], ["TD", "P"]], ["TR", ["TD", "P"]]]]
    nodes = {n.id: n for n in tree[0].walk() if n.id}
    assert [nodes[i].type for i in ("t-1", "s-1", "b1-1", "b2-1", "photo1", "tbl")] == ["H1", "H2", "P", "P", "Figure", "Table"]
    assert nodes["b2-1"].lang == "en" and not nodes["b1-1"].lang  # 與文件語言不同的段落才另外標語言
    assert nodes["photo1"].alt.startswith("圖片")
    assert "f-1" not in nodes and "n-1" not in nodes  # 頁尾與頁碼是 Artifact，不進結構樹
    first_text = next(o for o in objects if o.kind == "text")
    assert first_text.mcid == nodes["b1-1"].mcids[0]  # 最先畫的是圖層最底的內文
    assert all(o.mcid == -1 for o in objects if o.kind == "path")  # 底色、形狀、表格的框線都是 Artifact
    with pikepdf.open(path) as pdf:
        assert pdf_editable.struct_problems(pdf) == []
        assert pdf.Root.MarkInfo.Marked and str(pdf.Root.Lang) == "zh-Hant" and pdf.Root.ViewerPreferences.DisplayDocTitle
        assert str(pdf.docinfo["/Title"]) == "sample" and "/Author" not in pdf.docinfo and "Mortise" in str(pdf.docinfo["/Producer"])
        with pdf.open_metadata() as meta:
            assert meta["dc:title"] == "sample" and "pdfuaid:part" not in meta  # 沒有用 veraPDF 驗證過，不宣告 PDF/UA
        artifacts = [str(operands[1]) for operands, op in pikepdf.parse_content_stream(pdf.pages[0]) if str(op) == "BDC" and str(operands[0]) == "/Artifact"]
        assert any("/Background" in a for a in artifacts) and any("/Footer" in a for a in artifacts) and any("/Layout" in a for a in artifacts)
        th = pdf.Root.StructTreeRoot.K.K[5].K[0].K[0]
        assert th.S == "/TH" and th.A.Scope == "/Column"
        merged = pdf.Root.StructTreeRoot.K.K[5].K[2].K
        assert merged.S == "/TD" and merged.A.ColSpan == 2
    report = verify(out, doc, store)
    assert report.ok and not errors(report), errors(report)
    assert any(d.concept == "表格結構" for d in out.degradations) and any(d.concept == "閱讀順序" and not d.lossy for d in out.degradations)


def test_verify_catches_wrong_reading_order_and_roles(store):
    doc = doc_of(tagged_page(store))
    out, _ = export(doc, store)
    page = doc.pages[0]
    swapped = doc.model_copy(update={"pages": [page.model_copy(update={"reading_order": ["body1", "title1", "sub1", "photo1", "tbl", "foot1", "num1"]})]})
    codes = [i.code for pg in verify(out, swapped, store).pages for i in pg.issues]
    assert "reading_order" in codes
    boxes = [tb.model_copy(update={"paragraphs": [tb.paragraphs[0].model_copy(update={"role": Role.BODY})]}) if tb.id == "sub1" else tb for tb in page.text_boxes]
    retagged = doc.model_copy(update={"pages": [page.model_copy(update={"text_boxes": boxes})]})
    assert any(code == "struct_mismatch" for code, _ in errors(verify(out, retagged, store)))


def test_multi_page_sections_masters_and_shared_images(store):
    p1, p2 = tagged_page(store, 0), tagged_page(store, 1)
    master = StyledMaster(id="m1", text_boxes=[p1.text_boxes[3]], images=[p1.images[0]], member_ids=["foot1", "foot2", "photo1", "photo2"], page_indexes=[0, 1])
    doc = doc_of(p1, p2, masters=[master])
    out, path = export(doc, store)
    assert out.selfcheck_ok, out.selfcheck_notes
    pdoc = mp.open_document(str(path))
    trees = []
    for i in range(2):
        reader = mp.PageReader(pdoc, i)
        trees.append(pdf_target.read_struct_tree(reader))
        reader.close()
    assert [[k.type for k in t[0].kids] for t in trees] == [["Sect"], ["Sect"]]  # PDFium 逐頁給結構樹：每頁看到自己的那個 Sect
    ids = {n.id for t in trees for top in t for n in top.walk()}
    assert "photo1" not in ids and "photo2" not in ids  # 母片上的圖片每頁各畫一次，標成 Artifact
    assert {"t-1", "t-2", "tbl"} <= ids
    with pikepdf.open(path) as pdf:
        assert pdf_editable.struct_problems(pdf) == []
        assert [str(k.S) for k in pdf.Root.StructTreeRoot.K.K] == ["/Sect", "/Sect"]  # 多頁文件每頁一個 Sect
        images = [pg.obj.Resources.XObject for pg in pdf.pages]
        assert {v.objgen for v in images[0].values()} == {v.objgen for v in images[1].values()}  # 同一張圖在兩頁共用一份資料
    assert any(d.concept == "母片" and d.lossy for d in out.degradations)
    report = verify(out, doc, store)
    assert report.ok and len(report.pages) == 2 and not errors(report), errors(report)


def test_tagging_can_be_turned_off(store):
    doc = doc_of(tagged_page(store))
    cfg = config(pdf_tagged=False)
    out, path = export(doc, store, cfg)
    assert out.selfcheck_ok
    with pikepdf.open(path) as pdf:
        assert "/StructTreeRoot" not in pdf.Root and "/MarkInfo" not in pdf.Root and str(pdf.Root.Lang) == "zh-Hant"
    assert not any(o[0] == "BDC" for o in ops(path))
    assert any(d.concept == "段落角色與表格結構" for d in out.degradations)
    report = verify(out, doc, store, cfg)
    assert report.ok and any("關閉結構標記" in n for n in report.notes)


# ---------------------------------------------------------------- 逐行對位（⑦的修正單）


def test_line_fit_moves_lines_and_records_itself(store):
    p = para("p", run("Mortise ", size=20), zh("季度報告", 20), run(" Review", size=20))
    page = StyledPage(page_index=0, width_pt=500, height_pt=120, text_boxes=[tbox("tb", 30, 30, 400, 30, p)])
    doc = doc_of(page)
    base = pdf_editable.placed_lines(doc, config())[0][0]
    fit = {"p/0": [3.0, -1.5, [0.0, 0.5, 0.0], [[8, 6.0]]]}  # 行首右移 3、上移 1.5；中文那一段字距 0.5；第 8 個字（季）之前多空 6
    out, path = export(doc, store, adjust={"tb": {"line_fit": fit}})
    assert out.selfcheck_ok
    with pikepdf.open(path) as pdf:
        assert json.loads(str(pdf.docinfo["/MortiseLineFit"])) == {"tb": fit}  # 輸出檔自己記得套用了什麼
    assert pdf_target.applied_fit(str(path)) == {"tb": fit}
    moved = pdf_editable.placed_lines(doc, config(), {"tb": fit})[0][0]
    assert (moved.x - base.x, moved.y - base.y) == pytest.approx((3.0, -1.5))
    shift = [m[1] - b[1] for m, b in zip(moved.chars, base.chars)]
    assert shift[7] == pytest.approx(0.0) and shift[8] == pytest.approx(6.0) and shift[9] == pytest.approx(6.5) and shift[12] == pytest.approx(8.0)
    names = [o[0] for o in ops(path)]
    assert names.count("TJ") == 1 and names.count("Tc") == 2  # 位移寫成 TJ 的一個數字；中文那一段前後各設一次字距
    report = verify(out, doc, store)  # 回讀驗證讀檔案裡的紀錄，預期位置已算入對位
    assert report.ok and report.pages[0].position_error_pt < 0.01, errors(report)
    assert any("逐行對位" in n for n in report.notes) and any("逐行對位" in n for n in out.selfcheck_notes)
    plain, _ = export(doc, store, config(pdf_line_fit=False), adjust={"tb": {"line_fit": fit}})
    assert pdf_target.applied_fit(str(store.blob_path(plain.file))) == {}  # 設定關閉時不採用修正單
