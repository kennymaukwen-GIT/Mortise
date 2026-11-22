"""④ 結構：收元素清單，交出版面 Layout。

核心是「空間組裝法」：每個元素用四角確定自己的空間；空間之間發放有類型的組裝編號，
只有類型與編號都相符的空間才能組在一起（取代「距離近就合併」）。

步驟：
  s4.text_flow   文字流：行 → 段落 → 文字框（發放 text_flow 編號）
  s4.align       段落對齊方式
  s4.roles       角色標籤：標題、內文、頁碼（規則判斷；之後由大型 AI 仲裁）
  s4.contain     包含關係：文字框落在形狀裡（發放 contain 編號）
  s4.same_style  同樣式分群（發放 same_style 編號，供⑤歸納樣式表）
  s4.order       閱讀順序與圖層順序、對齊線
  s4.cross_page  跨頁重複元素 → 母片候選
  s4.tables     表格：列、欄、合併儲存格（相鄰兩格之間沒有格線就是合併）、表頭、儲存格內的段落
尚未實作：清單階層、分欄、無框線表格、圖說配對、網格編號、大型 AI 仲裁。
"""

from __future__ import annotations

import hashlib
import re
import statistics

from ..artifacts import (
    Align, AssemblyLink, AssemblyType, Correction, ElementList, Layout, MasterCandidate, PageLayout, PageSpec, Paragraph,
    Provenance, Quad, Role, TableCell, TableStructure, TextBox, TextLine, make_element_id, measured, oriented_union, union_quad,
)
from ..config import Config
from ..tools.fonts import is_cjk

NAME = "s4"
VERSION = "3"

_BULLET = re.compile(r"^\s*([•‧·●○■□▪◆◇▶➤\-–—*]|\(?\d{1,2}[.)、]|[a-zA-Z][.)]|[一二三四五六七八九十]+[、.])\s*")
_PAGE_NUMBER = re.compile(r"^[\-–— ]*(第\s*)?\d{1,4}(\s*頁)?(\s*[/／]\s*\d{1,4})?[\-–— ]*$")


def _style_sig(line: TextLine) -> tuple:
    r = max(line.runs, key=lambda x: len(x.text))
    return (r.font_candidates[0].name if r.font_candidates else "", round(r.font_size.value, 6), r.bold.value, r.italic.value, r.color.value.hex)


def _first_token_width(line: TextLine) -> float:
    """這一行第一個詞有多寬（相對單位）：用來判斷上一行是不是「放得下卻沒放」，也就是硬換行。"""
    text = line.text
    xs = [x for r in line.runs for x in r.char_x]
    visible = [ch for ch in text if ch.strip()]
    if not visible or len(xs) != len(visible):
        return line.font_size
    if is_cjk(visible[0]):
        return xs[1] - xs[0] if len(xs) > 1 else line.font_size
    count = 0
    seen_space = False
    for ch in text:
        if not ch.strip():
            if count:
                seen_space = True
            continue
        if seen_space or is_cjk(ch):
            return xs[count] - xs[0]
        count += 1
    return line.quad.width


def _joiner(a: str, b: str) -> str:
    """兩行接成一段時中間補什麼：中日韓文字之間不補，西文單字之間補一個空白。"""
    if not a or not b:
        return ""
    if is_cjk(a[-1]) or is_cjk(b[0]):
        return ""
    return "" if a[-1].isspace() else " "


def run_page(elements: ElementList, spec: PageSpec, cfg: Config, corrections: list[Correction], ai=None, page_image=None) -> PageLayout:
    c = cfg.section("structure")
    page = spec.page_index
    step = f"{NAME}.text_flow@{VERSION}"
    notes: list[str] = []
    lines = [t for t in elements.texts if not t.hidden and t.text.strip()]
    lines.sort(key=lambda t: (round(t.baseline[0].y, 4), t.baseline[0].x))

    # ---------- s4.tables：表格先處理，儲存格裡的字不參與一般的文字流 ----------
    tables, table_pars, in_tables = _tables(elements, lines, spec, c)
    lines = [t for t in lines if t.id not in in_tables]

    # ---------- s4.text_flow：行 → 區塊 ----------
    blocks: list[list[TextLine]] = []
    for line in lines:
        best, best_gap = None, None
        max_angle = c.get("flow_max_angle_deg", 2.0)  # 量測產線的行框會有零點幾度的誤差
        if abs(line.quad.angle_deg) < max_angle:
            for block in blocks:
                last = block[-1]
                if abs(last.quad.angle_deg) >= max_angle or abs(last.quad.angle_deg - line.quad.angle_deg) > 1.0:
                    continue
                size = last.font_size
                tol = max(c["size_tolerance"], 2.0 * max(line.font_size_rel_error, last.font_size_rel_error))  # 量測值的容差隨誤差放大
                if abs(line.font_size - size) > tol * size:
                    continue
                pitch = line.baseline[0].y - last.baseline[0].y
                if pitch <= 0.3 * size:
                    continue
                known = [b2.baseline[0].y - b1.baseline[0].y for b1, b2 in zip(block, block[1:])]
                limit = (min(known) * c["paragraph_gap_factor"] * 1.6) if known else 2.6 * size
                if pitch > limit:
                    continue
                ll, lr = last.quad.bbox[0], last.quad.bbox[2]
                nl, nr = line.quad.bbox[0], line.quad.bbox[2]
                bl, br = min(t.quad.bbox[0] for t in block), max(t.quad.bbox[2] for t in block)
                tol = c["left_align_tolerance"]
                aligned = abs(nl - bl) <= tol or abs(nr - br) <= tol or abs((nl + nr) / 2 - (bl + br) / 2) <= tol
                indented = 0 < nl - bl <= 3 * size and nr <= br + tol  # 首行縮排
                overlap = min(lr, nr) - max(ll, nl)
                if not (aligned or indented) or overlap <= 0:
                    continue
                if best is None or pitch < best_gap:
                    best, best_gap = block, pitch
        if best is None:
            blocks.append([line])
        else:
            best.append(line)

    # ---------- 區塊 → 段落 ----------
    paragraphs: list[Paragraph] = []
    text_boxes: list[TextBox] = []
    links: list[AssemblyLink] = []
    line_role_hint: dict[str, Role] = {}
    for block in blocks:
        pitches = [b2.baseline[0].y - b1.baseline[0].y for b1, b2 in zip(block, block[1:])]
        base_pitch = min(pitches) if pitches else None
        b_left = min(t.quad.bbox[0] for t in block)
        b_right = max(t.quad.bbox[2] for t in block)
        groups: list[list[TextLine]] = [[block[0]]]
        gaps_between: list[float] = []
        for prev, line, pitch in zip(block, block[1:], pitches):
            size = line.font_size
            wide_gap = pitch > base_pitch * c["paragraph_gap_factor"]
            room = b_right - prev.quad.bbox[2]
            hard_break = room > _first_token_width(line) + 0.3 * size
            bullet = bool(_BULLET.match(line.text))
            style_change = _style_sig(prev)[2:4] != _style_sig(line)[2:4] and len(prev.runs) == 1 and len(line.runs) == 1
            if wide_gap or hard_break or bullet or style_change:
                groups.append([line])
                gaps_between.append(max(0.0, pitch - base_pitch))
            else:
                groups[-1].append(line)
        par_ids: list[str] = []
        for g in groups:
            q = oriented_union([t.quad for t in g])
            inner = [b2.baseline[0].y - b1.baseline[0].y for b1, b2 in zip(g, g[1:])]
            pitch_value = statistics.median(inner) if inner else base_pitch
            align = _alignment(g, block, spec, c)
            marker = _BULLET.match(g[0].text)
            pid = make_element_id(page, "par", q, g[0].id)
            paragraphs.append(
                Paragraph(
                    id=pid, line_ids=[t.id for t in g], quad=q, alignment=align,
                    role=measured(Role.BODY, 0.5, Provenance.INFERRED, f"{NAME}.roles@{VERSION}"),
                    line_pitch=pitch_value, first_line_indent=max(0.0, g[0].quad.bbox[0] - b_left) if len(g) > 1 else 0.0,
                    list_marker=marker.group(1) if marker else None,
                    joiners=[_joiner(a.text, b.text) for a, b in zip(g, g[1:])],
                )
            )
            par_ids.append(pid)
            if len(g) > 1:
                links.append(AssemblyLink(type=AssemblyType.TEXT_FLOW, key=pid, members=[t.id for t in g], confidence=0.8, writer=step))
        bq = oriented_union([t.quad for t in block])
        bid = make_element_id(page, "box", bq, par_ids[0])
        text_boxes.append(TextBox(id=bid, quad=bq, z=min(t.z for t in block), paragraph_ids=par_ids, space_between=gaps_between))

    # ---------- s4.roles ----------
    paragraphs = _roles(paragraphs, {t.id: t for t in lines}, spec, c) + table_pars

    # ---------- s4.contain ----------
    for box in text_boxes:
        l, t, r, b = box.quad.bbox
        for shape in elements.shapes:
            sl, st, sr, sb = shape.quad.bbox
            if sl - 0.002 <= l and st - 0.002 <= t and r <= sr + 0.002 and b <= sb + 0.002 and shape.z < box.z:
                links.append(AssemblyLink(type=AssemblyType.CONTAIN, key=shape.id, members=[shape.id, box.id], confidence=0.7, writer=f"{NAME}.contain@{VERSION}"))

    # ---------- s4.same_style ----------
    by_line = {t.id: t for t in elements.texts}
    groups_by_style: dict[str, list[str]] = {}
    for p in paragraphs:
        sig = _style_sig(by_line[p.line_ids[0]])
        key = hashlib.sha1(repr(sig).encode("utf-8")).hexdigest()[:8]
        groups_by_style.setdefault(key, []).append(p.id)
    for key, members in groups_by_style.items():
        links.append(AssemblyLink(type=AssemblyType.SAME_STYLE, key=key, members=members, confidence=1.0, writer=f"{NAME}.same_style@{VERSION}"))

    # ---------- s4.order ----------
    items = [(b.quad, b.id, b.z) for b in text_boxes] + [(i.quad, i.id, i.z) for i in elements.images] + [(s.quad, s.id, s.z) for s in elements.shapes] + [(t.quad, t.id, t.z) for t in tables]
    reading = xy_cut([(i[1], i[0].bbox) for i in items if not i[1].split("-")[1].startswith("shp")], c.get("xy_cut_min_gap", 0.006))
    reading += [i[1] for i in sorted(items, key=lambda i: (i[0].bbox[1], i[0].bbox[0])) if i[1] not in reading]
    z_order = [i[1] for i in sorted(items, key=lambda i: i[2])]
    guides_x = _guides([i[0].bbox[0] for i in items] + [i[0].bbox[2] for i in items], c["left_align_tolerance"])
    guides_y = _guides([i[0].bbox[1] for i in items] + [i[0].bbox[3] for i in items], c["left_align_tolerance"])
    # ---------- s4.ai_roles：規則判斷不確定時，請大型 AI 看編號標記圖決定角色與閱讀順序 ----------
    if ai is not None and page_image is not None and ai.feature("structure"):
        paragraphs, reading, ai_note = _ai_roles_order(ai, page_image, paragraphs, text_boxes, elements, tables, reading, spec, cfg)
        if ai_note:
            notes.append(ai_note)

    layout = PageLayout(
        page_index=page, paragraphs=paragraphs, text_boxes=text_boxes, reading_order=reading, z_order=z_order,
        links=links, guides_x=guides_x, guides_y=guides_y, notes=notes, tables=tables,
    )
    return _apply_corrections(layout, corrections)


def _covered(segments: list[tuple[float, float, float, float]], vertical: bool, at: float, lo: float, hi: float, tol: float) -> bool:
    """在位置 at、範圍 lo 到 hi 之間，是否有格線線段蓋住至少六成。"""
    need = 0.6 * (hi - lo)
    got = 0.0
    for x0, y0, x1, y1 in segments:
        if vertical and abs(x0 - x1) <= tol and abs((x0 + x1) / 2 - at) <= tol:
            a, b = min(y0, y1), max(y0, y1)
        elif not vertical and abs(y0 - y1) <= tol and abs((y0 + y1) / 2 - at) <= tol:
            a, b = min(x0, x1), max(x0, x1)
        else:
            continue
        got += max(0.0, min(b, hi) - max(a, lo))
    return got >= need


def _tables(elements: ElementList, lines: list[TextLine], spec: PageSpec, c: dict) -> tuple[list[TableStructure], list[Paragraph], set[str]]:
    """s4.tables：③的表格區 → 列、欄、合併儲存格、表頭，並把文字行分給儲存格。

    合併儲存格的判斷：相鄰兩格之間的格線若不存在（線段蓋不到六成），兩格就是同一格。
    回傳（表格結構、儲存格內的段落、被表格吸收的文字行編號）。"""
    step = f"{NAME}.tables@{VERSION}"
    tol = c.get("table_line_tolerance", 0.004)
    out: list[TableStructure] = []
    pars: list[Paragraph] = []
    used: set[str] = set()
    for reg in elements.table_regions:
        ys, xs = reg.h_lines, reg.v_lines
        nr, nc = len(ys) - 1, len(xs) - 1
        parent = {(r, k): (r, k) for r in range(nr) for k in range(nc)}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        if reg.ruled:
            for r in range(nr):
                for k in range(nc - 1):  # 左右相鄰：看中間的直線
                    if not _covered(reg.segments, True, xs[k + 1], ys[r], ys[r + 1], tol):
                        parent[find((r, k + 1))] = find((r, k))
            for r in range(nr - 1):
                for k in range(nc):  # 上下相鄰：看中間的橫線
                    if not _covered(reg.segments, False, ys[r + 1], xs[k], xs[k + 1], tol):
                        parent[find((r + 1, k))] = find((r, k))
        groups: dict[tuple, list[tuple[int, int]]] = {}
        for key in parent:
            groups.setdefault(find(key), []).append(key)
        spans = []
        for members in groups.values():
            r0, r1 = min(m[0] for m in members), max(m[0] for m in members)
            k0, k1 = min(m[1] for m in members), max(m[1] for m in members)
            if len(members) == (r1 - r0 + 1) * (k1 - k0 + 1):
                spans.append((r0, k0, r1 - r0 + 1, k1 - k0 + 1))
            else:  # 不是長方形的合併：拆回單格
                spans += [(m[0], m[1], 1, 1) for m in members]
        cells: list[TableCell] = []
        for r, k, rs, cs in sorted(spans):
            q = Quad.from_rect(xs[k], ys[r], xs[k + cs], ys[r + rs])
            l, t, rr, b = q.bbox
            mine = [ln for ln in lines if ln.id not in used and l <= ln.quad.center.x <= rr and t <= ln.quad.center.y <= b]
            mine.sort(key=lambda ln: (round(ln.baseline[0].y, 4), ln.baseline[0].x))
            used.update(ln.id for ln in mine)
            pids = []
            for g in _cell_groups(mine):
                pq = union_quad([ln.quad for ln in g])
                inner = [b2.baseline[0].y - b1.baseline[0].y for b1, b2 in zip(g, g[1:])]
                pid = make_element_id(spec.page_index, "par", pq, g[0].id)
                pars.append(Paragraph(
                    id=pid, line_ids=[ln.id for ln in g], quad=pq, alignment=_cell_alignment(g, q, tol),
                    role=measured(Role.BODY, 0.6, Provenance.INFERRED, step), line_pitch=statistics.median(inner) if inner else None,
                    joiners=[_joiner(a.text, b2.text) for a, b2 in zip(g, g[1:])],
                ))
                pids.append(pid)
            cells.append(TableCell(row=r, col=k, row_span=rs, col_span=cs, quad=q, paragraph_ids=pids))
        # 表頭：第一列的字全是粗體，或第一列有自己的底色
        by_id = {ln.id: ln for ln in lines}
        par_by_id = {p.id: p for p in pars}
        first = [cl for cl in cells if cl.row == 0]
        first_lines = [by_id[lid] for cl in first for pid in cl.paragraph_ids for lid in par_by_id[pid].line_ids]
        bold = bool(first_lines) and all(r.bold.value for ln in first_lines for r in ln.runs)
        filled = any(abs(f[0].center.y - (ys[0] + ys[1]) / 2) < (ys[1] - ys[0]) / 2 for f in reg.cell_fills) and not any(abs(f[0].center.y - (ys[-2] + ys[-1]) / 2) < (ys[-1] - ys[-2]) / 2 for f in reg.cell_fills)
        header_rows = 1 if (bold or filled) and nr > 1 else 0
        if header_rows:
            cells = [cl.model_copy(update={"header": cl.row < header_rows}) for cl in cells]
        out.append(TableStructure(
            id=reg.id.replace("-tbl-", "-table-"), region_id=reg.id, quad=reg.quad, z=reg.z, row_lines=ys, col_lines=xs,
            cells=cells, header_rows=header_rows, ruled=reg.ruled, confidence=reg.confidence * (0.95 if all(cs == 1 and rs == 1 for _, _, rs, cs in spans) else 0.8),
        ))
    return out, pars, used


def _cell_groups(lines: list[TextLine]) -> list[list[TextLine]]:
    """儲存格內的行 → 段落：行距明顯變大、或字級改變就分段。"""
    groups: list[list[TextLine]] = []
    for ln in lines:
        if groups:
            prev = groups[-1][-1]
            pitch = ln.baseline[0].y - prev.baseline[0].y
            if 0 < pitch <= 1.6 * max(prev.font_size, ln.font_size) and abs(ln.font_size - prev.font_size) <= 0.05 * prev.font_size:
                groups[-1].append(ln)
                continue
        groups.append([ln])
    return groups


def _cell_alignment(lines: list[TextLine], cell: Quad, tol: float):
    step = f"{NAME}.align@{VERSION}"
    l, _, r, _ = cell.bbox
    lefts = [ln.quad.bbox[0] - l for ln in lines]
    rights = [r - ln.quad.bbox[2] for ln in lines]
    if all(abs(a - b) <= max(tol, 0.08 * (a + b)) for a, b in zip(lefts, rights)) and min(lefts) > 2 * tol:
        return measured(Align.CENTER, 0.75, Provenance.MEASURED, step)
    if all(b < a * 0.6 for a, b in zip(lefts, rights)):
        return measured(Align.RIGHT, 0.75, Provenance.MEASURED, step)
    return measured(Align.LEFT, 0.8, Provenance.MEASURED, step)


def _alignment(group: list[TextLine], block: list[TextLine], spec: PageSpec, c: dict):
    step = f"{NAME}.align@{VERSION}"
    tol = c["left_align_tolerance"]
    ref = group if len(group) > 1 else block
    if len(ref) > 1:
        lefts = [t.quad.bbox[0] for t in ref]
        rights = [t.quad.bbox[2] for t in ref]
        centers = [(a + b) / 2 for a, b in zip(lefts, rights)]
        l_ok = max(lefts) - min(lefts) <= tol
        r_ok = max(rights) - min(rights) <= tol
        c_ok = max(centers) - min(centers) <= tol
        body_r_ok = len(ref) > 2 and max(rights[:-1]) - min(rights[:-1]) <= tol
        if l_ok and r_ok:
            return measured(Align.LEFT, 0.6, Provenance.MEASURED, step)
        if l_ok and body_r_ok and len(group) > 2:
            return measured(Align.JUSTIFY, 0.6, Provenance.MEASURED, step)
        if l_ok:
            return measured(Align.LEFT, 0.9, Provenance.MEASURED, step)
        if c_ok:
            return measured(Align.CENTER, 0.85, Provenance.MEASURED, step)
        if r_ok:
            return measured(Align.RIGHT, 0.85, Provenance.MEASURED, step)
        return measured(Align.LEFT, 0.4, Provenance.INFERRED, step)
    # 單行：只有「置中在頁面正中」這個線索
    l, _, r, _ = group[0].quad.bbox
    if abs((l + r) / 2 - 0.5) <= tol and l > 0.1:
        return measured(Align.CENTER, 0.6, Provenance.INFERRED, step)
    return measured(Align.LEFT, 0.5, Provenance.INFERRED, step)


def _body_below(p: Paragraph, paragraphs: list[Paragraph], by_line: dict[str, TextLine], body: float) -> bool:
    """這一段底下（左右範圍有重疊的同一欄裡）還有一般字級的內文嗎？有的話它就不是頁尾。
    例如履歷頁面下方的小標題「EDUCATION」：字比內文小一點、又在頁面最下方，但底下還有學歷內容，是標題不是頁尾。
    頁尾自己可能有好幾行小字，所以底下只有小字（同樣不到內文 0.95 倍）時仍算頁尾。"""
    l, _, r, b = p.quad.bbox
    for q in paragraphs:
        if q is p:
            continue
        ql, qt, qr, _ = q.quad.bbox
        if qt < b - 1e-6 or min(r, qr) - max(l, ql) <= 0:
            continue
        if by_line[q.line_ids[0]].font_size >= body * 0.95:
            return True
    return False


def _roles(paragraphs: list[Paragraph], by_line: dict[str, TextLine], spec: PageSpec, c: dict) -> list[Paragraph]:
    step = f"{NAME}.roles@{VERSION}"
    if not paragraphs:
        return paragraphs
    weights: dict[float, int] = {}
    for p in paragraphs:
        for lid in p.line_ids:
            line = by_line[lid]
            weights[round(line.font_size, 6)] = weights.get(round(line.font_size, 6), 0) + len(line.text)
    body = max(weights, key=lambda k: weights[k])
    biggest = max(weights)
    out = []
    title_done = False
    for p in sorted(paragraphs, key=lambda p: p.quad.bbox[1]):
        first = by_line[p.line_ids[0]]
        text = "".join(by_line[i].text for i in p.line_ids).strip()
        size = first.font_size
        role, conf = Role.BODY, 0.6
        in_bottom = p.quad.bbox[1] > spec.aspect_ratio * (1 - c["page_number_band"])
        if in_bottom and len(text) <= 12 and _PAGE_NUMBER.match(text):
            role, conf = Role.PAGE_NUMBER, 0.85
        elif not title_done and size >= biggest * 0.999 and size >= body * c["title_size_ratio"] and p.quad.bbox[1] < spec.aspect_ratio * 0.5:
            role, conf = Role.TITLE, 0.75
            title_done = True
        elif size >= body * c["title_size_ratio"]:
            role, conf = Role.SUBTITLE, 0.5
        elif in_bottom and size < body * 0.95 and not _body_below(p, paragraphs, by_line, body):
            role, conf = Role.FOOTER, 0.5
        out.append(p.model_copy(update={"role": measured(role, conf, Provenance.INFERRED, step)}))
    order = {p.id: i for i, p in enumerate(paragraphs)}
    return sorted(out, key=lambda p: order[p.id])


_AI_ROLE = {
    "title": Role.TITLE, "subtitle": Role.SUBTITLE, "body": Role.BODY, "caption": Role.CAPTION, "header": Role.HEADER,
    "footer": Role.FOOTER, "page_number": Role.PAGE_NUMBER, "footnote": Role.FOOTNOTE, "list_item": Role.BODY,
    "table": Role.BODY, "figure": Role.CAPTION, "other": Role.UNKNOWN,
}


def _ai_roles_order(ai, page_image, paragraphs, text_boxes, elements, tables, reading, spec, cfg):
    """把段落、圖片、表格依目前的閱讀順序編號畫在頁面上，請 AI 回答每段的角色與閱讀順序。
    觸發條件：有角色信心值低於門檻的段落，或頁面上有並排的文字框（可能多欄）。AI 只回答編號與標籤。"""
    from ..ai.marks import render_marks
    from ..ai.tasks import roles_order_task

    c = cfg.section("structure")
    table_par_ids = {pid for t in tables for cell in t.cells for pid in cell.paragraph_ids}
    flow = [p for p in paragraphs if p.id not in table_par_ids]
    if len(flow) < int(c.get("ai_min_paragraphs", 2)):
        return paragraphs, reading, None
    low = [p for p in flow if p.role.confidence < c.get("ai_role_confidence", 0.7) and not p.role.locked]
    boxes = [b.quad.bbox for b in text_boxes]
    side_by_side = any(a[2] <= b[0] and min(a[3], b[3]) - max(a[1], b[1]) > 0.02 for a in boxes for b in boxes if a is not b)
    if not low and not side_by_side:
        return paragraphs, reading, None
    box_of = {pid: b.id for b in text_boxes for pid in b.paragraph_ids}
    by_line = {t.id: t for t in elements.texts}
    pos = {eid: i for i, eid in enumerate(reading)}
    ordered = sorted(flow, key=lambda p: (pos.get(box_of.get(p.id), 1e9), p.quad.bbox[1]))
    items_q = [(p.id, p.quad) for p in ordered] + [(im.id, im.quad) for im in elements.images] + [(t.id, t.quad) for t in tables]
    png, mapping = render_marks(page_image, items_q, max_long_edge=int(ai.cfg.get("max_image_long_edge", 2576)))
    kinds = {p.id: "paragraph" for p in ordered} | {im.id: "image" for im in elements.images} | {t.id: "table" for t in tables}
    items = []
    for n, eid in mapping.items():
        it = {"n": n, "kind": kinds[eid]}
        if kinds[eid] == "paragraph":
            par = next(p for p in ordered if p.id == eid)
            it["text"] = "".join(by_line[lid].text for lid in par.line_ids)[:80]
        items.append(it)
    ans = ai.ask(roles_order_task(png, items, spec.page_index))
    if ans is None:
        return paragraphs, reading, None
    step = f"{NAME}.ai_roles@{VERSION}"
    role_of = {mapping[r["n"]]: _AI_ROLE[r["role"]] for r in ans["roles"]}
    new_pars = [p.model_copy(update={"role": measured(role_of[p.id], 0.85, Provenance.AI, step)}) if p.id in role_of and not p.role.locked else p for p in paragraphs]
    order: list[str] = []
    for n in ans["reading_order"]:
        eid = mapping[n]
        eid = box_of.get(eid, eid)  # 段落 → 所屬的文字框
        if eid not in order:
            order.append(eid)
    order += [eid for eid in reading if eid not in order]
    return new_pars, order, "段落角色與閱讀順序由大型 AI 依編號標記圖判斷。"


def xy_cut(items: list[tuple[str, tuple[float, float, float, float]]], min_gap: float = 0.006) -> list[str]:
    """閱讀順序：遞迴 XY 切割。先找能把元素分成上下兩群的水平空白帶（最寬者），找不到再找左右兩群的垂直空白帶；
    上群先於下群、左群先於右群。這樣兩欄的頁面會先讀完左欄再讀右欄，而跨欄的標題仍在最前面。"""
    if len(items) <= 1:
        return [i[0] for i in items]

    def gaps(axis: int) -> list[tuple[float, float]]:
        spans = sorted((b[axis], b[axis + 2]) for _, b in items)
        out, end = [], spans[0][1]
        for a, b2 in spans[1:]:
            if a - end > min_gap:
                out.append((end, a))
            end = max(end, b2)
        return out

    for axis in (1, 0):  # 先上下（y），再左右（x）
        g = gaps(axis)
        if g:
            lo, hi = max(g, key=lambda t: t[1] - t[0])
            cut = (lo + hi) / 2
            first = [i for i in items if (i[1][axis] + i[1][axis + 2]) / 2 < cut]
            second = [i for i in items if (i[1][axis] + i[1][axis + 2]) / 2 >= cut]
            if first and second:
                return xy_cut(first, min_gap) + xy_cut(second, min_gap)
    # 切不開（元素互相重疊）：由上而下、由左而右
    return [i[0] for i in sorted(items, key=lambda i: (round(i[1][1], 3), i[1][0]))]


def _guides(values: list[float], tol: float) -> list[float]:
    """至少兩個元素共用的邊，就是一條對齊線。"""
    out: list[float] = []
    for v in sorted(values):
        if out and abs(v - out[-1]) <= tol:
            continue
        if sum(1 for x in values if abs(x - v) <= tol) >= 2:
            out.append(v)
    return out


def _apply_corrections(layout: PageLayout, corrections: list[Correction]) -> PageLayout:
    """④接受的修正：改段落的角色（field="role"）或對齊（field="align"）。"""
    if not corrections:
        return layout
    paragraphs = list(layout.paragraphs)
    for c in corrections:
        for i, p in enumerate(paragraphs):
            if p.id != c.element_id:
                continue
            if c.field == "role":
                paragraphs[i] = p.model_copy(update={"role": measured(Role(c.new_value), 1.0, Provenance.USER, "user").model_copy(update={"locked": True})})
            elif c.field == "align":
                paragraphs[i] = p.model_copy(update={"alignment": measured(Align(c.new_value), 1.0, Provenance.USER, "user").model_copy(update={"locked": True})})
    return layout.model_copy(update={"paragraphs": paragraphs})


# ====================================================================== 跨頁


def run_document(pages: list[PageLayout], elements: list[ElementList], cfg: Config) -> Layout:
    """s4.cross_page：在多數頁面的相同位置重複出現的元素，列為母片候選。"""
    c = cfg.section("structure")
    candidates: list[MasterCandidate] = []
    if len(pages) >= c["master_min_pages"]:
        seen: dict[str, list[tuple[int, str]]] = {}
        for el in elements:
            for t in el.texts:
                if t.hidden:
                    continue
                b = t.quad.bbox
                key = f"t|{round(b[0], 3)}|{round(b[1], 3)}|{t.text.strip()}"
                seen.setdefault(key, []).append((el.page_index, t.id))
            for im in el.images:
                b = im.quad.bbox
                key = f"i|{round(b[0], 3)}|{round(b[1], 3)}|{im.image.sha256[:16]}"
                seen.setdefault(key, []).append((el.page_index, im.id))
            for s in el.shapes:
                b = s.quad.bbox
                key = f"s|{round(b[0], 3)}|{round(b[1], 3)}|{round(b[2], 3)}|{round(b[3], 3)}|{s.fill.hex if s.fill else ''}"
                seen.setdefault(key, []).append((el.page_index, s.id))
        for key, hits in seen.items():
            if len({h[0] for h in hits}) >= max(2, c["master_repeat_ratio"] * len(pages)):
                candidates.append(MasterCandidate(key=hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], element_ids=[h[1] for h in hits], page_indexes=[h[0] for h in hits]))
    return Layout(pages=pages, master_candidates=candidates)
