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
尚未實作：母片定案、表格設定、數字格式、Excel 公式推論、顏色吸附、比例尺一致性檢查。
"""

from __future__ import annotations

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
VERSION = "1"

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


def _box(q: Quad, w: float) -> Box:
    """四角 → 位置與大小（pt）。旋轉的元素以中心為準還原成未旋轉的框加角度。"""
    cx, cy = q.center.x * w, q.center.y * w
    bw, bh = q.width * w, q.height * w
    angle = q.angle_deg
    return Box(x=cx - bw / 2, y=cy - bh / 2, w=bw, h=bh, rotation=0.0 if abs(angle) < 0.05 else angle)


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

    pages: list[StyledPage] = []
    style_counter: Counter = Counter()
    style_role: dict[tuple, Role] = {}
    color_counter: Counter = Counter()

    for spec, el, pl in zip(specs, elements, layout.pages):
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
                runs = _runs(p_lines, p.joiners, w, c, subs)
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
            box = _box(tb.quad, w)
            single = sum(sp.source_line_count for sp in sps) == 1
            wrap = not (single and c["single_line_no_wrap"])
            if wrap:
                # 多留一點寬度，降低目標軟體因字寬略有出入而提早折行的機率；位置依對齊方式錨定
                slack = c["box_width_slack_pt"]
                align = sps[0].align
                dx = 0.0 if align in (Align.LEFT, Align.JUSTIFY) else (-slack if align == Align.RIGHT else -slack / 2)
                box = box.model_copy(update={"x": box.x + dx, "w": box.w + slack})
            text_boxes.append(StyledTextBox(id=tb.id, box=box, z=tb.z, paragraphs=sps, wrap=wrap))

        images = [StyledImage(id=im.id, box=_box(im.quad, w), z=im.z, image=im.image) for im in el.images]
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
                bx = _box(s.quad, w)
            if s.fill:
                color_counter[s.fill.hex] += 50
            shapes.append(StyledShape(id=s.id, box=bx, z=s.z, geometry=s.geometry.value, fill=s.fill, stroke=s.stroke, stroke_width_pt=(s.stroke_width * w) if s.stroke_width else None))
        bg = el.background
        pages.append(
            StyledPage(
                page_index=spec.page_index, width_pt=w, height_pt=h,
                background_color=bg.color if (bg.color and bg.color.hex != "FFFFFF") else None, background_image=bg.image,
                text_boxes=text_boxes, images=images, shapes=shapes, reading_order=pl.reading_order,
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

    styled = StyledDoc(target_mode=target_mode, source_app=source_app, pages=pages, styles=styles, theme_colors=theme, notes=notes)
    return _apply_corrections(styled, corrections, subs)


# ---------- s5.font_size、s5.font ----------


def _snap_size(pt: float, c: dict, source: Measured, step: str) -> Measured:
    grid = c["size_snap_step_pt"]
    snapped = round(pt / grid) * grid
    if abs(snapped - pt) <= c["size_snap_tolerance_pt"]:
        if abs(snapped - pt) < 1e-6 and source.source == Provenance.EXACT:
            return measured(float(snapped), 1.0, Provenance.EXACT, step)
        return measured(float(snapped), min(source.confidence, 0.95), Provenance.SNAPPED, step, error=abs(snapped - pt))
    return measured(round(pt, 3), source.confidence, source.source, step)  # 不吸附：保留原值


def _font(run: TextRun, subs: dict[str, str]) -> FontChoice:
    step = f"{NAME}.font@{VERSION}"
    family = run.font_candidates[0].name if run.font_candidates else "unknown"
    ea = has_cjk(run.text)
    vf, substituted = verification_font(family, subs, ea)
    return FontChoice(family=measured(family, 1.0 if run.font_candidates else 0.0, run.font_size.source, step), verification_family=vf, is_substituted=substituted, east_asian=ea)


def _runs(lines: list[TextLine], joiners: list[str], w: float, c: dict, subs: dict[str, str]) -> list[StyledRun]:
    """把一段的各行接起來，樣式相同的相鄰文字併成一段。"""
    step = f"{NAME}.font_size@{VERSION}"
    out: list[StyledRun] = []
    for i, line in enumerate(lines):
        for j, r in enumerate(line.runs):
            text = r.text
            if j == 0 and i > 0 and joiners[i - 1]:
                text = joiners[i - 1] + text
            sr = StyledRun(text=text, font=_font(r, subs), size_pt=_snap_size(r.font_size.value * w, c, r.font_size, step), bold=r.bold.value, italic=r.italic.value, color=r.color.value)
            if out and (out[-1].font.family.value, out[-1].size_pt.value, out[-1].bold, out[-1].italic, out[-1].color) == (sr.font.family.value, sr.size_pt.value, sr.bold, sr.italic, sr.color):
                out[-1] = out[-1].model_copy(update={"text": out[-1].text + text})
            else:
                out.append(sr)
    if out:  # 段尾的空白沒有意義
        out[-1] = out[-1].model_copy(update={"text": out[-1].text.rstrip()})
    return [r for r in out if r.text]


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
    font_family、font_size_pt、color（十六進位）、align；
    以及⑦驗證開的 width_adjust_pt（修正斷行）、offset_pt（平移文字框）。
    """
    if not corrections:
        return doc
    pages = []
    for page in doc.pages:
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
