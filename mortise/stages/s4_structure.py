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
尚未實作：清單階層、分欄、表格結構、圖說配對、網格編號、大型 AI 仲裁。
"""

from __future__ import annotations

import hashlib
import re
import statistics

from ..artifacts import (
    Align, AssemblyLink, AssemblyType, Correction, ElementList, Layout, MasterCandidate, PageLayout, PageSpec, Paragraph,
    Provenance, Role, TextBox, TextLine, make_element_id, measured, union_quad,
)
from ..config import Config
from ..tools.fonts import is_cjk

NAME = "s4"
VERSION = "1"

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


def run_page(elements: ElementList, spec: PageSpec, cfg: Config, corrections: list[Correction]) -> PageLayout:
    c = cfg.section("structure")
    page = spec.page_index
    step = f"{NAME}.text_flow@{VERSION}"
    notes: list[str] = []
    lines = [t for t in elements.texts if not t.hidden and t.text.strip()]
    lines.sort(key=lambda t: (round(t.baseline[0].y, 4), t.baseline[0].x))

    # ---------- s4.text_flow：行 → 區塊 ----------
    blocks: list[list[TextLine]] = []
    for line in lines:
        best, best_gap = None, None
        if abs(line.quad.angle_deg) < 0.5:
            for block in blocks:
                last = block[-1]
                if abs(last.quad.angle_deg) >= 0.5:
                    continue
                size = last.font_size
                if abs(line.font_size - size) > c["size_tolerance"] * size:
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
            q = union_quad([t.quad for t in g])
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
        bq = union_quad([t.quad for t in block])
        bid = make_element_id(page, "box", bq, par_ids[0])
        text_boxes.append(TextBox(id=bid, quad=bq, z=min(t.z for t in block), paragraph_ids=par_ids, space_between=gaps_between))

    # ---------- s4.roles ----------
    paragraphs = _roles(paragraphs, {t.id: t for t in lines}, spec, c)

    # ---------- s4.contain ----------
    for box in text_boxes:
        l, t, r, b = box.quad.bbox
        for shape in elements.shapes:
            sl, st, sr, sb = shape.quad.bbox
            if sl - 0.002 <= l and st - 0.002 <= t and r <= sr + 0.002 and b <= sb + 0.002 and shape.z < box.z:
                links.append(AssemblyLink(type=AssemblyType.CONTAIN, key=shape.id, members=[shape.id, box.id], confidence=0.7, writer=f"{NAME}.contain@{VERSION}"))

    # ---------- s4.same_style ----------
    by_line = {t.id: t for t in lines}
    groups_by_style: dict[str, list[str]] = {}
    for p in paragraphs:
        sig = _style_sig(by_line[p.line_ids[0]])
        key = hashlib.sha1(repr(sig).encode("utf-8")).hexdigest()[:8]
        groups_by_style.setdefault(key, []).append(p.id)
    for key, members in groups_by_style.items():
        links.append(AssemblyLink(type=AssemblyType.SAME_STYLE, key=key, members=members, confidence=1.0, writer=f"{NAME}.same_style@{VERSION}"))

    # ---------- s4.order ----------
    items = [(b.quad, b.id, b.z) for b in text_boxes] + [(i.quad, i.id, i.z) for i in elements.images] + [(s.quad, s.id, s.z) for s in elements.shapes]
    reading = [i[1] for i in sorted(items, key=lambda i: (round(i[0].bbox[1], 2), i[0].bbox[0]))]
    z_order = [i[1] for i in sorted(items, key=lambda i: i[2])]
    guides_x = _guides([i[0].bbox[0] for i in items] + [i[0].bbox[2] for i in items], c["left_align_tolerance"])
    guides_y = _guides([i[0].bbox[1] for i in items] + [i[0].bbox[3] for i in items], c["left_align_tolerance"])
    if elements.table_regions:
        notes.append("表格結構（列、欄、合併儲存格）尚未實作。")

    layout = PageLayout(
        page_index=page, paragraphs=paragraphs, text_boxes=text_boxes, reading_order=reading, z_order=z_order,
        links=links, guides_x=guides_x, guides_y=guides_y, notes=notes,
    )
    return _apply_corrections(layout, corrections)


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
        elif in_bottom and size < body * 0.95:
            role, conf = Role.FOOTER, 0.5
        out.append(p.model_copy(update={"role": measured(role, conf, Provenance.INFERRED, step)}))
    order = {p.id: i for i, p in enumerate(paragraphs)}
    return sorted(out, key=lambda p: order[p.id])


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
