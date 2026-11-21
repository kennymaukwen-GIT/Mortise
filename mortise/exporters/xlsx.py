"""Excel 輸出器（XlsxWriter，BSD-2-Clause；自檢讀回用 openpyxl，MIT）。

Excel 不是版面格式：只輸出成品文件中的表格區塊。每個 StyledTable 一張工作表，
工作表名稱「第 N 頁 表 M」。整份文件沒有表格時，退回成一張工作表，把各頁文字框的
段落依閱讀順序一列一段寫入，並記一筆「這份文件沒有偵測到表格」。

降階對照表（成品文件的概念 → XLSX 的物件 → 是否有損）：
  表格的列與欄            → 工作表的儲存格範圍（從 A1 起）            → 無損
  合併儲存格              → merge_range 合併範圍                       → 無損
  欄寬 col_widths_pt      → 欄寬（pt → 96 dpi 像素 → set_column_pixels，
                             檔案裡存字元單位，Calibri 11 最大數字寬 7 像素）→ 量化到整數像素（≤ 0.375 pt）
  列高 row_heights_pt     → 列高（pt，原值寫入）                       → 無損
  儲存格文字（單一樣式）  → 字串 + 儲存格格式                          → 無損
  一格內多段或多種樣式    → write_rich_string 多格式字串（段落以換行分隔）→ 無損
  數值 value + 數字格式   → 數值儲存格 + num_format                    → 無損
  公式 formula            → 公式（同時寫入顯示值當快取結果）           → 無損
  字型名稱                → 儲存格字型名稱（XLSX 只有一個名稱欄位，寫推論字型；
                             東亞文字的 run 另寫 charset 提示；關閉主題字型 scheme）→ 無損
  字級、粗斜體、文字顏色  → 儲存格格式／多格式字串的片段格式           → 無損；文字透明度捨棄（有損）
  水平對齊、垂直對齊      → 儲存格對齊（一格只有一種水平對齊）         → 一格內各段對齊不同時有損
  自動換行                → text_wrap（多段或原文多行時開）            → 無損
  底色                    → 純色填滿                                   → 無損；透明度捨棄（有損）
  框線（顏色、粗細）      → 每邊的線型等級（細 0.75、中 1.5、粗 2.25 pt）加顏色 → 粗細量化（差距超過容差才記）
  儲存格邊距              → 不輸出（Excel 沒有儲存格邊距）             → 有損
  行距、段前段後、首行縮排 → 不輸出                                     → 有損
  表頭列 header_rows      → 凍結窗格                                   → 無損
  表格在頁面上的位置、旋轉 → 不輸出                                    → 有損
  表格以外的元素（文字框、圖片、形狀、整頁底圖、底色）→ 不輸出（本格式的定位）→ 有損，每頁彙總一筆
  沒有表格的文件          → 單一工作表，一列一段文字                   → 有損（版面全部捨棄）
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional

import xlsxwriter

from ..artifacts import Align, Degradation, OutputFile, OutputFormat, StyledCell, StyledDoc, StyledPage, StyledParagraph, StyledRun, StyledTable
from ..config import Config
from ..pipeline.store import Store

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DEFAULT_PARAMS: dict[str, Any] = {
    # 欄寬換算：活頁簿預設字型固定為 Calibri 11，96 dpi 下最大數字寬度 7 像素（formats.md 3.6）
    "default_font": "Calibri",
    "default_font_size": 11,
    "screen_dpi": 96.0,
    "max_col_width_px": 255 * 7 + 5,  # Excel 欄寬上限 255 字元
    # 工作表名稱
    "sheet_name_template": "第 {page} 頁 表 {index}",
    "no_table_sheet_name": "文字內容",
    # 框線粗細等級：(XlsxWriter 線型編號, 代表粗細 pt)；細 1 像素、中 2 像素、粗 3 像素
    "border_levels": [(1, 0.75), (2, 1.5), (5, 2.25)],
    "border_quantize_tolerance_pt": 0.25,  # 量到的線寬與等級差距超過此值才記降級
    "freeze_header_rows": True,  # 表頭列凍結窗格
    "east_asian_charset": 136,  # 東亞文字 run 的字元集提示（136 = 繁體中文 Big5）；0 表示不寫
    "hide_gridlines": False,
    "no_table_max_col_width_pt": 540.0,  # 沒有表格時，文字欄的寬度上限（pt）
}

_ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_H_ALIGN = {Align.LEFT: "left", Align.CENTER: "center", Align.RIGHT: "right", Align.JUSTIFY: "justify"}
_V_ALIGN = {"top": "top", "middle": "vcenter", "center": "vcenter", "bottom": "bottom"}


def params(cfg: Optional[Config]) -> dict[str, Any]:
    """預設參數加上設定檔 [export.xlsx] 的覆寫。"""
    out = dict(DEFAULT_PARAMS)
    if cfg is not None:
        out.update(cfg.section("export").get("xlsx", {}) or {})
    return out


# ---------------------------------------------------------------- 單位與名稱


def pt_to_px(pt: float, dpi: float = 96.0) -> int:
    """pt 換成螢幕像素（Excel 欄寬以整數像素呈現）。"""
    return max(0, int(round(pt * dpi / 72.0)))


def excel_width_to_px(width: float, max_digit_width: int = 7) -> int:
    """檔案裡的欄寬（字元單位）換回像素：ECMA-376 / MS-OI29500 18.3.1.13 的公式。"""
    return int(((256 * width + int(128 / max_digit_width)) / 256) * max_digit_width)


def sanitize_sheet_name(name: str, used: set[str]) -> str:
    """Excel 工作表名稱：31 字元以內、不含 []:*?/\\、不以單引號開頭結尾、不分大小寫不得重複。"""
    base = _ILLEGAL_SHEET_CHARS.sub("_", name).strip().strip("'").strip() or "工作表"
    if base.lower() == "history":  # Excel 保留名稱
        base = "History_"
    base = base[:31]
    candidate = base
    n = 2
    while candidate.lower() in used:
        suffix = f" ({n})"
        candidate = base[: 31 - len(suffix)].rstrip() + suffix
        n += 1
    used.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------- 格式


class _Formats:
    """把格式屬性字典快取成 XlsxWriter 的 Format（同樣的屬性只建一次）。"""

    def __init__(self, wb: xlsxwriter.Workbook, p: dict[str, Any]):
        self.wb = wb
        self.p = p
        self._cache: dict[tuple, Any] = {}
        self.fonts: set[str] = set()

    def get(self, props: dict[str, Any]):
        key = tuple(sorted(props.items()))
        fmt = self._cache.get(key)
        if fmt is None:
            fmt = self.wb.add_format(dict(props))
            if "font_name" in props:
                fmt.set_font_scheme("none")  # 不讓 Excel 改用主題字型
                self.fonts.add(props["font_name"])
            self._cache[key] = fmt
        return fmt

    def run_props(self, run: StyledRun) -> dict[str, Any]:
        props: dict[str, Any] = {
            "font_name": run.font.family.value,
            "font_size": round(float(run.size_pt.value), 2),
            "bold": bool(run.bold),
            "italic": bool(run.italic),
            "font_color": "#" + run.color.hex,
        }
        charset = int(self.p.get("east_asian_charset") or 0)
        if run.font.east_asian and charset:
            props["font_charset"] = charset
        return props


def _run_style_key(run: StyledRun) -> tuple:
    return (run.font.family.value, round(float(run.size_pt.value), 2), run.bold, run.italic, run.color.hex, run.font.east_asian)


class _TableStats:
    """一張表內的降級統計，最後彙總成少數幾筆紀錄（避免每格一筆）。"""

    def __init__(self) -> None:
        self.border_quantized = 0
        self.border_max_err = 0.0
        self.alpha_text = 0
        self.alpha_fill = 0
        self.mixed_align = 0
        self.margins = 0
        self.para_spacing = 0
        self.skipped: list[str] = []
        self.width_clamped = 0


def _nearest_border(width_pt: float, p: dict[str, Any]) -> tuple[int, float]:
    levels = p["border_levels"]
    style, level_pt = min(levels, key=lambda lv: abs(lv[1] - width_pt))
    return int(style), abs(level_pt - width_pt)


def _cell_props(cell: StyledCell, paragraphs: list[StyledParagraph], fmts: _Formats, stats: _TableStats, default_wrap: Optional[bool] = None) -> dict[str, Any]:
    p = fmts.p
    props: dict[str, Any] = {}
    first_run = next((r for para in paragraphs for r in para.runs), None)
    if first_run is not None:
        props.update(fmts.run_props(first_run))
    else:
        props.update({"font_name": p["default_font"], "font_size": p["default_font_size"]})
    if paragraphs:
        aligns = {para.align for para in paragraphs}
        props["align"] = _H_ALIGN[paragraphs[0].align]
        if len(aligns) > 1:
            stats.mixed_align += 1
        if any(para.space_before_pt or para.space_after_pt or para.first_line_indent_pt or para.line_spacing is not None for para in paragraphs):
            stats.para_spacing += 1
    props["valign"] = _V_ALIGN.get(cell.v_align, "top")
    wrap = default_wrap if default_wrap is not None else (len(paragraphs) > 1 or any(para.source_line_count > 1 for para in paragraphs))
    if wrap:
        props["text_wrap"] = True
    if cell.fill is not None:
        props["pattern"] = 1
        props["bg_color"] = "#" + cell.fill.hex
        if cell.fill.a < 0.999:
            stats.alpha_fill += 1
    for side, border in zip(("top", "right", "bottom", "left"), cell.borders):
        if border is None or border.width_pt <= 0:
            continue
        style, err = _nearest_border(border.width_pt, p)
        props[side] = style
        props[f"{side}_color"] = "#" + border.color.hex
        if err > float(p["border_quantize_tolerance_pt"]):
            stats.border_quantized += 1
            stats.border_max_err = max(stats.border_max_err, err)
    if any(m > 0 for m in cell.margins_pt):
        stats.margins += 1
    if cell.number_format and (cell.value is not None or cell.formula):
        props["num_format"] = cell.number_format
    return props


def _rich_fragments(paragraphs: list[StyledParagraph], fmts: _Formats, stats: _TableStats) -> list[tuple[tuple, StyledRun, str]]:
    """把各段各 run 攤平成 (樣式鍵, run, 文字)；段落之間以換行接在下一段開頭。相鄰同樣式的片段合併。"""
    frags: list[tuple[tuple, StyledRun, str]] = []
    pending_nl = ""
    for i, para in enumerate(paragraphs):
        if i > 0:
            pending_nl += "\n"
        for run in para.runs:
            if run.color.a < 0.999:
                stats.alpha_text += 1
            text = pending_nl + run.text
            if not text:
                continue
            pending_nl = ""
            key = _run_style_key(run)
            if frags and frags[-1][0] == key:
                frags[-1] = (key, frags[-1][1], frags[-1][2] + text)
            else:
                frags.append((key, run, text))
    if pending_nl and frags:
        frags[-1] = (frags[-1][0], frags[-1][1], frags[-1][2] + pending_nl)
    return frags


def _write_cell(ws, row: int, col: int, cell: StyledCell, paragraphs: list[StyledParagraph], fmts: _Formats, stats: _TableStats, default_wrap: Optional[bool] = None) -> None:
    """寫一格：公式 → 數值 → 多格式字串 → 字串，依序判斷。"""
    props = _cell_props(cell, paragraphs, fmts, stats, default_wrap)
    cell_fmt = fmts.get(props)
    if cell.formula:
        formula = cell.formula if cell.formula.startswith("=") else "=" + cell.formula
        ws.write_formula(row, col, formula, cell_fmt, cell.value if cell.value is not None else 0)
        return
    if cell.value is not None:
        ws.write_number(row, col, float(cell.value), cell_fmt)
        return
    frags = _rich_fragments(paragraphs, fmts, stats)
    if not frags:
        ws.write_blank(row, col, None, cell_fmt)
    elif len(frags) == 1:
        ws.write_string(row, col, frags[0][2], cell_fmt)
    else:
        args: list[Any] = []
        for _, run, text in frags:
            args += [fmts.get(fmts.run_props(run)), text]
        args.append(cell_fmt)
        ws.write_rich_string(row, col, *args)


# ---------------------------------------------------------------- 表格與頁面


def _ordered_tables(page: StyledPage) -> list[StyledTable]:
    order = {eid: i for i, eid in enumerate(page.reading_order)}
    return sorted(page.tables, key=lambda t: (order.get(t.id, len(order)), t.box.y, t.box.x))


def _write_table(ws, table: StyledTable, fmts: _Formats, degradations: list[Degradation]) -> dict[str, int]:
    p = fmts.p
    stats = _TableStats()
    n_rows, n_cols = len(table.row_heights_pt), len(table.col_widths_pt)
    for c, w_pt in enumerate(table.col_widths_pt):
        px = pt_to_px(w_pt, p["screen_dpi"])
        if px > p["max_col_width_px"]:
            px = int(p["max_col_width_px"])
            stats.width_clamped += 1
        ws.set_column_pixels(c, c, px)
    for r, h_pt in enumerate(table.row_heights_pt):
        ws.set_row(r, round(float(h_pt), 2))
    covered: set[tuple[int, int]] = set()
    merges = 0
    for cell in sorted(table.cells, key=lambda c: (c.row, c.col)):
        r0, c0 = cell.row, cell.col
        r1 = min(r0 + max(cell.row_span, 1) - 1, n_rows - 1)
        c1 = min(c0 + max(cell.col_span, 1) - 1, n_cols - 1)
        if r0 < 0 or c0 < 0 or r0 >= n_rows or c0 >= n_cols or (r0, c0) in covered:
            stats.skipped.append(f"({r0},{c0})")
            continue
        span = {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        if span & covered:  # 與先前的合併範圍重疊：只寫左上角，不合併
            r1, c1 = r0, c0
            span = {(r0, c0)}
            stats.skipped.append(f"合併({cell.row},{cell.col})")
        covered |= span
        if (r1, c1) != (r0, c0):
            props = _cell_props(cell, cell.paragraphs, fmts, _TableStats())
            ws.merge_range(r0, c0, r1, c1, "", fmts.get(props))
            merges += 1
        _write_cell(ws, r0, c0, cell, cell.paragraphs, fmts, stats)
    if table.header_rows > 0 and p["freeze_header_rows"]:
        ws.freeze_panes(min(table.header_rows, n_rows), 0)
    if p["hide_gridlines"]:
        ws.hide_gridlines(2)
    tid = table.id
    degradations.append(Degradation(element_id=tid, concept="表格在頁面上的位置", action="Excel 工作表從 A1 起排，頁面位置不輸出", lossy=True))
    if table.box.rotation:
        degradations.append(Degradation(element_id=tid, concept="表格旋轉", action=f"旋轉 {table.box.rotation:.1f} 度不輸出，以水平表格寫入", lossy=True))
    if stats.border_quantized:
        degradations.append(Degradation(element_id=tid, concept="框線粗細", action=f"{stats.border_quantized} 條框線量化到 Excel 的固定等級（最大誤差 {stats.border_max_err:.2f} pt）", lossy=True))
    if stats.alpha_fill:
        degradations.append(Degradation(element_id=tid, concept="底色透明度", action=f"{stats.alpha_fill} 格以不透明底色輸出", lossy=True))
    if stats.alpha_text:
        degradations.append(Degradation(element_id=tid, concept="文字透明度", action=f"{stats.alpha_text} 段文字以不透明顏色輸出", lossy=True))
    if stats.mixed_align:
        degradations.append(Degradation(element_id=tid, concept="一格內各段對齊不同", action=f"{stats.mixed_align} 格只能用第一段的水平對齊", lossy=True))
    if stats.margins:
        degradations.append(Degradation(element_id=tid, concept="儲存格邊距", action=f"Excel 沒有儲存格邊距，{stats.margins} 格以 Excel 預設邊距顯示", lossy=True))
    if stats.para_spacing:
        degradations.append(Degradation(element_id=tid, concept="行距、段前段後、首行縮排", action=f"Excel 儲存格不支援，{stats.para_spacing} 格未輸出這些設定", lossy=True))
    if stats.width_clamped:
        degradations.append(Degradation(element_id=tid, concept="欄寬", action=f"{stats.width_clamped} 欄超過 Excel 上限 255 字元，截到上限", lossy=True))
    if stats.skipped:
        degradations.append(Degradation(element_id=tid, concept="儲存格超出表格或與合併範圍重疊", action="略過：" + "、".join(stats.skipped[:20]), lossy=True))
    return {"rows": n_rows, "cols": n_cols, "merges": merges}


def _page_drop_summary(page: StyledPage, degradations: list[Degradation], include_text: bool) -> None:
    """表格以外的元素不輸出：每頁彙總一筆。"""
    parts = []
    if include_text and page.text_boxes:
        parts.append(f"文字框 {len(page.text_boxes)}")
    if page.images:
        parts.append(f"圖片 {len(page.images)}")
    if page.shapes:
        parts.append(f"形狀 {len(page.shapes)}")
    if page.background_image is not None:
        parts.append("整頁底圖")
    if page.background_color is not None:
        parts.append("頁面底色")
    if parts:
        degradations.append(Degradation(element_id=f"page-{page.page_index + 1}", concept="表格以外的元素（" + "、".join(parts) + "）", action="Excel 只輸出表格區塊，這些元素不輸出", lossy=True))


def _text_paragraphs_in_reading_order(page: StyledPage) -> list[StyledParagraph]:
    order = {eid: i for i, eid in enumerate(page.reading_order)}
    boxes = sorted(page.text_boxes, key=lambda t: (order.get(t.id, len(order)), t.box.y, t.box.x))
    return [para for tb in boxes for para in tb.paragraphs]


def _write_text_only(wb, doc: StyledDoc, fmts: _Formats, degradations: list[Degradation], used: set[str]) -> dict[str, int]:
    """沒有表格：一張工作表，各頁文字框依閱讀順序一列一段。"""
    p = fmts.p
    ws = wb.add_worksheet(sanitize_sheet_name(p["no_table_sheet_name"], used))
    widest = max((tb.box.w for page in doc.pages for tb in page.text_boxes), default=200.0)
    ws.set_column_pixels(0, 0, min(pt_to_px(min(widest, p["no_table_max_col_width_pt"]), p["screen_dpi"]), int(p["max_col_width_px"])))
    stats = _TableStats()
    row = 0
    for page in doc.pages:
        for para in _text_paragraphs_in_reading_order(page):
            if not "".join(r.text for r in para.runs).strip():
                continue
            pseudo = StyledCell(row=row, col=0, paragraphs=[para])
            _write_cell(ws, row, 0, pseudo, [para], fmts, stats, default_wrap=True)
            row += 1
        _page_drop_summary(page, degradations, include_text=False)
    degradations.append(Degradation(element_id="document", concept="表格", action="這份文件沒有偵測到表格；改把各頁文字框的段落依閱讀順序一列一段寫入單一工作表，文字框的位置與版面不輸出", lossy=True))
    if stats.alpha_text:
        degradations.append(Degradation(element_id="document", concept="文字透明度", action=f"{stats.alpha_text} 段文字以不透明顏色輸出", lossy=True))
    if stats.para_spacing:
        degradations.append(Degradation(element_id="document", concept="行距、段前段後、首行縮排", action="Excel 儲存格不支援，未輸出", lossy=True))
    return {"rows": row, "cols": 1, "merges": 0}


# ---------------------------------------------------------------- 自檢


def _selfcheck(data: bytes, expected: list[tuple[str, dict[str, int]]]) -> tuple[bool, list[str]]:
    """重新讀入：確認能開、工作表數與名稱相符、合併範圍數相符、列欄數不少於預期。"""
    notes: list[str] = []
    try:
        import openpyxl  # MIT；只用來讀回
    except ImportError:  # pragma: no cover - 沒有 openpyxl 時只做 ZIP 與 XML 檢查
        import zipfile

        from lxml import etree

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                root = etree.fromstring(z.read("xl/workbook.xml"))
            n = len(root.findall(".//{*}sheet"))
        except Exception as e:
            return False, [f"自檢：輸出檔無法重新開啟（{e}）。"]
        ok = n == len(expected)
        if not ok:
            notes.append(f"自檢：工作表數 {n} 與預期 {len(expected)} 不符。")
        notes.append("自檢：沒有 openpyxl，只檢查了工作表數。")
        return ok, notes
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data))
    except Exception as e:
        return False, [f"自檢：輸出檔無法重新開啟（{e}）。"]
    ok = True
    if len(wb.sheetnames) != len(expected):
        ok = False
        notes.append(f"自檢：工作表數 {len(wb.sheetnames)} 與預期 {len(expected)} 不符。")
    for name, exp in expected:
        if name not in wb.sheetnames:
            ok = False
            notes.append(f"自檢：找不到工作表「{name}」。")
            continue
        ws = wb[name]
        if len(ws.merged_cells.ranges) != exp["merges"]:
            ok = False
            notes.append(f"自檢：工作表「{name}」合併範圍 {len(ws.merged_cells.ranges)} 個，預期 {exp['merges']} 個。")
        if exp["rows"] and (ws.max_row < exp["rows"] or ws.max_column < exp["cols"]):
            ok = False
            notes.append(f"自檢：工作表「{name}」範圍 {ws.max_row}×{ws.max_column} 小於預期 {exp['rows']}×{exp['cols']}。")
    wb.close()
    return ok, notes


# ---------------------------------------------------------------- 入口


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    p = params(cfg)
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False, "strings_to_formulas": False, "strings_to_urls": False})
    wb.set_properties({"creator": "Mortise", "comments": "由 Mortise 從成品文件的表格區塊產生"})
    fmts = _Formats(wb, p)
    degradations: list[Degradation] = []
    used: set[str] = set()
    expected: list[tuple[str, dict[str, int]]] = []
    tables = [(page, t) for page in doc.pages for t in _ordered_tables(page)]
    if tables:
        for page in doc.pages:
            for m, table in enumerate(_ordered_tables(page), start=1):
                name = sanitize_sheet_name(p["sheet_name_template"].format(page=page.page_index + 1, index=m), used)
                ws = wb.add_worksheet(name)
                expected.append((name, _write_table(ws, table, fmts, degradations)))
            _page_drop_summary(page, degradations, include_text=True)
    else:
        expected.append((p["no_table_sheet_name"], _write_text_only(wb, doc, fmts, degradations, used)))
        expected[-1] = (wb.worksheets()[-1].name, expected[-1][1])
    wb.close()
    data = buf.getvalue()
    ok, notes = _selfcheck(data, expected)
    notes.append(f"欄寬以活頁簿預設字型 {p['default_font']} {p['default_font_size']} pt 換算；使用者電腦沒有此字型時欄寬會整體偏移（替代字型 Carlito 字寬相容）。")
    blob = store.put_blob(data, XLSX_MEDIA_TYPE, "xlsx")
    fonts = sorted(fmts.fonts | {p["default_font"]})
    return OutputFile(format=OutputFormat.XLSX, file=blob, filename=f"{basename}.xlsx", degradations=degradations, fonts_used=fonts, selfcheck_ok=ok, selfcheck_notes=notes)
