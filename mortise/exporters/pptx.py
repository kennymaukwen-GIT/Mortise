"""PowerPoint 輸出器（python-pptx，MIT；東亞字型欄位等用 XML 補）。

降階對照：
  文字框 → 文字方塊（關閉自動縮放、邊距 0、拉丁／東亞／複雜文字三個字型欄位都寫）
  段落 → 段落（對齊、行距、段前段後、首行縮排）
  圖片 → 圖片；形狀 → 矩形、圓角矩形、橢圓、直線、自訂路徑
  表格 → 表格（合併儲存格、底色、框線、儲存格邊距）
  整頁底圖 → 最底層的圖片
"""

from __future__ import annotations

import io

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

_ALIGN = {Align.LEFT: PP_ALIGN.LEFT, Align.CENTER: PP_ALIGN.CENTER, Align.RIGHT: PP_ALIGN.RIGHT, Align.JUSTIFY: PP_ALIGN.JUSTIFY}


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


def _fill_paragraphs(tf, paragraphs, element_id: str, degradations: list[Degradation], fonts: set[str]) -> None:
    """把成品段落寫進一個文字框（文字方塊或表格儲存格共用）。"""
    tb_id = element_id
    for i, sp in enumerate(paragraphs):
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
                degradations.append(Degradation(element_id=tb_id, concept="文字透明度", action="以不透明顏色輸出", lossy=True))


def _background_picture(slide, path) -> None:
    """投影片背景設成一張圖（p:bg 的 blipFill，拉伸填滿）。"""
    _, rid = slide.part.get_or_add_image_part(str(path))
    cSld = slide._element.cSld
    old = cSld.find(qn("p:bg"))
    if old is not None:
        cSld.remove(old)
    bg = etree.Element(qn("p:bg"))
    bgPr = etree.SubElement(bg, qn("p:bgPr"))
    blipFill = etree.SubElement(bgPr, qn("a:blipFill"))
    blipFill.set("dpi", "0")
    blipFill.set("rotWithShape", "1")
    blip = etree.SubElement(blipFill, qn("a:blip"))
    blip.set(qn("r:embed"), rid)
    etree.SubElement(etree.SubElement(blipFill, qn("a:stretch")), qn("a:fillRect"))
    etree.SubElement(bgPr, qn("a:effectLst"))
    cSld.insert(0, bg)


def _drop_style(shape) -> None:
    """拿掉形狀的主題樣式參照（p:style）。填色與線條都已明確寫出；留著它，
    LibreOffice 會依 effectRef 畫出主題陰影，PowerPoint 則不會，兩邊看起來就不一樣。"""
    el = shape._element
    style = el.find(qn("p:style"))
    if style is not None:
        el.remove(style)


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
    _fill_paragraphs(tf, tb.paragraphs, tb.id, degradations, fonts)


def _add_page(prs, page: StyledPage, store: Store, degradations: list[Degradation], fonts: set[str], skip: frozenset = frozenset(), layout=None) -> None:
    slide = prs.slides.add_slide(layout if layout is not None else prs.slide_layouts[6])  # 空白版面
    if page.background_color is not None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(page.background_color.r, page.background_color.g, page.background_color.b)
    if page.background_image is not None:
        name = f"background-{page.page_index + 1}"
        if layout is not None:
            # 有母片時，底圖要放在投影片背景（畫在母片之下），不能是投影片上的圖片（會蓋住母片元素）
            _background_picture(slide, store.blob_path(page.background_image))
        else:
            pic = slide.shapes.add_picture(str(store.blob_path(page.background_image)), 0, 0, prs.slide_width, prs.slide_height)
            pic.name = name
        degradations.append(Degradation(element_id=name, concept="尚未轉成可編輯物件的內容", action="保留在整頁底圖中", lossy=False))
    _add_elements(slide, page, store, degradations, fonts, skip)


def _add_elements(slide, page: StyledPage, store: Store, degradations: list[Degradation], fonts: set[str], skip: frozenset = frozenset()) -> None:
    items = [(t.z, "text", t) for t in page.text_boxes] + [(i.z, "image", i) for i in page.images] + [(s.z, "shape", s) for s in page.shapes] + [(t.z, "table", t) for t in page.tables]
    for _, kind, item in sorted(items, key=lambda x: x[0]):
        if item.id in skip:
            continue  # 已經放在母片上
        b = item.box
        x, y, w, h = (Emu(pt_to_emu(v)) for v in (b.x, b.y, b.w, b.h))
        if kind == "text":
            _add_text_box(slide, item, degradations, fonts)
        elif kind == "image":
            pic = slide.shapes.add_picture(str(store.blob_path(item.image)), x, y, w, h)
            pic.name = item.id
            if b.rotation:
                pic.rotation = b.rotation
        elif kind == "table":
            _add_table(slide, item, degradations, fonts)
        elif item.geometry == "path":
            _add_path(slide, item, degradations)
        elif item.geometry == "line":
            y0, y1 = (b.y + b.h, b.y) if b.rotation else (b.y, b.y + b.h)
            line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, Emu(pt_to_emu(y0)), Emu(pt_to_emu(b.x + b.w)), Emu(pt_to_emu(y1)))
            line.name = item.id
            _drop_style(line)
            if item.stroke:
                line.line.color.rgb = RGBColor(item.stroke.r, item.stroke.g, item.stroke.b)
            line.line.width = Pt(item.stroke_width_pt or 1.0)
        else:
            geom = {"ellipse": MSO_SHAPE.OVAL, "rounded_rect": MSO_SHAPE.ROUNDED_RECTANGLE}.get(item.geometry, MSO_SHAPE.RECTANGLE)
            shape = slide.shapes.add_shape(geom, x, y, w, h)
            shape.name = item.id
            if item.geometry == "rounded_rect" and item.corner_radius_pt:
                # 圓角比例 = 半徑 ÷ 短邊（PowerPoint 的 adj 值，0 到 0.5）
                shape.adjustments[0] = max(0.0, min(0.5, item.corner_radius_pt / max(min(b.w, b.h), 1e-6)))
            shape.shadow.inherit = False  # 不套主題預設的陰影
            _drop_style(shape)
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


# ---------------------------------------------------------------- 表格

_NO_STYLE = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"  # 「無樣式、無格線」：避免套上預設的藍色表格樣式


def _ln(tag: str, border) -> etree._Element:
    el = etree.Element(qn(tag))
    if border is None:
        etree.SubElement(el, qn("a:noFill"))
        el.set("w", "0")
    else:
        el.set("w", str(pt_to_emu(border.width_pt)))
        fill = etree.SubElement(el, qn("a:solidFill"))
        etree.SubElement(fill, qn("a:srgbClr")).set("val", border.color.hex)
    return el


def _add_table(slide, table, degradations: list[Degradation], fonts: set[str]) -> None:
    b = table.box
    nr, nc = len(table.row_heights_pt), len(table.col_widths_pt)
    frame = slide.shapes.add_table(nr, nc, Emu(pt_to_emu(b.x)), Emu(pt_to_emu(b.y)), Emu(pt_to_emu(b.w)), Emu(pt_to_emu(b.h)))
    frame.name = table.id
    tbl = frame.table
    tbl.first_row = False
    tbl.horz_banding = False
    style = tbl._tbl.tblPr.find(qn("a:tableStyleId"))
    if style is None:
        style = etree.SubElement(tbl._tbl.tblPr, qn("a:tableStyleId"))
    style.text = _NO_STYLE
    for i, wpt in enumerate(table.col_widths_pt):
        tbl.columns[i].width = Emu(pt_to_emu(wpt))
    for i, hpt in enumerate(table.row_heights_pt):
        tbl.rows[i].height = Emu(pt_to_emu(hpt))
    anchors = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
    for cell in table.cells:
        target = tbl.cell(cell.row, cell.col)
        if cell.row_span > 1 or cell.col_span > 1:
            target.merge(tbl.cell(cell.row + cell.row_span - 1, cell.col + cell.col_span - 1))
        t, r, btm, l = cell.margins_pt
        target.margin_top, target.margin_right, target.margin_bottom, target.margin_left = (Emu(pt_to_emu(v)) for v in (t, r, btm, l))
        target.vertical_anchor = anchors.get(cell.v_align, MSO_ANCHOR.TOP)
        tf = target.text_frame
        tf.word_wrap = True
        if cell.paragraphs:
            _fill_paragraphs(tf, cell.paragraphs, table.id, degradations, fonts)
        if cell.fill is not None:
            target.fill.solid()
            target.fill.fore_color.rgb = RGBColor(cell.fill.r, cell.fill.g, cell.fill.b)
        else:
            target.fill.background()
        tcPr = target._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("a:lnL")) + tcPr.findall(qn("a:lnR")) + tcPr.findall(qn("a:lnT")) + tcPr.findall(qn("a:lnB")):
            tcPr.remove(old)
        top, right, bottom, left = cell.borders
        for pos, (tag, border) in enumerate((("a:lnL", left), ("a:lnR", right), ("a:lnT", top), ("a:lnB", bottom))):
            tcPr.insert(pos, _ln(tag, border))
    if any(c.number_format for c in table.cells):
        degradations.append(Degradation(element_id=table.id, concept="數字格式", action="簡報的表格沒有數字格式，以文字照原樣輸出（Excel 輸出才有數值與格式）", lossy=False))


# ---------------------------------------------------------------- 自訂路徑


def _add_path(slide, shape, degradations: list[Degradation]) -> None:
    """自訂路徑 → PowerPoint 的自訂幾何（custGeom），保留直線與三次貝茲，可在 PowerPoint 中編輯端點。"""
    b = shape.box
    sp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(pt_to_emu(b.x)), Emu(pt_to_emu(b.y)), Emu(max(1, pt_to_emu(b.w))), Emu(max(1, pt_to_emu(b.h))))
    sp.name = shape.id
    sp.shadow.inherit = False
    _drop_style(sp)
    spPr = sp._element.spPr
    prst = spPr.find(qn("a:prstGeom"))
    cust = etree.Element(qn("a:custGeom"))
    for tag in ("a:avLst", "a:gdLst", "a:ahLst", "a:cxnLst"):
        etree.SubElement(cust, qn(tag))
    rect = etree.SubElement(cust, qn("a:rect"))
    rect.set("l", "0"), rect.set("t", "0"), rect.set("r", "r"), rect.set("b", "b")
    lst = etree.SubElement(cust, qn("a:pathLst"))
    path = etree.SubElement(lst, qn("a:path"))
    path.set("w", str(max(1, pt_to_emu(b.w))))
    path.set("h", str(max(1, pt_to_emu(b.h))))
    if shape.fill is None:
        path.set("fill", "none")

    def pt_el(parent, x: float, y: float) -> None:
        e = etree.SubElement(parent, qn("a:pt"))
        e.set("x", str(pt_to_emu(x)))
        e.set("y", str(pt_to_emu(y)))

    for sg in shape.path:
        kind = int(sg[0])
        if kind == 0:
            pt_el(etree.SubElement(path, qn("a:moveTo")), sg[1], sg[2])
        elif kind == 1:
            pt_el(etree.SubElement(path, qn("a:lnTo")), sg[1], sg[2])
        elif kind == 2:
            cb = etree.SubElement(path, qn("a:cubicBezTo"))
            for i in range(3):
                pt_el(cb, sg[1 + 2 * i], sg[2 + 2 * i])
        elif kind == 3:
            etree.SubElement(path, qn("a:close"))
    spPr.replace(prst, cust)
    if shape.fill is not None:
        sp.fill.solid()
        sp.fill.fore_color.rgb = RGBColor(shape.fill.r, shape.fill.g, shape.fill.b)
        if shape.fill.a < 0.999:
            degradations.append(Degradation(element_id=shape.id, concept="填色透明度", action="以不透明顏色輸出", lossy=True))
    else:
        sp.fill.background()
    if shape.stroke is not None:
        sp.line.color.rgb = RGBColor(shape.stroke.r, shape.stroke.g, shape.stroke.b)
        sp.line.width = Pt(shape.stroke_width_pt or 1.0)
    else:
        sp.line.fill.background()


def _build_master(prs, doc: StyledDoc, store: Store, degradations: list[Degradation], fonts: set[str]):
    """把母片元素畫在「空白」版面配置上：所有投影片都套用它，改一次全部跟著改。
    做法是先畫在一張暫時的投影片上，再把物件搬到版面配置；圖片要在版面配置上重新建立關聯。"""
    if not doc.masters:
        return None, frozenset()
    master = doc.masters[0]
    layout = prs.slide_layouts[6]
    temp = prs.slides.add_slide(layout)
    page = StyledPage(page_index=-1, width_pt=doc.pages[0].width_pt, height_pt=doc.pages[0].height_pt, text_boxes=master.text_boxes, images=master.images, shapes=master.shapes)
    before = len(temp.shapes)
    sp_tree = temp.shapes._spTree
    # 只畫元素，不畫底色與底圖
    _add_elements(temp, page, store, degradations, fonts)
    moved = list(sp_tree)[2 + before:]  # 前兩個是 nvGrpSpPr 與 grpSpPr
    target = layout.shapes._spTree
    for el in moved:
        blips = el.findall(".//" + qn("a:blip"))
        for blip in blips:
            rid = blip.get(qn("r:embed"))
            image_part = temp.part.related_part(rid)
            new_rid = layout.part.relate_to(image_part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
            blip.set(qn("r:embed"), new_rid)
        sp_tree.remove(el)
        target.append(el)
    # 刪掉暫時的投影片
    sld_ids = prs.slides._sldIdLst
    last = sld_ids[-1]
    prs.part.drop_rel(last.get(qn("r:id")))
    sld_ids.remove(last)
    return layout, frozenset(master.member_ids)


def apply_adjustments(doc: StyledDoc, adjust: dict[str, dict]) -> StyledDoc:
    """套用⑦為 PowerPoint 開的渲染修正（只在這個輸出器內，成品文件本身不變）：
    width_adjust_pt 依對齊方式加寬或縮窄文字框；offset_pt 平移文字框。"""
    if not adjust:
        return doc
    pages = []
    for page in doc.pages:
        boxes = []
        for tb in page.text_boxes:
            a = adjust.get(tb.id)
            if a:
                box = tb.box
                if "width_adjust_pt" in a:
                    delta = float(a["width_adjust_pt"])
                    align = tb.paragraphs[0].align if tb.paragraphs else Align.LEFT
                    dx = 0.0 if align in (Align.LEFT, Align.JUSTIFY) else (-delta if align == Align.RIGHT else -delta / 2)
                    box = box.model_copy(update={"x": box.x + dx, "w": max(1.0, box.w + delta)})
                if "offset_pt" in a:
                    box = box.model_copy(update={"x": box.x + float(a["offset_pt"][0]), "y": box.y + float(a["offset_pt"][1])})
                tb = tb.model_copy(update={"box": box})
            boxes.append(tb)
        pages.append(page.model_copy(update={"text_boxes": boxes}))
    return doc.model_copy(update={"pages": pages})


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store, adjust: dict | None = None) -> OutputFile:
    doc = apply_adjustments(doc, adjust or {})
    prs = Presentation()
    first = doc.pages[0]
    prs.slide_width = Emu(pt_to_emu(first.width_pt))
    prs.slide_height = Emu(pt_to_emu(first.height_pt))
    degradations: list[Degradation] = []
    fonts: set[str] = set()
    notes: list[str] = []
    layout, skip = _build_master(prs, doc, store, degradations, fonts)
    for page in doc.pages:
        if abs(page.width_pt - first.width_pt) > 0.5 or abs(page.height_pt - first.height_pt) > 0.5:
            degradations.append(Degradation(element_id=f"page-{page.page_index + 1}", concept="各頁尺寸不同", action="簡報只能有一種頁面尺寸，以第 1 頁為準", lossy=True))
        _add_page(prs, page, store, degradations, fonts, skip=skip, layout=layout)
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


