"""⑤ 設定：收版面，交出成品文件 StyledDoc（與輸出格式無關的最終設定，單位 pt）。

步驟：
  s5.source_app   來源軟體指紋（依 PDF 的 Producer、Creator）
  s5.target_mode  目標模式：同源同格式求原值；異源求最簡潔且渲染一致的設定
  s5.page_size    頁面尺寸吸附到標準規格（使用者指定的尺寸不吸附）
  s5.font_size    字級吸附（「不吸附」永遠是候選）
  s5.font         字型定案：推論的字型名稱與驗證用的替代字型分開記
  s5.paragraph    段落屬性：行距、段距、對齊
  s5.box          文字框、圖片、形狀的位置與大小
  s5.styles       歸納樣式表與主題色
  s5.tables       表格：欄寬、列高、儲存格邊距與垂直對齊、底色、框線、數字格式推論（千分位、百分比、貨幣）
  s5.masters      母片：每一頁相同位置都出現的元素
  s5.formula      Excel 公式推論（只當候選，使用者確認才寫入）
尚未實作：顏色吸附、比例尺一致性檢查。
"""

from __future__ import annotations

import re
from collections import Counter

from ..artifacts import (
    Align, Box, Color, Correction, ElementList, FontChoice, Layout, LineSpacing, LineSpacingMode, OutputFormat, PageSpec, Provenance,
    Quad, Role, SourceDoc, Style, StyledDoc, StyledImage, StyledPage, StyledParagraph, StyledRun, StyledShape, StyledTextBox,
    TargetMode, TextLine, TextRun, measured,
)
from ..artifacts.common import Measured
from ..artifacts.element_list import ImageKind, ShapeGeometry
from ..config import Config
from ..tools.fonts import has_cjk, verification_font

NAME = "s5"
VERSION = "4"

_APPS = [
    ("powerpoint", "powerpoint"), ("impress", "libreoffice_impress"), ("keynote", "keynote"), ("google slides", "google_slides"),
    ("microsoft word", "word"), ("word", "word"), ("writer", "libreoffice_writer"), ("google docs", "google_docs"),
    ("excel", "excel"), ("calc", "libreoffice_calc"), ("indesign", "indesign"), ("illustrator", "illustrator"),
    ("canva", "canva"), ("figma", "figma"), ("chromium", "browser"), ("skia", "browser"),
]
_FAMILY = {
    "powerpoint": "slides", "libreoffice_impress": "slides", "keynote": "slides", "google_slides": "slides",
    "word": "document", "libreoffice_writer": "document", "google_docs": "document", "excel": "sheet", "libreoffice_calc": "sheet",
}
_FORMAT_FAMILY = {OutputFormat.PPTX: "slides", OutputFormat.GOOGLE_SLIDES: "slides", OutputFormat.DOCX_FIXED: "document", OutputFormat.DOCX_FLOW: "document", OutputFormat.XLSX: "sheet"}
_ROLE_NAMES = {Role.TITLE: "標題", Role.SUBTITLE: "副標", Role.BODY: "內文", Role.CAPTION: "圖說", Role.HEADER: "頁首", Role.FOOTER: "頁尾", Role.PAGE_NUMBER: "頁碼", Role.FOOTNOTE: "註腳", Role.UNKNOWN: "文字"}


_ROT_SNAP = {"exact": 0.05, "measured": 1.0}  # 旋轉角吸附到 0、±90、180 度的容差（度）：量測值容許較大的誤差


def _box(q: Quad, w: float, measured_input: bool = False) -> Box:
    """四角 → 位置與大小（pt）。旋轉的元素以中心為準還原成未旋轉的框加角度；接近直角倍數的角度吸附過去。"""
    cx, cy = q.center.x * w, q.center.y * w
    bw, bh = q.width * w, q.height * w
    angle = q.angle_deg
    tol = _ROT_SNAP["measured" if measured_input else "exact"]
    nearest = round(angle / 90.0) * 90.0
    if abs(angle - nearest) < tol:
        angle = nearest
        if nearest in (90.0, -90.0, 270.0, -270.0):
            pass
    rotation = 0.0 if abs(angle) < 1e-9 else angle
    if rotation == 0.0 and measured_input:
        l, t, r, b = q.bbox  # 量測產線：微小傾斜視為水平，用外接矩形
        return Box(x=l * w, y=t * w, w=(r - l) * w, h=(b - t) * w)
    return Box(x=cx - bw / 2, y=cy - bh / 2, w=bw, h=bh, rotation=rotation)


def run(doc: SourceDoc, specs: list[PageSpec], elements: list[ElementList], layout: Layout, cfg: Config, corrections: list[Correction]) -> StyledDoc:
    c = cfg.section("settings")
    subs = cfg.section("fonts").get("substitutes", {})
    sizes = cfg.section("page_sizes")
    notes: list[str] = []

    # ---------- s5.source_app ----------
    blob = " ".join(doc.metadata.get(k, "") for k in ("Creator", "Producer")).lower()
    app = next((name for needle, name in _APPS if needle in blob), "unknown")
    source_app = measured(app, 0.9 if app != "unknown" else 0.0, Provenance.INFERRED, f"{NAME}.source_app@{VERSION}")

    # ---------- s5.target_mode ----------
    wanted = {_FORMAT_FAMILY.get(f) for f in doc.intent.outputs}
    same = app != "unknown" and _FAMILY.get(app) in wanted
    target_mode = measured(TargetMode.SAME_SOURCE if same else TargetMode.CROSS_SOURCE, 0.8 if app != "unknown" else 0.5, Provenance.INFERRED, f"{NAME}.target_mode@{VERSION}")

    doc_fonts: dict = document_fonts(elements)
    doc_fonts["_palette"] = text_palette(elements, c)  # 量測產線的顏色調色盤
    doc_fonts["_bold_samples"] = bold_samples(elements, {sp.page_index: sp.page_width_pt.value for sp in specs})
    pages: list[StyledPage] = []
    style_counter: Counter = Counter()
    style_role: dict[tuple, Role] = {}
    color_counter: Counter = Counter()

    for spec, el, pl in zip(specs, elements, layout.pages):
        doc_fonts["_page_px_w"] = spec.working_image_size[0]
        doc_fonts["_page_paper"] = spec.enhanced
        doc_fonts["_page_pt_w"] = spec.page_width_pt.value
        # ---------- s5.page_size ----------
        w = spec.page_width_pt.value
        h = w * spec.aspect_ratio
        if not spec.page_width_pt.locked:
            for name, (sw, sh) in sizes.items():
                if abs(w - sw) <= c["page_snap_tolerance_pt"] and abs(h - sh) <= c["page_snap_tolerance_pt"]:
                    w, h = float(sw), float(sh)
                    break
        lines = {t.id: t for t in el.texts}
        pars = {p.id: p for p in pl.paragraphs}
        text_boxes: list[StyledTextBox] = []
        for tb in pl.text_boxes:
            sps: list[StyledParagraph] = []
            for i, pid in enumerate(tb.paragraph_ids):
                p = pars[pid]
                p_lines = [lines[i2] for i2 in p.line_ids]
                runs = _runs(p_lines, p.joiners, w, c, subs, doc_fonts)
                for r in runs:
                    sig = (r.font.family.value, r.size_pt.value, r.bold, r.italic, r.color.hex)
                    style_counter[sig] += len(r.text)
                    style_role.setdefault(sig, p.role.value)
                    color_counter[r.color.hex] += len(r.text)
                spacing = _line_spacing(p_lines, p.line_pitch, runs, w, c, same)
                gap = tb.space_between[i] * w if i < len(tb.space_between) else 0.0
                sps.append(
                    StyledParagraph(
                        id=p.id, runs=runs, align=p.alignment.value, line_spacing=spacing,
                        space_after_pt=round(gap, 1), first_line_indent_pt=round(p.first_line_indent * w, 1),
                        role=p.role.value, source_line_count=len(p.line_ids),
                    )
                )
            box = _box(tb.quad, w, el.production_line.value == "image")
            single = sum(sp.source_line_count for sp in sps) == 1
            wrap = not (single and c["single_line_no_wrap"])
            if wrap:
                # 多留一點寬度，降低目標軟體因字寬略有出入而提早折行的機率；位置依對齊方式錨定
                slack = c["box_width_slack_pt"]
                align = sps[0].align
                dx = 0.0 if align in (Align.LEFT, Align.JUSTIFY) else (-slack if align == Align.RIGHT else -slack / 2)
                box = box.model_copy(update={"x": box.x + dx, "w": box.w + slack})
            text_boxes.append(StyledTextBox(id=tb.id, box=box, z=tb.z, paragraphs=sps, wrap=wrap))

        images = [StyledImage(id=im.id, box=_box(im.quad, w, el.production_line.value == "image"), z=im.z, image=im.image) for im in el.images]
        if any(im.kind.value == ImageKind.SHADOW for im in el.images):
            notes.append(f"第 {spec.page_index + 1} 頁：形狀的陰影在來源檔中是點陣圖，照原樣保留為圖片，不是可調整的陰影效果。")
        shapes = []
        for s in el.shapes:
            if s.is_page_background:
                continue
            if s.geometry == ShapeGeometry.LINE:
                x0, y0, x1, y1 = s.quad.tl.x * w, s.quad.tl.y * w, s.quad.tr.x * w, s.quad.tr.y * w
                bx = Box(x=min(x0, x1), y=min(y0, y1), w=abs(x1 - x0), h=abs(y1 - y0))
                # 用 rotation 記方向：0 表示左上到右下，1 表示左下到右上
                bx = bx.model_copy(update={"rotation": 1.0 if (x1 - x0) * (y1 - y0) < 0 else 0.0})
            else:
                bx = _box(s.quad, w, el.production_line.value == "image")
            if s.fill:
                color_counter[s.fill.hex] += 50
            path_pt: list[list[float]] = []
            if s.geometry == ShapeGeometry.PATH:
                for sg in s.path:
                    if sg[0] == 3:
                        path_pt.append([3])
                    else:
                        path_pt.append([sg[0]] + [v * w - (bx.x if i % 2 == 0 else bx.y) for i, v in enumerate(sg[1:])])
            shapes.append(StyledShape(
                id=s.id, box=bx, z=s.z, geometry=s.geometry.value, fill=s.fill, stroke=s.stroke,
                stroke_width_pt=(s.stroke_width * w) if s.stroke_width else None,
                corner_radius_pt=(s.corner_radius * w) if s.corner_radius else None, path=path_pt,
            ))
        tables = [_table(t, el, pl, lines, w, c, subs, same, style_counter, style_role, color_counter, doc_fonts) for t in pl.tables]
        bg = el.background
        pages.append(
            StyledPage(
                page_index=spec.page_index, width_pt=w, height_pt=h,
                background_color=bg.color if (bg.color and bg.color.hex != "FFFFFF") else None, background_image=bg.image,
                text_boxes=text_boxes, images=images, shapes=shapes, reading_order=pl.reading_order, tables=tables,
            )
        )
        notes.extend(f"第 {spec.page_index + 1} 頁：{n}" for n in el.notes)

    # ---------- s5.styles ----------
    styles: list[Style] = []
    sig_to_id: dict[tuple, str] = {}
    used_names: Counter = Counter()
    for n, (sig, count) in enumerate(style_counter.most_common(), start=1):
        family, size, bold, italic, hexcolor = sig
        role = style_role.get(sig, Role.UNKNOWN)
        base = _ROLE_NAMES.get(role, "文字")
        used_names[base] += 1
        name = base if used_names[base] == 1 else f"{base} {used_names[base]}"
        sid = f"S{n}"
        sig_to_id[sig] = sid
        styles.append(Style(id=sid, name=name, font_family=family, size_pt=size, bold=bold, italic=italic, color=Color(r=int(hexcolor[0:2], 16), g=int(hexcolor[2:4], 16), b=int(hexcolor[4:6], 16)), role=role, usage_count=count))
    pages = [_tag_styles(p, sig_to_id) for p in pages]
    theme = [Color(r=int(hx[0:2], 16), g=int(hx[2:4], 16), b=int(hx[4:6], 16)) for hx, _ in color_counter.most_common(6)]

    masters = _masters(layout, pages)
    if masters:
        notes.append(f"有 {sum(len(m.member_ids) for m in masters)} 個元素在每一頁都出現在相同位置，已放到母片上。")
    styled = StyledDoc(target_mode=target_mode, source_app=source_app, pages=pages, styles=styles, theme_colors=theme, notes=notes, masters=masters)
    return _apply_corrections(styled, corrections, subs)


# ---------- s5.masters ----------


def _masters(layout: Layout, pages: list[StyledPage]):
    """④的母片候選 → 母片。只收「每一頁都有」的候選（簡報的版面配置會套用到所有頁）；
    文字只收單獨成框的（例如頁尾文字），框裡還有其他內容的不收。"""
    from ..artifacts import StyledMaster

    n = len(pages)
    if n < 2 or not layout.master_candidates:
        return []
    line_box: dict[str, str] = {}
    for pl in layout.pages:
        pars = {p.id: p for p in pl.paragraphs}
        for b in pl.text_boxes:
            ids = [lid for pid in b.paragraph_ids for lid in pars[pid].line_ids]
            if len(ids) == 1:
                line_box[ids[0]] = b.id
    tbs, ims, shs, members = [], [], [], []
    for cand in layout.master_candidates:
        if sorted(set(cand.page_indexes)) != list(range(n)) or len(cand.element_ids) != n:
            continue
        first_page = pages[cand.page_indexes[0]]
        eid0 = cand.element_ids[0]
        ids = [line_box.get(e, e) for e in cand.element_ids]
        tb = next((t for t in first_page.text_boxes if t.id == ids[0]), None)
        im = next((i for i in first_page.images if i.id == eid0), None)
        sh = next((x for x in first_page.shapes if x.id == eid0), None)
        if tb is not None:
            tbs.append(tb)
        elif im is not None:
            ims.append(im)
        elif sh is not None:
            shs.append(sh)
        else:
            continue
        members += ids
    if not members:
        return []
    return [StyledMaster(id="master-1", text_boxes=tbs, images=ims, shapes=shs, member_ids=members, page_indexes=list(range(n)))]


# ---------- s5.tables ----------

_NUM = re.compile(r"^\s*([-+(]?)\s*([$€£¥＄NT]*)\s*([\d,，]*\d(?:[.．]\d+)?)\s*(%|％)?\s*\)?\s*$")


def infer_number(text: str) -> tuple[float, str] | None:
    """「1,250」→（1250, "#,##0"）；「61%」→（0.61, "0%"）；「$25」→（25, "$#,##0"）；「(3.5)」→（-3.5, "#,##0.0;(#,##0.0)"）。"""
    m = _NUM.match(text)
    if not m:
        return None
    sign, cur, digits, pct = m.groups()
    raw = digits.replace("，", ",").replace("．", ".")
    if "," in raw and not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        return None  # 逗號位置不像千分位（可能是清單或日期）
    value = float(raw.replace(",", ""))
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    body = ("#,##0" if "," in raw else "0") + ("." + "0" * decimals if decimals else "")
    neg = sign in ("-", "(")
    if pct:
        value /= 100.0
        fmt = ("0" + ("." + "0" * decimals if decimals else "")) + "%"
    else:
        fmt = body
    if cur:
        sym = "$" if cur in ("$", "＄") else ("NT$" if "NT" in cur else cur)
        fmt = f'"{sym}"{fmt}' if sym not in ("$",) else f"${fmt}"
    if sign == "(":
        fmt = f"{fmt};({fmt})"
    return (-value if neg else value), fmt


def _table(t, el: ElementList, pl, lines: dict, w: float, c: dict, subs: dict, same: bool, style_counter, style_role, color_counter, doc_fonts=None):
    """④的表格結構 → 成品表格：欄寬、列高、儲存格的段落、底色、框線、邊距、垂直對齊、數字格式。"""
    from ..artifacts import Border, StyledCell, StyledTable

    region = next((r for r in el.table_regions if r.id == t.region_id), None)
    pars = {p.id: p for p in pl.paragraphs}
    xs, ys = t.col_lines, t.row_lines
    tol = 0.004
    seg = region.segments if region else []
    color = (region.line_color if region and region.line_color else None) or Color(r=0, g=0, b=0)
    width_pt = (region.line_width * w) if region and region.line_width else 0.5
    cells = []
    for cl in t.cells:
        l, top, r, b = cl.quad.bbox
        sps: list[StyledParagraph] = []
        cell_lines = []
        for pid in cl.paragraph_ids:
            p = pars[pid]
            p_lines = [lines[i] for i in p.line_ids]
            cell_lines += p_lines
            runs = _runs(p_lines, p.joiners, w, c, subs, doc_fonts)
            for rn in runs:
                sig = (rn.font.family.value, rn.size_pt.value, rn.bold, rn.italic, rn.color.hex)
                style_counter[sig] += len(rn.text)
                style_role.setdefault(sig, Role.BODY)
                color_counter[rn.color.hex] += len(rn.text)
            sps.append(StyledParagraph(id=p.id, runs=runs, align=p.alignment.value, line_spacing=_line_spacing(p_lines, p.line_pitch, runs, w, c, same), role=Role.BODY, source_line_count=len(p.line_ids)))
        if cell_lines:
            tl, tt = min(ln.quad.bbox[0] for ln in cell_lines), min(ln.quad.bbox[1] for ln in cell_lines)
            tr, tb = max(ln.quad.bbox[2] for ln in cell_lines), max(ln.quad.bbox[3] for ln in cell_lines)
            gap_t, gap_b, gap_l, gap_r = (tt - top) * w, (b - tb) * w, (tl - l) * w, (r - tr) * w
            if gap_t > 0 and gap_b > 0 and abs(gap_t - gap_b) <= 0.3 * (gap_t + gap_b) and (b - top) > 1.6 * (tb - tt):
                v_align = "middle"
            elif gap_b < 0.5 * gap_t:
                v_align = "bottom"
            else:
                v_align = "top"
            side = max(0.0, min(gap_l, gap_r)) if sps and sps[0].align == Align.CENTER else max(0.0, gap_l)
            margins = [max(0.0, gap_t) if v_align == "top" else 0.0, max(0.0, gap_r) if sps and sps[0].align == Align.RIGHT else side, max(0.0, gap_b) if v_align == "bottom" else 0.0, side]
        else:
            v_align, margins = "top", [0.0, 0.0, 0.0, 0.0]
        fill = None
        if region:
            for fq, fc in region.cell_fills:
                fl, ft, fr, fb = fq.bbox
                if fl - tol <= cl.quad.center.x <= fr + tol and ft - tol <= cl.quad.center.y <= fb + tol:
                    fill = fc
        edges = [
            _covered_edge(seg, False, top, l, r, tol), _covered_edge(seg, True, r, top, b, tol),
            _covered_edge(seg, False, b, l, r, tol), _covered_edge(seg, True, l, top, b, tol),
        ]
        borders = [Border(color=color, width_pt=width_pt) if e else None for e in edges]
        text = "".join(rn.text for sp in sps for rn in sp.runs).strip()
        num = infer_number(text) if text and len(sps) == 1 else None
        cells.append(StyledCell(
            row=cl.row, col=cl.col, row_span=cl.row_span, col_span=cl.col_span, paragraphs=sps, fill=fill, borders=borders,
            v_align=v_align, margins_pt=[round(v, 2) for v in margins], header=cl.header,
            value=num[0] if num else None, number_format=num[1] if num else None,
        ))
    cells = _formula_candidates(cells)
    return StyledTable(
        id=t.id, box=_box(t.quad, w), z=t.z, col_widths_pt=[(b - a) * w for a, b in zip(xs, xs[1:])],
        row_heights_pt=[(b - a) * w for a, b in zip(ys, ys[1:])], cells=cells, header_rows=t.header_rows,
    )


_TOTAL_WORDS = ("合計", "總計", "小計", "總和", "共計", "total", "sum", "subtotal")


def _col_letter(c: int) -> str:
    out = ""
    c += 1
    while c:
        c, r = divmod(c - 1, 26)
        out = chr(65 + r) + out
    return out


def _formula_candidates(cells):
    """s5.formula：數值儲存格若等於正上方（或正左方）連續兩格以上數值的總和，推論為 =SUM(...)。
    只當候選（formula_candidate）；使用者確認後才寫成公式，未確認的 Excel 輸出純數值。
    容差是顯示位數的一半（1,250 與 1,249.6 不算相等）；同列有「合計」「Total」之類的字時才收列方向的加總。"""
    by_pos = {(c.row, c.col): c for c in cells if c.row_span == 1 and c.col_span == 1}

    def decimals(c) -> int:
        f = c.number_format or ""
        body = f.split(";")[0]
        return len(body.split(".")[1].rstrip("%")) if "." in body else 0

    def label(r: int) -> str:
        first = by_pos.get((r, 0))
        return "".join(rn.text for p in first.paragraphs for rn in p.runs).lower() if first else ""

    out = []
    for c in cells:
        cand = None
        if c.value is not None and not c.header:
            tol = 0.5 * 10 ** (-decimals(c)) + 1e-9
            if "%" in (c.number_format or ""):
                tol /= 100
            for axis in ("col", "row"):
                run = []
                k = 1
                while True:
                    pos = (c.row - k, c.col) if axis == "col" else (c.row, c.col - k)
                    other = by_pos.get(pos)
                    if other is None or other.value is None or other.header:
                        break
                    run.append(other)
                    k += 1
                if len(run) < 2:
                    continue
                if axis == "row" and not any(wd in label(c.row) for wd in _TOTAL_WORDS):
                    continue
                if abs(sum(o.value for o in run) - c.value) <= tol:
                    if axis == "col":
                        cand = f"=SUM({_col_letter(c.col)}{c.row - len(run) + 1}:{_col_letter(c.col)}{c.row})"
                    else:
                        cand = f"=SUM({_col_letter(c.col - len(run))}{c.row + 1}:{_col_letter(c.col - 1)}{c.row + 1})"
                    break
        out.append(c.model_copy(update={"formula_candidate": cand}) if cand else c)
    return out


def _covered_edge(segments, vertical: bool, at: float, lo: float, hi: float, tol: float) -> bool:
    need, got = 0.6 * (hi - lo), 0.0
    for x0, y0, x1, y1 in segments:
        if vertical and abs(x0 - x1) <= tol and abs((x0 + x1) / 2 - at) <= tol:
            a, b = min(y0, y1), max(y0, y1)
        elif not vertical and abs(y0 - y1) <= tol and abs((y0 + y1) / 2 - at) <= tol:
            a, b = min(x0, x1), max(x0, x1)
        else:
            continue
        got += max(0.0, min(b, hi) - max(a, lo))
    return got >= need


# ---------- s5.font_size、s5.font ----------


def _snap_size(pt: float, c: dict, source: Measured, step: str, error_pt: float = 0.0) -> Measured:
    """字級吸附。精確擷取的值只容許極小的差距（37.2 pt 保留原值）；量測值的容差隨量測誤差放大
    （量不準的 18.06 ± 0.26 pt 吸到 18 pt）。先試整數，再試半點；都不符就不吸附。"""
    tol = c["size_snap_tolerance_pt"]
    if source.source != Provenance.EXACT:
        tol = max(tol, c.get("size_snap_sigma", 1.5) * max(error_pt, 0.0))
    for grid in (1.0, c["size_snap_step_pt"]):
        snapped = round(pt / grid) * grid
        if abs(snapped - pt) <= tol:
            if abs(snapped - pt) < 1e-6 and source.source == Provenance.EXACT:
                return measured(float(snapped), 1.0, Provenance.EXACT, step)
            return measured(float(snapped), min(source.confidence, 0.95), Provenance.SNAPPED, step, error=abs(snapped - pt))
    return measured(round(pt, 3), source.confidence, source.source, step, error=error_pt or None)  # 不吸附：保留原值


def _font(run: TextRun, subs: dict[str, str], family: str | None = None, confidence: float | None = None) -> FontChoice:
    step = f"{NAME}.font@{VERSION}"
    if family is None:
        family = run.font_candidates[0].name if run.font_candidates else "unknown"
    if confidence is None:
        confidence = 1.0 if run.font_size.source == Provenance.EXACT else (run.font_candidates[0].score if run.font_candidates else 0.0)
    ea = has_cjk(run.text)
    vf, substituted = verification_font(family, subs, ea)
    return FontChoice(family=measured(family, float(confidence), run.font_size.source, step), verification_family=vf, is_substituted=substituted, east_asian=ea)


_SKIP_FONTS = ("Unifont",)  # 點陣後備字型，設計文件不會用；量測時常被誤選


def _candidates(r: TextRun, c: dict):
    skip = tuple(c.get("font_skip_prefixes", _SKIP_FONTS))
    return [fc for fc in r.font_candidates if not fc.name.startswith(skip)]


def _script(text: str) -> str:
    return "cjk" if has_cjk(text) else "latin"


def _lab(c: Color) -> tuple[float, float, float]:
    def lin(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = lin(c.r), lin(c.g), lin(c.b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(a: Color, b: Color) -> float:
    la, lb = _lab(a), _lab(b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


def text_palette(elements: list[ElementList], c: dict) -> dict[str, Color]:
    """s5.color：量測產線的文字顏色歸併成調色盤。光照與壓縮會讓同一個顏色量出一片相近的值；
    ΔE 小於門檻的歸成一群，用字數最多的那個顏色代表；接近純黑或純白的再吸到純黑、純白。回傳（原色 → 定案色）。"""
    counts: Counter = Counter()
    for el in elements:
        if el.production_line.value != "image":
            continue
        for t in el.texts:
            for r in t.runs:
                counts[r.color.value.hex] += len(r.text.strip())
    tol = c.get("color_merge_delta_e", 12.0)
    centers: list[Color] = []
    out: dict[str, Color] = {}
    for hx, _ in counts.most_common():
        col = Color(r=int(hx[0:2], 16), g=int(hx[2:4], 16), b=int(hx[4:6], 16))
        near = min(centers, key=lambda z: _delta_e(z, col), default=None)
        if near is not None and _delta_e(near, col) <= tol:
            out[hx] = near
        else:
            centers.append(col)
            out[hx] = col
    black, white = Color(r=0, g=0, b=0), Color(r=255, g=255, b=255)
    snap = c.get("color_snap_black_white_delta_e", 10.0)
    for hx, col in list(out.items()):
        if _delta_e(col, black) <= snap:
            out[hx] = black
        elif _delta_e(col, white) <= snap:
            out[hx] = white
    return out


def bold_samples(elements: list[ElementList], w_of: dict[int, float]) -> list[tuple[float, float, int]]:
    """所有量測段的（字級 pt、筆畫粗細比、字數），給「同字級群組內」的相對粗體判斷用。
    模糊會讓筆畫一起變粗，而且字越小加粗得越多，所以只跟字級相近的文字比。"""
    out = []
    for el in elements:
        w = w_of.get(el.page_index, 1.0)
        for t in el.texts:
            for r in t.runs:
                if r.stroke_ratio:
                    out.append((r.font_size.value * w, r.stroke_ratio, len(r.text.strip())))
    return out


def _relative_bold(pt: float, ratio: float, samples, c: dict) -> bool | None:
    """同字級群組（±25%）中，筆畫粗細比的加權中位數當作「一般粗細」；明顯更粗才算粗體。群組裡的其他字太少時回傳 None（交給③的判斷）。"""
    group = sorted((r, n) for p_, r, n in samples if abs(p_ - pt) <= 0.25 * pt)
    if sum(n for _, n in group) < c.get("bold_group_min_chars", 30):
        return None
    total = sum(n for _, n in group)
    acc, median = 0.0, group[-1][0]
    for r, n in group:
        acc += n
        if acc >= total / 2:
            median = r
            break
    return ratio >= median * c.get("bold_relative_ratio", 1.3)


def document_fonts(elements: list[ElementList]) -> dict[str, str]:
    """整份文件各書寫系統最可靠的字型（量測產線用）：信心值夠高的段落，依「字數 × 信心值 × 候選分數」投票。"""
    votes: dict[str, Counter] = {"cjk": Counter(), "latin": Counter()}
    for el in elements:
        for t in el.texts:
            for r in t.runs:
                cands = [fc for fc in r.font_candidates if not fc.name.startswith(_SKIP_FONTS)]
                if r.font_size.source == Provenance.EXACT or not cands:
                    continue
                top = cands[0]
                if r.font_size.confidence >= 0.5:
                    votes[_script(r.text)][top.name] += len(r.text.strip()) * r.font_size.confidence * max(top.score, 0.05)
    return {k: v.most_common(1)[0][0] for k, v in votes.items() if v}


def _settle(lines: list[TextLine], w: float, c: dict, doc_fonts: dict) -> dict[int, tuple[float, float, str, float, bool, bool]]:
    """量測產線的段落內定案（s5.font、s5.font_size 的「同段投票」）：
    - 字級：段落內依「字數 × 信心值」取加權中位數；誤差範圍內（或差距小於 6%）的段一律用中位數，真的不同才保留。
    - 字型：同一書寫系統的段投票；某段自己的第一名信心低、或投票結果在它的前三名候選裡，就改用投票結果；
      整段都不可靠時用整份文件的字型。
    - 粗體與斜體：信心低的段跟隨段落多數。
    回傳（段的 id → 字級 pt、字級誤差 pt、字型、字型信心、粗體、斜體）。精確擷取的段不處理。"""
    runs = [r for ln in lines for r in ln.runs if r.font_size.source != Provenance.EXACT and r.text.strip()]
    if not runs:
        return {}
    sizes = sorted((r.font_size.value * w, len(r.text.strip()) * max(r.font_size.confidence, 0.05)) for r in runs)
    total = sum(wt for _, wt in sizes)
    acc, median = 0.0, sizes[-1][0]
    for v, wt in sizes:
        acc += wt
        if acc >= total / 2:
            median = v
            break
    votes: dict[str, Counter] = {"cjk": Counter(), "latin": Counter()}
    for r in runs:
        if _candidates(r, c):
            top = _candidates(r, c)[0]
            votes[_script(r.text)][top.name] += len(r.text.strip()) * max(r.font_size.confidence, 0.05) * max(top.score, 0.05)
    bold_votes = sum((1 if r.bold.value else -1) * r.bold.confidence * len(r.text.strip()) for r in runs)
    out = {}
    for r in runs:
        pt = r.font_size.value * w
        err = (r.font_size.error or 0.0) * w
        few = len(r.text.strip()) <= 3 or r.font_size.confidence < 0.5  # 字太少或量不準：容許差距放寬
        rel = c.get("settle_size_rel", 0.06) * (2.5 if few else 1.0)
        if abs(pt - median) <= max(2 * err, rel * median):
            pt, err = median, min(err, 0.5 * median * c.get("settle_size_rel", 0.06))
        script = _script(r.text)
        cands = _candidates(r, c)
        own = cands[0].name if cands else None
        own_conf = cands[0].score if cands else 0.0
        winner = votes[script].most_common(1)[0][0] if votes[script] else None
        backers = [x for x in runs if _script(x.text) == script and _candidates(x, c) and _candidates(x, c)[0].name == winner]
        weak_vote = not backers or max(_candidates(x, c)[0].score for x in backers) < 0.3
        names = [fc.name for fc in cands[:3]]
        family, conf = own, own_conf
        if winner and not weak_vote and (winner in names or own_conf < c.get("settle_font_conf", 0.5) or r.font_size.confidence < 0.5):
            family, conf = winner, max(own_conf, 0.5)
        if (family is None or (weak_vote and conf < 0.3)) and doc_fonts.get(script):
            family, conf = doc_fonts[script], 0.4  # 整段都不可靠（例如單獨一個頁碼）：用整份文件的字型
        bold = r.bold.value if r.bold.confidence >= 0.7 else bold_votes > 0
        if r.stroke_ratio and doc_fonts.get("_bold_samples"):
            rel = _relative_bold(pt, r.stroke_ratio, doc_fonts["_bold_samples"], c)  # 相對同字級文字的筆畫粗細
            if rel is not None:
                bold = rel
        italic = r.italic.value
        out[id(r)] = (pt, err, family or "unknown", conf, bold, italic)
    return out


def _runs(lines: list[TextLine], joiners: list[str], w: float, c: dict, subs: dict[str, str], doc_fonts: dict[str, str] | None = None) -> list[StyledRun]:
    """把一段的各行接起來，樣式相同的相鄰文字併成一段。量測產線的值先做段落內定案（_settle）。"""
    step = f"{NAME}.font_size@{VERSION}"
    settled = _settle(lines, w, c, doc_fonts or {})
    out: list[StyledRun] = []
    for i, line in enumerate(lines):
        for j, r in enumerate(line.runs):
            text = r.text
            if j == 0 and i > 0 and joiners[i - 1]:
                text = joiners[i - 1] + text
            if id(r) in settled:
                pt, err, family, fconf, bold, italic = settled[id(r)]
                size = _snap_size(pt, c, r.font_size, step, err)
                font = _font(r, subs, family, fconf)
            else:
                size = _snap_size(r.font_size.value * w, c, r.font_size, step)
                font = _font(r, subs)
                bold, italic = r.bold.value, r.italic.value
            color = r.color.value
            if r.color.source != Provenance.EXACT:
                color = (doc_fonts or {}).get("_palette", {}).get(color.hex, color)
                lab = _lab(color)
                chroma = (lab[1] ** 2 + lab[2] ** 2) ** 0.5
                stroke_px = (r.stroke_ratio or 0.0) * r.font_size.value * (doc_fonts or {}).get("_page_px_w", 0)
                thin = 0 < stroke_px < c.get("thin_stroke_px", 2.5)  # 筆畫太細：核心顏色會被模糊與反鋸齒沖淡，量到的灰不可信
                small_on_paper = (doc_fonts or {}).get("_page_paper") and r.font_size.value * (doc_fonts or {}).get("_page_pt_w", 0) < c.get("paper_small_text_pt", 14)
                thin = thin or bool(small_on_paper)  # 白紙上的小字：照片解析度下筆畫只有一兩個像素，量到的灰多半是黑字被沖淡
                if (thin or r.color.confidence < c.get("dark_text_conf", 0.6)) and chroma < 12 and lab[0] < c.get("dark_text_max_l", 60):
                    color = Color(r=0, g=0, b=0)  # 細筆畫被模糊沖淡的深灰：當成黑字
            sr = StyledRun(text=text, font=font, size_pt=size, bold=bold, italic=italic, color=color)
            if out and (out[-1].font.family.value, out[-1].size_pt.value, out[-1].bold, out[-1].italic) == (sr.font.family.value, sr.size_pt.value, sr.bold, sr.italic) and _same_color(out[-1].color, sr.color, r.color.source):
                out[-1] = out[-1].model_copy(update={"text": out[-1].text + text})
            else:
                out.append(sr)
    if out:  # 段尾的空白沒有意義
        out[-1] = out[-1].model_copy(update={"text": out[-1].text.rstrip()})
    return [r for r in out if r.text]


def _same_color(a: Color, b: Color, source: Provenance) -> bool:
    """精確擷取要完全相同；量測值差距在 ΔRGB 12 以內視為同色（反鋸齒與壓縮的雜訊）。"""
    if source == Provenance.EXACT:
        return a == b
    return max(abs(a.r - b.r), abs(a.g - b.g), abs(a.b - b.b)) <= 12


# ---------- s5.paragraph ----------


def _line_spacing(lines: list[TextLine], pitch: float | None, runs: list[StyledRun], w: float, c: dict, same_source: bool):
    """行距：量得到基線間距才有意義。能整齊對上倍數就用倍數，否則用固定值（pt）。"""
    step = f"{NAME}.paragraph@{VERSION}"
    if pitch is None or not runs:
        return None
    pitch_pt = pitch * w
    inner = [b.baseline[0].y - a.baseline[0].y for a, b in zip(lines, lines[1:])]
    consistent = bool(inner) and (max(inner) - min(inner)) * w <= 0.15
    size = max(r.size_pt.value for r in runs)
    multiple = pitch_pt / (size * c["font_line_height"])
    if same_source and consistent:
        for cand in c["line_spacing_candidates"]:
            if abs(multiple - cand) <= c["line_spacing_tolerance"]:
                return measured(LineSpacing(mode=LineSpacingMode.MULTIPLE, value=cand), 0.7, Provenance.SNAPPED, step, error=abs(multiple - cand))
    return measured(LineSpacing(mode=LineSpacingMode.EXACT, value=round(pitch_pt, 1)), 0.8 if consistent else 0.6, Provenance.MEASURED, step)


def _tag_styles(page: StyledPage, sig_to_id: dict[tuple, str]) -> StyledPage:
    boxes = []
    for tb in page.text_boxes:
        ps = []
        for p in tb.paragraphs:
            r = max(p.runs, key=lambda x: len(x.text)) if p.runs else None
            sid = sig_to_id.get((r.font.family.value, r.size_pt.value, r.bold, r.italic, r.color.hex)) if r else None
            ps.append(p.model_copy(update={"style_id": sid}))
        boxes.append(tb.model_copy(update={"paragraphs": ps}))
    return page.model_copy(update={"text_boxes": boxes})


# ---------- 修正單 ----------


def _apply_corrections(doc: StyledDoc, corrections: list[Correction], subs: dict[str, str]) -> StyledDoc:
    """⑤接受的修正（指定文字框或段落的編號）：
    font_family、font_size_pt、color（十六進位）、align、text（改一段的文字，指名段落編號）；
    以及⑦驗證開的 width_adjust_pt（修正斷行）、offset_pt（平移文字框）；
    表格儲存格（編號為「表格編號#列,欄」）的 formula：值為 accept 表示採用推論的公式，或直接給公式，空值表示不用公式。
    """
    if not corrections:
        return doc
    pages = []
    for page in doc.pages:
        tables = []
        for t in page.tables:
            cells = list(t.cells)
            for c in corrections:
                if c.field != "formula" or not c.element_id or "#" not in c.element_id:
                    continue
                tid, pos = c.element_id.split("#", 1)
                if tid != t.id:
                    continue
                r, k = (int(v) for v in pos.split(","))
                for i, cell in enumerate(cells):
                    if (cell.row, cell.col) == (r, k):
                        value = cell.formula_candidate if c.new_value in (True, "accept") else (str(c.new_value) if c.new_value else None)
                        cells[i] = cell.model_copy(update={"formula": value})
            tables.append(t.model_copy(update={"cells": cells}))
        page = page.model_copy(update={"tables": tables})
        boxes = []
        for tb in page.text_boxes:
            box = tb.box
            paragraphs = list(tb.paragraphs)
            for c in corrections:
                hits_box = c.element_id == tb.id
                if c.field == "width_adjust_pt" and hits_box:
                    delta = float(c.new_value)
                    align = paragraphs[0].align if paragraphs else Align.LEFT
                    dx = 0.0 if align in (Align.LEFT, Align.JUSTIFY) else (-delta if align == Align.RIGHT else -delta / 2)
                    box = box.model_copy(update={"x": box.x + dx, "w": max(1.0, box.w + delta)})
                    continue
                if c.field == "offset_pt" and hits_box:
                    box = box.model_copy(update={"x": box.x + float(c.new_value[0]), "y": box.y + float(c.new_value[1])})
                    continue
                for i, p in enumerate(paragraphs):
                    if not (hits_box or c.element_id == p.id):
                        continue
                    if c.field == "align":
                        paragraphs[i] = p.model_copy(update={"align": Align(c.new_value)})
                        continue
                    if c.field == "text":
                        if hits_box and len(paragraphs) > 1:
                            continue  # 指名文字框但框裡有多段：不知道要改哪一段
                        head = p.runs[0] if p.runs else None
                        if head is not None:
                            paragraphs[i] = p.model_copy(update={"runs": [head.model_copy(update={"text": str(c.new_value)})], "text_locked": True})
                        continue
                    runs = []
                    for r in p.runs:
                        if c.field == "font_family":
                            vf, sub = verification_font(str(c.new_value), subs, r.font.east_asian)
                            fam = measured(str(c.new_value), 1.0, Provenance.USER, "user").model_copy(update={"locked": True})
                            r = r.model_copy(update={"font": r.font.model_copy(update={"family": fam, "verification_family": vf, "is_substituted": sub})})
                        elif c.field == "font_size_pt":
                            r = r.model_copy(update={"size_pt": measured(float(c.new_value), 1.0, Provenance.USER, "user").model_copy(update={"locked": True})})
                        elif c.field == "color":
                            hx = str(c.new_value).lstrip("#")
                            r = r.model_copy(update={"color": Color(r=int(hx[0:2], 16), g=int(hx[2:4], 16), b=int(hx[4:6], 16))})
                        runs.append(r)
                    paragraphs[i] = paragraphs[i].model_copy(update={"runs": runs})
            boxes.append(tb.model_copy(update={"box": box, "paragraphs": paragraphs}))
        pages.append(page.model_copy(update={"text_boxes": boxes}))
    return doc.model_copy(update={"pages": pages})
