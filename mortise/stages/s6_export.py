"""⑥ 輸出：收成品文件，交出各格式的檔案 OutputFile。

每種格式一個獨立的輸出器，登記在 EXPORTERS。新增格式只要加一個函數並登記，不必動其他階段。
輸出器自己把成品文件「降階」成該格式的物件；格式專屬的東西不回流到成品文件。

  s6.pptx   PowerPoint（python-pptx，MIT；東亞字型欄位等用 XML 補）
尚未實作：Word（兩種模式）、Excel、給 Illustrator 的 PDF、InDesign IDML、模板。
"""

from __future__ import annotations

import io
from typing import Callable

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from ..artifacts import Align, Degradation, LineSpacingMode, OutputFile, OutputFormat, StyledDoc, StyledPage, StyledTextBox
from ..config import Config
from ..pipeline.store import Store
from ..tools.units import pt_to_emu

NAME = "s6"
VERSION = "1"

_ALIGN = {Align.LEFT: PP_ALIGN.LEFT, Align.CENTER: PP_ALIGN.CENTER, Align.RIGHT: PP_ALIGN.RIGHT, Align.JUSTIFY: PP_ALIGN.JUSTIFY}


class ExportNotImplemented(Exception):
    pass


def run(doc: StyledDoc, fmt: OutputFormat, basename: str, cfg: Config, store: Store) -> OutputFile:
    exporter = EXPORTERS.get(fmt)
    if exporter is None:
        raise ExportNotImplemented(f"輸出格式 {fmt.value} 尚未實作。")
    return exporter(doc, basename, cfg, store)


# ====================================================================== PPTX


def _set_fonts(run, family: str) -> None:
    """拉丁、東亞、複雜文字三個字型欄位都寫，否則中文會落回主題字型。"""
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
        el.set("typeface", family)
    order = {qn("a:ln"): 0, qn("a:solidFill"): 1, qn("a:latin"): 5, qn("a:ea"): 6, qn("a:cs"): 7}
    children = sorted(rpr, key=lambda e: order.get(e.tag, 3))
    for ch in children:  # 子元素必須照綱要順序，否則 PowerPoint 會要求修復
        rpr.remove(ch)
    for ch in children:
        rpr.append(ch)


def _add_text_box(slide, tb: StyledTextBox, degradations: list[Degradation], fonts: set[str]) -> None:
    b = tb.box
    shape = slide.shapes.add_textbox(Emu(pt_to_emu(b.x)), Emu(pt_to_emu(b.y)), Emu(pt_to_emu(b.w)), Emu(pt_to_emu(b.h)))
    shape.name = tb.id
    if b.rotation:
        shape.rotation = b.rotation
    tf = shape.text_frame
    tf.word_wrap = tb.wrap
    tf.auto_size = MSO_AUTO_SIZE.NONE  # 不讓目標軟體自動縮放文字
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for i, sp in enumerate(tb.paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN[sp.align]
        if sp.line_spacing is not None:
            ls = sp.line_spacing.value
            p.line_spacing = Pt(ls.value) if ls.mode == LineSpacingMode.EXACT else float(ls.value)
        p.space_before = Pt(sp.space_before_pt)
        p.space_after = Pt(sp.space_after_pt)
        if sp.first_line_indent_pt:
            p._p.get_or_add_pPr().set("indent", str(pt_to_emu(sp.first_line_indent_pt)))
        for sr in sp.runs:
            r = p.add_run()
            r.text = sr.text
            f = r.font
            f.size = Pt(sr.size_pt.value)
            f.bold = sr.bold
            f.italic = sr.italic
            f.color.rgb = RGBColor(sr.color.r, sr.color.g, sr.color.b)
            _set_fonts(r, sr.font.family.value)
            fonts.add(sr.font.family.value)
            if sr.color.a < 0.999:
                degradations.append(Degradation(element_id=tb.id, concept="文字透明度", action="以不透明顏色輸出", lossy=True))


def _add_page(prs, page: StyledPage, store: Store, degradations: list[Degradation], fonts: set[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白版面
    if page.background_color is not None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(page.background_color.r, page.background_color.g, page.background_color.b)
    if page.background_image is not None:
        pic = slide.shapes.add_picture(str(store.blob_path(page.background_image)), 0, 0, prs.slide_width, prs.slide_height)
        pic.name = f"background-{page.page_index + 1}"
        degradations.append(Degradation(element_id=pic.name, concept="尚未轉成可編輯物件的內容", action="保留在整頁底圖中", lossy=False))
    items = [(t.z, "text", t) for t in page.text_boxes] + [(i.z, "image", i) for i in page.images] + [(s.z, "shape", s) for s in page.shapes]
    for _, kind, item in sorted(items, key=lambda x: x[0]):
        b = item.box
        x, y, w, h = (Emu(pt_to_emu(v)) for v in (b.x, b.y, b.w, b.h))
        if kind == "text":
            _add_text_box(slide, item, degradations, fonts)
        elif kind == "image":
            pic = slide.shapes.add_picture(str(store.blob_path(item.image)), x, y, w, h)
            pic.name = item.id
            if b.rotation:
                pic.rotation = b.rotation
        elif item.geometry == "line":
            y0, y1 = (b.y + b.h, b.y) if b.rotation else (b.y, b.y + b.h)
            line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, Emu(pt_to_emu(y0)), Emu(pt_to_emu(b.x + b.w)), Emu(pt_to_emu(y1)))
            line.name = item.id
            if item.stroke:
                line.line.color.rgb = RGBColor(item.stroke.r, item.stroke.g, item.stroke.b)
            line.line.width = Pt(item.stroke_width_pt or 1.0)
        else:
            shape = slide.shapes.add_shape(MSO_SHAPE.OVAL if item.geometry == "ellipse" else MSO_SHAPE.RECTANGLE, x, y, w, h)
            shape.name = item.id
            shape.shadow.inherit = False  # 不套主題預設的陰影
            if b.rotation:
                shape.rotation = b.rotation
            if item.fill:
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor(item.fill.r, item.fill.g, item.fill.b)
                if item.fill.a < 0.999:
                    degradations.append(Degradation(element_id=item.id, concept="填色透明度", action="以不透明顏色輸出", lossy=True))
            else:
                shape.fill.background()
            if item.stroke:
                shape.line.color.rgb = RGBColor(item.stroke.r, item.stroke.g, item.stroke.b)
                shape.line.width = Pt(item.stroke_width_pt or 1.0)
            else:
                shape.line.fill.background()


def export_pptx(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    prs = Presentation()
    first = doc.pages[0]
    prs.slide_width = Emu(pt_to_emu(first.width_pt))
    prs.slide_height = Emu(pt_to_emu(first.height_pt))
    degradations: list[Degradation] = []
    fonts: set[str] = set()
    notes: list[str] = []
    for page in doc.pages:
        if abs(page.width_pt - first.width_pt) > 0.5 or abs(page.height_pt - first.height_pt) > 0.5:
            degradations.append(Degradation(element_id=f"page-{page.page_index + 1}", concept="各頁尺寸不同", action="簡報只能有一種頁面尺寸，以第 1 頁為準", lossy=True))
        _add_page(prs, page, store, degradations, fonts)
    buf = io.BytesIO()
    prs.save(buf)
    data = buf.getvalue()
    # 檔案自檢：重新讀入，確認結構能被開啟、頁數相符
    ok = True
    try:
        again = Presentation(io.BytesIO(data))
        if len(again.slides) != len(doc.pages):
            ok = False
            notes.append("自檢：頁數與成品文件不符。")
    except Exception as e:  # pragma: no cover
        ok = False
        notes.append(f"自檢：輸出檔無法重新開啟（{e}）。")
    blob = store.put_blob(data, "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx")
    return OutputFile(format=OutputFormat.PPTX, file=blob, filename=f"{basename}.pptx", degradations=degradations, fonts_used=sorted(fonts), selfcheck_ok=ok, selfcheck_notes=notes)


EXPORTERS: dict[OutputFormat, Callable[[StyledDoc, str, Config, Store], OutputFile]] = {
    OutputFormat.PPTX: export_pptx,
}
