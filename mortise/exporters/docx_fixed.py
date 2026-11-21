"""Word 版面固定模式輸出器（DOCX_FIXED）：每個元素用絕對位置釘在頁面上，外觀最接近原件。

做法（見 docs/architecture/06-export.md 6.8）：
  - 每個 StyledPage 一節：紙張照 width_pt、height_pt，邊界與頁首頁尾距離全為 0。
  - 每頁一個「錨定段落」（1 pt 字級、固定行距 1 pt），該頁所有浮動物件都掛在它上面；
    頁與頁之間用分節符號（下一頁）隔開。
  - 浮動物件一律 wp:anchor：位置相對頁面（relativeFrom="page"），不文繞圖、允許重疊、錨點鎖定；
    圖層順序寫在 relativeHeight（依 z 由小到大遞增），整頁底色與底圖 behindDoc="1" 放在最底層。
  - 文字方塊用 DrawingML（wps:wsp，包在 mc:AlternateContent 的 Choice 裡）；VML 退路可用參數開啟。
    實測（tests/exporters/test_docx.py）：LibreOffice 24.2 讀 Choice，開或不開退路的渲染結果相同。
  - 表格預設放進文字方塊（table_container="textbox"），才能和其他物件一起排圖層；
    另一選項是浮動表格（"floating"，w:tblpPr 相對頁面）。兩者在 LibreOffice 的位置都實測過。

降階對照表（成品文件的概念 → DOCX 的物件 → 是否有損）：
  | 成品文件的概念             | DOCX 的物件（版面固定模式）                                   | 是否有損 |
  |----------------------------|--------------------------------------------------------------|----------|
  | 頁面與實際尺寸             | 每頁一節的 w:pgSz（twip），各頁尺寸可不同                      | 無損；超過 22 英吋時整頁等比縮小（有損，記錄） |
  | 頁面底色 background_color  | 整頁矩形（wps，behindDoc），因為 Word 的頁面色彩只能全文件一種 | 無損     |
  | 整頁底圖 background_image  | 整頁浮動圖片，最底層、文字後方                                 | 無損（記一筆非有損的說明） |
  | 文字框（位置、大小、旋轉） | 浮動文字方塊 wps:wsp，相對頁面定位（EMU），a:xfrm rot           | 無損     |
  | 文字框 wrap                | wps:bodyPr wrap="square"／"none"                              | 無損     |
  | 段落與字元格式             | 文字方塊內的段落，全部屬性明寫（並套具名樣式）                  | 見 docx_common |
  | 圖片                       | 浮動圖片 pic:pic，相對頁面定位，可旋轉                          | 無損     |
  | 形狀 rect、ellipse          | wps:wsp 的 prstGeom rect、ellipse                             | 無損     |
  | 形狀 rounded_rect          | prstGeom roundRect，adj = 圓角半徑 ÷ 短邊                      | 無損     |
  | 形狀 line                  | prstGeom line（方向用 flipV）                                  | 無損     |
  | 形狀 path                  | a:custGeom（moveTo、lnTo、cubicBezTo、close）                 | 無損     |
  | 填色與框線透明度           | a:alpha                                                      | 無損     |
  | 表格                       | 文字方塊內的表格（或浮動表格 tblpPr）；列高固定               | 浮動表格不能旋轉、不參與圖層（有損，記錄） |
  | 圖層順序 z                 | wp:anchor 的 relativeHeight；底圖 behindDoc                    | 無損     |
  | 閱讀順序 reading_order     | 不輸出：物件依圖層順序掛在錨定段落                              | 有損，本模式的本質 |
  | 文字跨框流動               | 不支援：每個文字框各自獨立                                     | 有損，本模式的本質 |
"""

from __future__ import annotations

from collections import Counter

from docx import Document

from ..artifacts import Align, OutputFile, OutputFormat, StyledDoc, StyledPage
from ..artifacts.common import Role
from ..config import Config
from ..pipeline.store import Store
from .docx_common import (
    MAX_PAGE_PT,
    URI_PIC,
    URI_WPS,
    Ctx,
    WordStyle,
    anchor_geometry,
    body_pr,
    build_paragraph,
    build_styles,
    build_table,
    context_fonts,
    cust_geom,
    drawing_anchor,
    drawing_run,
    el,
    emu,
    empty_paragraph,
    font_first_baseline_em,
    image_descriptor,
    line_props,
    make_ctx,
    picture_pic,
    q,
    save_bytes,
    selfcheck,
    set_sect,
    solid_fill,
    sub,
    table_texts,
    textbox_wsp,
    vml_textbox_fallback,
    xfrm,
)

DEFAULT_PARAMS = {
    "table_container": "textbox",  # 表格的容器："textbox"（放進文字方塊）或 "floating"（浮動表格 tblpPr）
    "vml_fallback": False,  # 文字方塊是否另附 VML 退路（只有 Word 2007 需要；會讓檔內文字重複一份）
    "anchor_font_pt": 1.0,  # 錨定段落的字級與固定行距（pt）
    "relative_height_base": 251658240,  # relativeHeight 的起始值（Word 自己寫的值也從這附近開始）
    "max_page_pt": MAX_PAGE_PT,  # Word 頁面邊長上限（pt）；超過時整頁等比縮小
    "textbox_autofit": True,  # 文字方塊寫 spAutoFit：目標軟體的字比較寬、行比較高時框往下長，不裁掉文字
    "baseline_compensation": True,  # 單倍行距的文字框依驗證字型的上緣量度微調 y，讓第一行基線落在「框上緣 + 字級」
    "nowrap_slack_ratio": 0.15,  # 不換行的文字方塊加寬這個比例（往對齊基準的反方向加，字的位置不變）。
    # 實測：LibreOffice 24.2 不理會 wrap="none"，字寬略大時仍會折行，折下去的那行被框高裁掉
}

MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def params_from(cfg: Config | None, defaults: dict, key: str) -> dict:
    p = dict(defaults)
    if cfg is not None:
        p.update(cfg.section("export").get(key, {}) or {})
    return p


def _shape_wsp(shape, w: float, h: float, ctx: Ctx):
    """形狀 → wps:wsp。回傳（wsp, 是否為退路寫法）。"""
    wsp = el("wps:wsp")
    sub(wsp, "wps:cNvSpPr")
    sp = sub(wsp, "wps:spPr")
    geo = shape.geometry
    if geo == "line":
        # 成品文件的直線用 rotation 記方向：0 為左上到右下，非 0 為左下到右上（同 PPTX 輸出器）
        xfrm(sp, "a:xfrm", w, h, 0.0, flip_v=bool(shape.box.rotation))
    else:
        xfrm(sp, "a:xfrm", w, h, shape.box.rotation)
    if geo == "path" and shape.path:
        if not cust_geom(sp, shape.path, w, h, ctx.scale):
            sp.remove(sp[-1])
            g = sub(sp, "a:prstGeom", {"prst": "rect"})
            sub(g, "a:avLst")
            ctx.degrade(shape.id, "自訂路徑", "路徑沒有可用的線段，以矩形輸出", True)
    else:
        prst = {"rect": "rect", "rounded_rect": "roundRect", "ellipse": "ellipse", "line": "line"}.get(geo)
        if prst is None:
            prst = "rect"
            ctx.degrade(shape.id, f"形狀 {geo}", "不認得的幾何，以矩形輸出", True)
        g = sub(sp, "a:prstGeom", {"prst": prst})
        av = sub(g, "a:avLst")
        if prst == "roundRect":
            r = (shape.corner_radius_pt or 0.0) * ctx.scale
            short = max(1e-6, min(w, h))
            sub(av, "a:gd", {"name": "adj", "fmla": f"val {min(50000, int(round(r / short * 100000)))}"})
    if shape.fill is not None and geo != "line":
        solid_fill(sp, shape.fill)
    else:
        sub(sp, "a:noFill")
    line_props(sp, shape.stroke, (shape.stroke_width_pt or 1.0) * ctx.scale if shape.stroke else None)
    body_pr(wsp)
    return wsp


def _baseline_shift(tb) -> float:
    """第一行基線應在「框上緣 + first_baseline_pt」（成品文件明寫；沒有時沿用舊約定「框上緣 + 字級」）。
    目標引擎把基線放在「框上緣 + 字型上緣量度（+ 固定行高多出的行距）」，兩者差多少就把框移多少（pt）。"""
    if not tb.paragraphs or not tb.paragraphs[0].runs:
        return 0.0
    sp = tb.paragraphs[0]
    ls = sp.line_spacing.value if sp.line_spacing else None
    size = max(r.size_pt.value for r in sp.runs)
    explicit = getattr(tb, "first_baseline_pt", None)
    exact = ls is not None and ls.mode.value == "exact"
    if ls is not None and not exact and not (ls.mode.value == "multiple" and abs(ls.value - 1.0) < 1e-6) and explicit is None:
        return 0.0
    target = explicit if explicit is not None else size  # 成品文件明寫了第一行基線的位置：以它為準
    if exact:
        # 固定行高：LibreOffice 把第一行基線放在框上緣 + 0.8 × 行高（實測 20、30、40、48 pt 都精確成立）
        return target - 0.8 * ls.value
    preds = []
    for r in sp.runs:
        em = font_first_baseline_em(r.font.verification_family or r.font.family.value)
        if em is None:
            return 0.0
        preds.append(em * r.size_pt.value)
    return target - max(preds)  # 倍數行距：第一行上方不加行距（實測 1.0、1.5 倍相同）


def _page_items(page: StyledPage) -> list[tuple[int, int, str, object]]:
    items = [(t.z, 0, "text", t) for t in page.text_boxes] + [(i.z, 1, "image", i) for i in page.images]
    items += [(s.z, 2, "shape", s) for s in page.shapes] + [(t.z, 3, "table", t) for t in page.tables]
    return sorted(items, key=lambda x: (x[0], x[1]))


def _add_page(docx_doc, page: StyledPage, ctx: Ctx, styles: dict[str, WordStyle], store: Store, params: dict,
              expect_geom: dict, expect_texts: list[str], rel: list[int]) -> tuple[list, object, float]:
    """一頁 → （錨定段落之前的區塊, 錨定段落, 比例）。rel 是全文件共用的 relativeHeight 計數器（每個物件唯一）。"""
    s = min(1.0, params["max_page_pt"] / max(page.width_pt, page.height_pt))
    ctx.scale = s
    pid = f"page-{page.page_index + 1}"
    if s < 1.0:
        ctx.degrade(pid, "頁面尺寸", f"超過 Word 上限 {params['max_page_pt'] / 72:g} 英吋，整頁等比縮小為 {s:.1%}", True)
    anchor_p = empty_paragraph(params["anchor_font_pt"])
    pre: list = []

    def place(child, uri, name, x, y, w, h, rot, behind, wps, fallback=None):
        rel[0] += 2
        a = drawing_anchor(child, uri, x=x, y=y, w=w, h=h, rot=rot, rel_height=rel[0], behind=behind, pr_id=ctx.new_id(), name=name)
        anchor_p.append(drawing_run(a, wps, fallback))
        expect_geom[name] = (emu(x), emu(y), emu(w), emu(h))

    W, H = page.width_pt * s, page.height_pt * s
    if page.background_color is not None:
        wsp = el("wps:wsp")
        sub(wsp, "wps:cNvSpPr")
        sp = sub(wsp, "wps:spPr")
        xfrm(sp, "a:xfrm", W, H)
        g = sub(sp, "a:prstGeom", {"prst": "rect"})
        sub(g, "a:avLst")
        solid_fill(sp, page.background_color)
        line_props(sp, None, None)
        body_pr(wsp)
        place(wsp, URI_WPS, f"{pid}-background-color", 0, 0, W, H, 0.0, True, True)
    if page.background_image is not None:
        rid, _ = docx_doc.part.get_or_add_image(image_descriptor(store, page.background_image))
        name = f"{pid}-background"
        pic = picture_pic(rid, W, H, 0.0, ctx._next_id, name)
        place(pic, URI_PIC, name, 0, 0, W, H, 0.0, True, False)
        ctx.degrade(name, "尚未轉成可編輯物件的內容", "保留在整頁底圖中（最底層、文字後方）", False)

    floating = params["table_container"] == "floating"
    min_table_z = min((t.z for t in page.tables), default=None) if floating else None
    for z, _, kind, item in _page_items(page):
        b = item.box
        x, y, w, h, rot = b.x * s, b.y * s, b.w * s, b.h * s, b.rotation
        # 浮動表格在文字層，不參與 relativeHeight：比它低的物件放到文字後方，才不會蓋住表格
        behind = min_table_z is not None and z < min_table_z
        if kind == "text":
            latin, ea = context_fonts(item.paragraphs, ctx)
            blocks = [build_paragraph(sp, ctx, item.id, latin, ea, styles.get(sp.style_id or "")) for sp in item.paragraphs]
            if not item.wrap and params["nowrap_slack_ratio"]:
                extra = w * params["nowrap_slack_ratio"]
                aligns = {sp.align for sp in item.paragraphs}
                # 只加寬靠左（含左右對齊）的框；置中與靠右的框實測在 LibreOffice 加寬後位置會偏
                if not rot and aligns <= {Align.LEFT, Align.JUSTIFY}:  # 旋轉的框以中心旋轉，加寬會移動中心，不加
                    w = w + extra
            if params["baseline_compensation"] and not rot:
                y += _baseline_shift(item) * s
            fb = vml_textbox_fallback(blocks, x=x, y=y, w=w, h=h, rot=rot, z_index=rel[0] + 2) if params["vml_fallback"] else None
            place(textbox_wsp(blocks, w, h, rot, item.wrap, params["textbox_autofit"]), URI_WPS, item.id, x, y, w, h, rot, behind, True, fb)
            expect_texts.extend("".join(r.text for r in sp.runs) for sp in item.paragraphs)
        elif kind == "image":
            rid, _ = docx_doc.part.get_or_add_image(image_descriptor(store, item.image))
            place(picture_pic(rid, w, h, rot, ctx._next_id, item.id), URI_PIC, item.id, x, y, w, h, rot, behind, False)
        elif kind == "shape":
            place(_shape_wsp(item, w, h, ctx), URI_WPS, item.id, x, y, w, h, 0.0 if item.geometry == "line" else rot, behind, True)
        else:
            expect_texts.extend(table_texts(item))
            if floating:
                if rot:
                    ctx.degrade(item.id, "表格旋轉", "浮動表格不能旋轉，以不旋轉輸出", True)
                ctx.degrade(item.id, "表格圖層", "浮動表格位於文字層：z 較低的物件改放文字後方", False)
                if pre:
                    pre.append(empty_paragraph(params["anchor_font_pt"]))  # 兩個表格相鄰會被 Word 併成一個
                pre.append(build_table(item, ctx, styles, exact_rows=True, diff=False, float_pos=(x, y)))
            else:
                t = build_table(item, ctx, styles, exact_rows=True, diff=False)
                place(textbox_wsp([t, empty_paragraph(params["anchor_font_pt"])], w, h, rot, True), URI_WPS, item.id, x, y, w, h, rot, behind, True)
    return pre, anchor_p, s


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    params = params_from(cfg, DEFAULT_PARAMS, "docx_fixed")
    ctx = make_ctx(doc)
    docx_doc = Document()
    sizes = Counter(round(r.size_pt.value, 1) for p in doc.pages for t in p.text_boxes for sp in t.paragraphs if sp.role == Role.BODY for r in sp.runs)
    from .docx_common import setup_package

    setup_package(docx_doc, ctx, sizes.most_common(1)[0][0] if sizes else 11.0)
    styles = build_styles(docx_doc, doc, ctx)
    body = docx_doc.element.body
    body_sect = body.find(q("w:sectPr"))
    expect_geom: dict = {}
    expect_texts: list[str] = []
    rel = [params["relative_height_base"]]
    if doc.pages:
        ctx.degrade("document", "閱讀順序與文字流", "版面固定模式不輸出閱讀順序；文字不會跨框流動", True)
    for i, page in enumerate(doc.pages):
        pre, anchor_p, s = _add_page(docx_doc, page, ctx, styles, store, params, expect_geom, expect_texts, rel)
        for blk in pre:
            body_sect.addprevious(blk)
        body_sect.addprevious(anchor_p)
        W, H = page.width_pt * s, page.height_pt * s
        if i < len(doc.pages) - 1:
            sect = el("w:sectPr")
            set_sect(sect, W, H)
            anchor_p.find(q("w:pPr")).append(sect)  # sectPr 必須是 pPr 的最後一個子元素
        else:
            set_sect(body_sect, W, H)
    data = save_bytes(docx_doc)
    ok, notes = selfcheck(data, max(1, len(doc.pages)), expect_texts, ctx.fonts)
    got = anchor_geometry(data)
    off = [n for n, g in expect_geom.items() if got.get(n) != g]
    if off:
        ok = False
        notes.append(f"自檢：{len(off)} 個浮動物件讀回的位置或大小與寫入值不同，例如 {off[0]}。")
    blob = store.put_blob(data, MIME, "docx")
    return OutputFile(format=OutputFormat.DOCX_FIXED, file=blob, filename=f"{basename}.docx", degradations=ctx.degradations,
                      fonts_used=sorted(ctx.fonts), selfcheck_ok=ok, selfcheck_notes=notes)
