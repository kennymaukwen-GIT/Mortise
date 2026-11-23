"""兩欄版面不可被誤認成無框線表格；真正的無框線表格仍要認得出來。

背景：兩欄的履歷（左欄聯絡方式與技能、右欄經歷）被③當成跨欄的無框線表格，④照著做成表格，
輸出 Word、可編輯 PDF 時左欄的字不見、紅框蓋住整頁。原因與修正寫在 mortise/recognition/regions.py 的 _bl_column_flow
（③）與 mortise/stages/s4_structure.py 的 _sentences_cross_rows（④的保險）。

全部用虛構資料：
  - 前半：合成的文字框位置（不跑文字辨識，很快），涵蓋側欄＋主欄、兩欄同行距但被小標題錯開、價目表、課表、
    有分組列的表格、兩欄版面其中一欄裡的小表格。
  - 後半：tests/fixtures 的三份虛構 PDF（cv_two_column、two_column_notes、borderless_tables）畫成 150 dpi 截圖，
    走完整的影像產線，輸出版面固定的 Word 與可編輯 PDF，用⑦的逐行檢查確認每一行都在。
"""

from __future__ import annotations

import collections
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest

from mortise.artifacts import ElementList, Intent, OutputFormat, PageLayout
from mortise.recognition import regions as R
from mortise.tools import libreoffice

FIX = Path(__file__).resolve().parents[1] / "fixtures"
W, H = 1240, 1754  # A4 畫成 150 dpi 的大小（像素）
LINE_H = 18  # 一行文字框的高度（像素，約 9 pt 的字）


def box(x0: float, y0: float, w: float, h: float = LINE_H) -> np.ndarray:
    return np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]], float)


def find(quads, **params):
    """回傳（找到的表格 [(列, 欄)]、被當成兩欄版面而放棄的候選）。"""
    dbg: list = []
    P = R._merge({**params, "_debug": dbg})
    tables = R._borderless_tables(quads, [], P, H, W)
    rejected = [d for d in dbg if d.get("reason") == "two_column_flow"]
    return [(len(t["grid"].h_lines) - 1, len(t["grid"].v_lines) - 1) for t in tables], rejected, tables


OLD = {"bl_flow_pair_min": 0.0}  # 關掉這次加的檢查＝修正前的行為（用來確認情境真的會觸發原本的錯誤）


# ---------------------------------------------------------------- 合成的版面


def sidebar_cv() -> list[np.ndarray]:
    """左邊窄側欄（短項目，行距 30 px，每組之間多空一點）、右邊寬主欄（長行的段落與項目，行距 26 px）。
    兩欄各自排；最上面兩欄的小標題剛好同高（實際的履歷常常如此）。"""
    rng = np.random.default_rng(11)  # 寬度隨機產生（固定種子）
    q = []
    y = 300
    for i in range(19):  # 側欄：小標題（短）與項目
        q.append(box(70, y, 70 if i in (0, 6, 12) else float(rng.uniform(40, 260))))
        y += 30 + (22 if i in (5, 11) else 0)
    y = 300
    for i in range(25):  # 主欄：小標題、長行的段落（每段最後一行短）、項目
        if i in (0, 3, 16):
            w, x = 90.0, 414
        elif i % 4 == 0:
            w, x = float(rng.uniform(150, 520)), 421
        else:
            w, x = float(rng.uniform(720, 815)), 421
        q.append(box(x, y, w))
        y += 26 + (28 if i in (2, 15) else 0)
    return q


def same_pitch_notes() -> list[np.ndarray]:
    """兩欄同一個行距（27 px），寬欄是段落與項目、窄欄是項目與短段落（照 two_column_notes 的排法）：
    小標題前面多空 17 px、佔 33 px，段落或清單後面多空 10 px；兩欄的小標題出現在不同的地方，
    所以兩欄的行一陣子對齊、一陣子錯開（最容易被誤認成表格的情形）。"""
    q = []
    left = [("h", 160), ("p", [560, 575, 540, 120]), ("p", [585, 300]), ("h", 140), ("b", [560, 90, 470, 520]), ("h", 150),
            ("p", [590, 560, 575]), ("b", [330, 300, 380]), ("h", 110), ("p", [590, 560, 210])]
    right = [("h", 130), ("b", [300, 260, 370, 90]), ("h", 120), ("p", [380, 390]), ("b", [150, 210, 200]), ("h", 100),
             ("p", [370, 360, 150]), ("h", 140), ("p", [370, 330])]
    for x0, blocks in ((100, left), (750, right)):
        y = 258
        for kind, body in blocks:
            if kind == "h":
                y += 17
                q.append(box(x0, y, body))
                y += 33
                continue
            for w in body:
                q.append(box(x0 + (20 if kind == "b" else 0), y, w))
                y += 27
            y += 10
    return q


def price_list_and_timetable() -> list[np.ndarray]:
    """標題、橫跨整頁的說明、價目表（品項、份量、價格靠右；8 列 × 3 欄含表頭）、說明、課表（6 列 × 6 欄）、頁尾說明。"""
    q = [box(117, 120, 480, 40)]
    q += [box(117, 190, 1000), box(117, 220, 520)]
    y = 290
    items = [(30, 34, 50), (150, 50, 35), (115, 45, 35), (175, 55, 35), (100, 35, 35), (140, 45, 35), (170, 40, 35), (200, 50, 35)]
    for wi, ws, wp in items:
        q += [box(117, y, wi), box(480, y, ws), box(790 - wp, y, wp)]
        y += 40
    q += [box(117, y + 40, 1010), box(117, y + 70, 430)]
    y += 140
    words = [[45, 45, 45, 50, 45, 30], [55, 50, 75, 50, 55, 70], [55, 60, 70, 60, 70, 80], [55, 60, 60, 60, 60, 60],
             [55, 70, 75, 80, 75, 45], [55, 120, 50, 120, 50, 60]]
    for row in words:
        for k, w in enumerate(row):
            q.append(box(117 + 155 * k, y, w))
        y += 40
    q.append(box(117, y + 40, 780))
    return q


def sectioned_table() -> list[np.ndarray]:
    """有分組列的表格：表題一行、「分組名稱」單獨一列（只在第一欄），每組四列 × 3 欄。"""
    q = [box(117, 200, 260)]  # 表題，只在第一欄的範圍內
    y = 250
    for group in range(2):
        q.append(box(117, y, 90))  # 分組名稱
        y += 34
        for r in range(4):
            q += [box(117, y, 120 + 13 * r), box(420, y, 50), box(700, y, 45)]
            y += 34
    return q


def table_inside_right_column() -> list[np.ndarray]:
    """兩欄版面，右欄的段落之間夾著一張 4 列 × 3 欄的小表格（左欄是側欄，一路排下去）。"""
    q = [box(70, 300 + 30 * i, 120 + (37 * i) % 110) for i in range(30)]  # 左側欄
    y = 300
    for w in (780, 770, 790, 300):  # 右欄段落
        q.append(box(421, y, w))
        y += 26
    y += 20
    for r in range(4):
        q += [box(421, y, 150 + 10 * r), box(700, y, 60), box(950, y, 70)]
        y += 32
    y += 20
    for w in (760, 790, 420):
        q.append(box(421, y, w))
        y += 26
    return q


# ---------------------------------------------------------------- ③：合成版面


def test_sidebar_cv_is_not_a_table():
    old, _, _ = find(sidebar_cv(), **OLD)
    assert old, "這個情境在修正前應該會被誤認成表格（不然測不到東西）"
    tables, rejected, _ = find(sidebar_cv())
    assert tables == []
    assert rejected and all(r["pair_ratio"] < 0.8 and r["outside"] > 0 for r in rejected)


def test_same_pitch_columns_are_not_a_table():
    old, _, _ = find(same_pitch_notes(), **OLD)
    assert old, "這個情境在修正前應該會被誤認成表格（不然測不到東西）"
    tables, rejected, _ = find(same_pitch_notes())
    assert tables == [] and rejected


def test_price_list_and_timetable_are_still_tables():
    tables, rejected, found = find(price_list_and_timetable())
    assert sorted(tables) == [(6, 6), (8, 3)]
    assert rejected == []
    assert all(0.3 <= t["conf"] <= 0.7 for t in found)  # 無框表格信心值較低


def test_table_with_caption_and_group_rows_is_kept():
    """表題與分組名稱只在第一欄、別的欄沒有同高的行：它們是表格的一部分，不能把整張表當成兩欄版面。"""
    tables, rejected, _ = find(sectioned_table())
    assert tables == [(4, 3), (4, 3)], (tables, rejected)


def test_table_inside_one_column_of_a_two_column_page():
    """修正前：側欄的行和表格同一列，「表格」跨到側欄（列數、欄數都不對）。
    修正後：跨欄的候選放棄，在右欄裡面再找一次，找到真正的 4 列 × 3 欄。"""
    old, _, old_found = find(table_inside_right_column(), **OLD)
    assert old and all(t["box"][0] < 100 for t in old_found) and (4, 3) not in old
    tables, rejected, found = find(table_inside_right_column())
    assert tables == [(4, 3)] and rejected
    x0, _, x1, _ = found[0]["box"]
    assert x0 >= 400 and x1 <= 1030  # 只在右欄


def test_rows_inside_a_ruled_table_are_not_another_table():
    """有框表格（taken）裡的幾列文字也對得齊；以前只看交集 ÷ 聯集，碎片比大表小很多就漏掉，變成多出來的無框表格。"""
    q = sectioned_table()
    found, _, _ = find(q)
    assert found
    dbg: list = []
    big = (100, 150, 800, 1400)  # 比這兩組大很多的有框表格
    assert R._borderless_tables(q, [big], R._merge({"_debug": dbg}), H, W) == []


def test_column_flow_stops_at_line_spanning_the_columns():
    """表格上下有橫跨整個表格的說明行：欄的文字流到那裡就停，不會把上下的段落算進來。"""
    q = [box(117, 100 + 26 * i, 1000) for i in range(4)]
    y = 230
    for r in range(4):
        q += [box(117, y, 140), box(480, y, 60)]
        y += 34
    q += [box(117, y + 20 + 26 * i, 990) for i in range(3)]
    boxes = [(b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()) for b in q]
    flow = R._bl_column_flow(boxes, (117, 230, 540, 230 + 3 * 34 + LINE_H), [(257, 480)], R._merge(None), float(LINE_H))
    assert flow["outside"] == 0 and flow["pair_ratio"] == 1.0 and flow["lines"] == 8


def test_threshold_comes_from_params():
    """設定檔 [recognize] 的 region_bl_flow_pair_min 會蓋過預設值（去掉 region_ 前綴同名）：設成 0 就回到修正前。"""
    from mortise.config import Config

    c = Config.load().section("recognize")
    assert c.get("region_bl_flow_pair_min") == R.DEFAULT_PARAMS["bl_flow_pair_min"]
    tables, _, _ = find(sidebar_cv(), bl_flow_pair_min=0.0)
    assert tables


# ---------------------------------------------------------------- ④的保險：句子一列接一列排下去就不是表格


SIZE = 9.5 / 595.276  # 字級（相對單位：÷ 頁寬）
PITCH = 13.0 / 595.276  # 一般行距


def _line(i: int, text: str, x0: float, row: int, w: float, top: float = 0.2):
    from mortise.artifacts import Color, Point, Provenance, Quad, TextLine, TextRun, measured

    y = top + row * PITCH
    q = Quad.from_rect(x0, y, x0 + w, y + SIZE)
    m = lambda v: measured(v, 0.9, Provenance.MEASURED, "t")  # noqa: E731
    run = TextRun(text=text, quad=q, font_size=m(SIZE), bold=m(False), italic=m(False), color=m(Color(r=40, g=40, b=40)))
    return TextLine(id=f"l{i:02d}", quad=q, z=i, baseline=(Point(x=x0, y=y + 0.8 * SIZE), Point(x=x0 + w, y=y + 0.8 * SIZE)), runs=[run])


def _region(xs: list[float], n_rows: int, top: float = 0.2):
    from mortise.artifacts import Quad, TableRegion

    ys = [top - 0.2 * PITCH + r * PITCH for r in range(n_rows + 1)]
    return TableRegion(id="p001-tbl-test", quad=Quad.from_rect(xs[0], ys[0], xs[-1], ys[-1]), h_lines=ys, v_lines=xs, ruled=False, confidence=0.5)


def _s4_tables(region, lines):
    from types import SimpleNamespace

    from mortise.config import Config
    from mortise.stages import s4_structure as S4

    notes: list[str] = []
    out, _, used = S4._tables(SimpleNamespace(table_regions=[region]), lines, SimpleNamespace(page_index=0), Config.load().section("structure"), notes)
    return out, used, notes


def test_s4_dissolves_borderless_table_whose_columns_are_running_text():
    """兩欄同行距、剛好每一行都對齊（③分不出來）：左欄是一段換行的文字、右欄是會換行的項目。④看得到字：句子一列接一列，不做成表格。"""
    left = ["Cold frames go in during the first dry week of March. Seedlings", "that start under glass are ready for the open beds about three",
            "weeks sooner, and the soil has time to warm before the first", "planting day.",
            "Each plot holder looks after one frame. The frames are shared, so", "please label every tray with your plot letter."]
    right = ["• Sow broad beans and early peas.", "• Lift the last leeks.", "• Mend the north fence before the windy", "weeks.",
             "• Return forks clean.", "• Hang the hoses on the hooks."]
    lw = [0.465, 0.455, 0.46, 0.11, 0.47, 0.40]
    rw = [0.27, 0.17, 0.30, 0.07, 0.17, 0.25]
    lines = [_line(i, t, 0.08, i, w) for i, (t, w) in enumerate(zip(left, lw))]
    lines += [_line(10 + i, t, 0.605 + (0.017 if t == "weeks." else 0.0), i, w) for i, (t, w) in enumerate(zip(right, rw))]
    out, used, notes = _s4_tables(_region([0.08, 0.58, 0.92], 6), lines)
    assert out == [] and not used  # 那些行留給一般的文字流
    assert notes and "不做成表格" in notes[0]


def test_s4_keeps_real_borderless_table_even_with_lowercase_cells():
    """真正的價目表：品項大寫開頭、有一欄是小寫開頭的備註（最長的那格剛好排滿）。只有一處像接續，不到兩處：仍是表格。"""
    rows = [("Filter coffee", "mug", "made with oat milk on request"), ("Flat white", "small", "served in a pot"),
            ("Hot chocolate", "large", "baked each morning"), ("Mint tea", "pot", "ask at the counter")]
    widths = [(0.13, 0.04, 0.26), (0.10, 0.05, 0.14), (0.14, 0.05, 0.17), (0.08, 0.03, 0.16)]
    lines = []
    for r, (cells, ws) in enumerate(zip(rows, widths)):
        for k, (t, w) in enumerate(zip(cells, ws)):
            lines.append(_line(10 * r + k, t, [0.1, 0.35, 0.55][k], r, w))
    out, used, notes = _s4_tables(_region([0.1, 0.3, 0.5, 0.85], 4), lines)
    assert len(out) == 1 and len(used) == 12 and notes == []


# ---------------------------------------------------------------- 完整的影像產線：虛構 fixture 的截圖


_RUNS: dict = {}


def _run(name: str, tmp_path_factory):
    """PDF 畫成 150 dpi 的 PNG，走完整流水線（輸出版面固定的 Word 與可編輯 PDF），同一份只跑一次。"""
    if name not in _RUNS:
        from mortise.pipeline import Pipeline

        tmp = tmp_path_factory.mktemp(name)
        png = tmp / f"{name}.png"
        pdfium.PdfDocument(str(FIX / f"{name}.pdf"))[0].render(scale=150 / 72).to_pil().convert("RGB").save(png)
        pipe = Pipeline(tmp / "job")
        pipe.start(png, png.name, Intent(outputs=[OutputFormat.DOCX_FIXED, OutputFormat.PDF]))
        report = pipe.run()
        el = pipe.store.load("s3/page-001", ElementList)
        layout = pipe.store.load("s4/page-001", PageLayout)
        _RUNS[name] = (pipe, report, el, layout)
    return _RUNS[name]


# 兩份兩欄文件：欄間空白的位置（頁寬比例）與兩欄區的上下範圍（相對單位：y ÷ 頁寬）
TWO_COLUMN = {
    "cv_two_column": (0.32, 125 / 595.276, 842 / 595.276),
    "two_column_notes": (0.58, 110 / 595.276, 760 / 595.276),
}


@pytest.mark.parametrize("name", list(TWO_COLUMN))
def test_two_column_fixture_has_no_table_and_columns_stay_apart(name, tmp_path_factory):
    _, _, el, layout = _run(name, tmp_path_factory)
    assert el.table_regions == [] and layout.tables == []
    gutter, top, bottom = TWO_COLUMN[name]
    by_id = {t.id: t for t in el.texts}
    sides = collections.Counter()
    for p in layout.paragraphs:
        lines = [by_id[i] for i in p.line_ids]
        if not all(top <= ln.quad.center.y <= bottom for ln in lines):
            continue  # 頁首、頁尾橫跨兩欄
        left = [ln.quad.bbox[2] <= gutter for ln in lines]
        right = [ln.quad.bbox[0] >= gutter for ln in lines]
        assert all(left) or all(right), f"段落跨了兩欄：{[ln.text for ln in lines]}"
        sides["left" if all(left) else "right"] += 1
    assert sides["left"] >= 5 and sides["right"] >= 5, sides  # 兩欄各自成段落（小標題、段落、項目各自一段）


@pytest.mark.skipif(not libreoffice.available(), reason="Word 要用 LibreOffice 轉成 PDF 才驗證得了")
@pytest.mark.parametrize("name", list(TWO_COLUMN))
@pytest.mark.parametrize("fmt", [OutputFormat.DOCX_FIXED, OutputFormat.PDF])
def test_two_column_fixture_every_line_is_in_the_output(name, fmt, tmp_path_factory):
    """⑦逐行檢查：兩欄文件的每一行都要在成品裡（修正前，被誤認成表格的那幾段在 Word 與 PDF 各有 7 到 8 行找不到）。"""
    _, report, _, _ = _run(name, tmp_path_factory)
    fr = next(f for f in report.format_reports if f.format == fmt)
    missing = [i.message for p in fr.pages for i in p.issues if i.code == "line_missing"]
    if name == "cv_two_column" and fmt == OutputFormat.DOCX_FIXED and len(missing) == 1 and missing[0].endswith("「shops.」。"):
        pytest.xfail("已知問題（與表格無關，留給⑥⑦）：段落最後一行只剩一個詞「shops.」；Word 換了字寬不同的字型後，"
                     "上一行的「small」被擠到下一行，⑦的短行規則把「shops.」判成找不到（其實字還在，是換行不同）。")
    assert missing == [], missing


def test_borderless_tables_fixture_is_still_recognized(tmp_path_factory):
    _, report, el, layout = _run("borderless_tables", tmp_path_factory)
    shapes = sorted((len(t.h_lines) - 1, len(t.v_lines) - 1) for t in el.table_regions)
    assert shapes == [(6, 6), (8, 3)] and not any(t.ruled for t in el.table_regions)
    assert sorted((len(t.row_lines) - 1, len(t.col_lines) - 1) for t in layout.tables) == [(6, 6), (8, 3)]
    assert not [n for n in layout.notes if "無框線表格" in n]  # ④的保險沒有把真正的表格拆掉
    # 每一格都分到字（價目表 24 格、課表 36 格），而且分對格子
    by_par = {p.id: p for p in layout.paragraphs}
    by_line = {t.id: t for t in el.texts}

    def cell_text(t, r, k):
        cell = next(c for c in t.cells if (c.row, c.col) == (r, k))
        return " ".join(by_line[i].text.strip() for pid in cell.paragraph_ids for i in by_par[pid].line_ids)

    price, week = sorted(layout.tables, key=lambda t: len(t.col_lines))
    for t in (price, week):
        assert all(c.paragraph_ids for c in t.cells), t.id
    assert (cell_text(price, 0, 0), cell_text(price, 1, 0), cell_text(price, 7, 2)) == ("Item", "Filter coffee", "4.50")
    assert (cell_text(week, 0, 1), cell_text(week, 5, 1), cell_text(week, 3, 5)) == ("Mon", "Homework", "Lunch")
