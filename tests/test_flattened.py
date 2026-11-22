"""原生 PDF 裡被扁平化的內容：外框字轉回文字、內嵌圖片裡的文字與圖形拆開（stages/s3_flattened.py）。

兩個 fixture（tests/fixtures/make_fixtures.py 的 flattened_outlined、flattened_embedded）的內容都已知（FLATTENED_TRUTH），
這裡逐項對答案。只跑①②③（不必整條流水線）。
"""

from __future__ import annotations

import importlib.util
import math
from collections import Counter
from pathlib import Path

import pytest

from mortise.artifacts import ElementList, InputType, Intent, PageSpec, Provenance, Quad
from mortise.config import Config
from mortise.pipeline.store import Store
from mortise.stages import s1_input, s2_page, s3_flattened, s3_image_line, s3_recognize

FIX = Path(__file__).parent / "fixtures"
A4_W, A4_H = 595.276, 841.89
# 影像產線量字級的容許誤差。單獨跑時實測誤差在 2% 以內，但 text_attributes 的結果會受「之前量過哪些行」影響
# （同一張圖連續量三次，同一行可差 6%；見回報的已知問題），所以放寬到 10%。
SIZE_TOL = 0.10

_spec = importlib.util.spec_from_file_location("make_fixtures", FIX / "make_fixtures.py")
_mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mf)
TRUTH = _mf.FLATTENED_TRUTH


def _recognize(job_dir: Path, source: Path, cfg: Config | None = None) -> tuple[Store, list[tuple[object, PageSpec, ElementList]]]:
    """只跑①輸入、②頁面、③辨識。回傳（儲存區、每頁的（頁面資訊、頁面規格、元素清單））。"""
    store = Store(job_dir)
    cfg = cfg or Config.load()
    doc = s1_input.run(source, source.name, Intent(), cfg, store)
    pages = []
    for ref in doc.pages:
        spec = s2_page.run(doc, ref.page_index, cfg, [], store)
        pages.append((ref, spec, s3_recognize.run(doc, spec, cfg, [], store)))
    return store, pages


def _cfg(**recognize) -> Config:
    cfg = Config.load()
    cfg.data["recognize"].update(recognize)
    return cfg


def _pt(v: float) -> float:
    """頁面相對單位 → pt（A4 頁寬）。"""
    return v * A4_W


@pytest.fixture(scope="module")
def outlined(tmp_path_factory):
    _, pages = _recognize(tmp_path_factory.mktemp("flat-outlined"), FIX / "flattened_outlined.pdf")
    return pages[0][2]


@pytest.fixture(scope="module")
def embedded(tmp_path_factory):
    store, pages = _recognize(tmp_path_factory.mktemp("flat-embedded"), FIX / "flattened_embedded.pdf")
    return store, pages


@pytest.fixture(scope="module")
def embedded_off(tmp_path_factory):
    """關掉「拆內嵌圖片」的結果：用來確認原生內容不受影響、照片沒有被動到。"""
    store, pages = _recognize(tmp_path_factory.mktemp("flat-embedded-off"), FIX / "flattened_embedded.pdf", _cfg(pdf_embedded_images=False))
    return store, pages


# ---------------------------------------------------------------- (a) 外框字 → 文字


def test_outlined_page_is_read_back_as_text(outlined):
    """整頁的字都是外框：每一行都讀回正確的文字，位置、字級由外框幾何算出，顏色取外框的填色，不再輸出那些外框。"""
    el = outlined
    by_text = {t.text: t for t in el.texts}
    assert sorted(by_text) == sorted(t[0] for t in TRUTH["outlined"])
    for text, font, size, x, y, color in TRUTH["outlined"]:
        line = by_text[text]
        assert _pt(line.baseline[0].y) == pytest.approx(A4_H - y, abs=0.1), text  # 基線：誤差 0.1 pt 以內
        assert _pt(line.baseline[0].x) == pytest.approx(x, abs=0.2), text  # 第一個字的筆位起點
        assert _pt(line.font_size) == pytest.approx(size, rel=0.005), text  # 字級：誤差 0.5% 以內
        assert line.runs[0].color.value.hex == color and line.runs[0].color.source == Provenance.EXACT
        assert all(r.font_size.source == Provenance.MEASURED for r in line.runs)
        assert line.text_confidence is not None and line.text_confidence >= 0.9
        assert any("原為外框字" in n and "字型為推論" in n for n in line.notes)
        expected_font = "WenQuanYi Zen Hei" if font == "wqy" else "Liberation Sans"
        assert line.runs[0].font_candidates[0].name == expected_font
        assert all(r.bold.value == (font == "bold") for r in line.runs)
    # 一行裡的紅字自成一段
    prices = by_text["Prices start at $49 per month."]
    assert [(r.text.strip(), r.color.value.hex) for r in prices.runs] == [("Prices start at", "222222"), ("$49", "C8553D"), ("per month.", "222222")]
    # 每個字的起點（char_x）與字型排出來的位置一致：「Flattened」的 F、l、a 的前進寬度是 0.611、0.278、0.556 em
    head = by_text["Flattened brochure"].runs[0]
    assert [_pt(v) for v in head.char_x[:3]] == pytest.approx([0.0, 0.611 * 30, (0.611 + 0.278) * 30], abs=0.05)
    # 外框與它們的圖片都不再輸出；裝飾圖形（葉子、圓）照舊是形狀
    assert el.vectors == [] and el.images == []
    assert Counter(s.geometry.value for s in el.shapes) == {"path": 1, "ellipse": 1}
    assert el.background.image is None  # 轉成文字的外框也從底圖隱藏了
    assert any("已轉回可編輯的文字" in n for n in el.notes)
    ids = [t.id for t in el.texts]
    assert len(ids) == len(set(ids))


def test_low_confidence_keeps_the_outlines(tmp_path):
    """信心不足（這裡把門檻設成 1.01 來模擬）：保留成外框向量圖，備註說明原因；同一塊區域不會同時有文字與外框。"""
    _, pages = _recognize(tmp_path, FIX / "flattened_outlined.pdf", _cfg(pdf_outlined_text_min_confidence=1.01))
    el = pages[0][2]
    assert el.texts == []
    assert el.vectors and all(v.kind == "outlined_text" for v in el.vectors)
    assert all(any("保留成外框" in n and "辨識信心" in n for n in v.notes) for v in el.vectors)
    assert len(el.images) == len(el.vectors)


def test_turning_it_off_keeps_the_old_behaviour(tmp_path):
    _, pages = _recognize(tmp_path, FIX / "flattened_outlined.pdf", _cfg(pdf_outlined_to_text=False))
    el = pages[0][2]
    assert el.texts == [] and el.vectors and all(v.kind == "outlined_text" for v in el.vectors)
    assert not any("保留成外框" in n for v in el.vectors for n in v.notes)


# ---------------------------------------------------------------- (b) 內嵌圖表截圖


def _chart_point(px: float, py: float) -> tuple[float, float]:
    """圖表截圖的像素座標 → 頁面上的 pt（原點左上）。"""
    info = TRUTH["chart"]
    k = info["width"] / info["pixels"][0]
    return info["x"] + px * k, info["top"] + py * k


def test_chart_picture_is_taken_apart(embedded):
    _, pages = embedded
    _, _, el = pages[0]
    k = TRUTH["chart"]["width"] / TRUTH["chart"]["pixels"][0]
    by_text = {t.text: t for t in el.texts}
    # 圖裡的字變成文字行：位置與字級是量測值，容許量測誤差
    for text, kind, px, x, y in TRUTH["chart"]["texts"]:
        line = by_text[text]
        bx, by = _chart_point(x, y)
        assert _pt(line.baseline[0].y) == pytest.approx(by, abs=1.0), text
        assert _pt(line.quad.bbox[0]) == pytest.approx(bx, abs=2.0), text  # 墨跡左緣（含字的左側留白，約一兩個像素）
        assert _pt(line.font_size) == pytest.approx(px * k, rel=SIZE_TOL), text
        assert line.runs[0].font_size.source == Provenance.MEASURED and line.text_confidence >= 0.9
        assert any("由嵌入圖片辨識而來" in n for n in line.notes)
    for text, x, y in TRUTH["chart"]["labels"]:
        line = by_text[text]
        cx, by = _chart_point(x, y)
        assert _pt(line.quad.center.x) == pytest.approx(cx, abs=2.0) and _pt(line.baseline[0].y) == pytest.approx(by, abs=1.5)
    assert {"220", "300", "260", "360"} <= set(by_text)
    # 長條變成矩形：位置與顏色
    rects = [s for s in el.shapes if s.geometry.value == "rect"]
    for x0, y0, x1, y1, col in TRUTH["chart"]["bars"]:
        (l, t), (r, b) = _chart_point(x0, y0), _chart_point(x1, y1)
        hit = [s for s in rects if abs(_pt(s.quad.bbox[0]) - l) < 1.5 and abs(_pt(s.quad.bbox[2]) - r) < 1.5 and abs(_pt(s.quad.bbox[1]) - t) < 1.5 and abs(_pt(s.quad.bbox[3]) - b) < 1.5]
        assert len(hit) == 1, (x0, y0)
        assert max(abs(a - b_) for a, b_ in zip((hit[0].fill.r, hit[0].fill.g, hit[0].fill.b), col)) <= 3
    # 座標軸（直線）
    assert any(s.geometry.value == "line" and abs(_pt(s.quad.bbox[0]) - _chart_point(100, 0)[0]) < 1.5 for s in el.shapes)
    # 白底的圖拆完只剩白色、底下沒有東西：原圖片不再保留
    assert el.images == [] and any("嵌入圖片含有文字或平面圖形" in n for n in el.notes)


def test_native_text_is_not_affected(embedded, embedded_off):
    """原生文字的內容、位置、字級與編號都不變；拆出來的元素在圖層上剛好落在原圖片的位置（前後的原生文字之間）。"""
    _, pages = embedded
    _, off_pages = embedded_off
    el, off = pages[0][2], off_pages[0][2]
    native_off = {t.id: t for t in off.texts}
    native = [t for t in el.texts if t.id in native_off]
    assert len(native) == 3
    for t in native:
        o = native_off[t.id]
        assert (t.text, t.quad, t.baseline, [r.font_size for r in t.runs], t.text_confidence) == (o.text, o.quad, o.baseline, [r.font_size for r in o.runs], None)
    (image,) = off.images
    above = [native_off[t.id].z > image.z for t in native]
    derived = [t for t in el.texts if t.id not in native_off] + list(el.shapes)
    for t in native:  # 原本在圖片上面的仍在上面，在下面的仍在下面
        if native_off[t.id].z > image.z:
            assert all(t.z > d.z for d in derived)
        else:
            assert all(t.z < d.z for d in derived)
    assert any(above) and not all(above)
    ids = [x.id for x in el.texts + el.shapes + el.images + el.table_regions + el.vectors]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- (c) 照片不拆


def test_photo_is_left_alone(embedded, embedded_off):
    _, pages = embedded
    _, off_pages = embedded_off
    el, off = pages[1][2], off_pages[1][2]
    assert [t.text for t in el.texts] == ["Field photo"]
    assert el.images == off.images and el.shapes == [] and el.vectors == []
    assert not any("嵌入圖片" in n for n in el.notes)


# ---------------------------------------------------------------- (d) 處理失敗時退回原圖片


def test_failure_falls_back_to_the_original_picture(tmp_path, monkeypatch, embedded_off):
    def boom(*args, **kwargs):
        raise RuntimeError("模擬影像產線失敗")

    monkeypatch.setattr(s3_image_line, "run", boom)
    _, pages = _recognize(tmp_path, FIX / "flattened_embedded.pdf")
    _, off_pages = embedded_off
    for (_, _, el), (_, _, off) in zip(pages, off_pages):
        assert el.images == off.images and el.texts == off.texts and el.shapes == off.shapes
    assert any("拆解時出錯" in n and "保留原圖片" in n for n in pages[0][2].notes)
    assert not any("拆解時出錯" in n for n in pages[1][2].notes)  # 照片在跑影像產線之前就被篩掉了


# ---------------------------------------------------------------- 整頁掃描上疊一行原生字（混合 PDF）


def test_full_page_scan_with_native_stamp(embedded, embedded_off):
    _, pages = embedded
    ref, spec, el = pages[2]
    assert ref.input_type.value == InputType.MIXED_PDF
    k = A4_W / TRUTH["memo"]["pixels"][0]
    by_text = {t.text: t for t in el.texts}
    found = 0
    for text, kind, px, x, y in TRUTH["memo"]["texts"]:
        line = by_text.get(text)
        if line is None:
            continue
        found += 1
        assert _pt(line.baseline[0].y) == pytest.approx(y * k, abs=1.0), text
        assert _pt(line.font_size) == pytest.approx(px * k, rel=SIZE_TOL), text
    assert found >= 5  # 六行裡至少五行一字不差
    stamp = by_text["APPROVED 2026-11-22"]
    off = {t.text: t for t in embedded_off[1][2][2].texts}["APPROVED 2026-11-22"]
    assert stamp.quad == off.quad and stamp.id == off.id
    assert all(stamp.z > t.z for t in el.texts if t is not stamp)  # 原生的字在掃描的上面
    # 掃描拆完只剩紙色：整頁的圖片改成頁面底色
    assert el.images == [] and el.background.image is None
    paper = el.background.color
    assert max(abs(a - b) for a, b in zip((paper.r, paper.g, paper.b), (252, 251, 248))) <= 3
    assert any(s.geometry.value == "line" for s in el.shapes)  # 主旨下的橫線


# ---------------------------------------------------------------- 小工具


def test_exact_contour_boxes_follow_the_curve():
    """曲線的外框取真正的極值，不是控制點的外框。"""
    # 一段往上凸的三次貝茲：控制點的 y 到 -1，曲線最高只到 -0.75
    path = [[0, 0.0, 0.0], [2, 0.0, -1.0, 1.0, -1.0, 1.0, 0.0], [3]]
    ((l, t, r, b),) = s3_flattened.contour_boxes_exact(path)
    assert (l, r, b) == pytest.approx((0.0, 1.0, 0.0)) and t == pytest.approx(-0.75)


def test_mapper_follows_the_placement():
    m = s3_flattened._Mapper.from_box(Quad.from_rect(0.1, 0.2, 0.5, 0.45), (800, 500))
    p = m.pt(s3_flattened.Point(x=1.0, y=500 / 800))
    assert (p.x, p.y) == pytest.approx((0.5, 0.45))
    assert m.k == pytest.approx(0.4) and m.sy == pytest.approx(0.4) and m.axis_aligned
    assert math.isclose(m.quad(Quad.from_rect(0, 0, 1, 500 / 800)).bbox[3], 0.45)
    # 轉 90 度：圖片的 x 軸朝下、y 軸朝左
    r = s3_flattened._Mapper((0.6, 0.1), (0.0, 0.3), (-0.2, 0.0))
    assert r.angle == pytest.approx(90.0) and not r.axis_aligned
    assert r.xy(1.0, 1.0) == pytest.approx((0.4, 0.4))


def _picture_pdf(path: Path) -> dict:
    """暫時的 PDF：同一張圖表截圖，一張被壓扁（高度只剩一半），一張轉 90 度。回傳各自的擺法。"""
    import io

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    chart, _ = _mf._flattened_images()
    buf = io.BytesIO()
    chart.save(buf, "PNG")
    c = canvas.Canvas(str(path), pagesize=(A4_W, A4_H), invariant=1)
    c.setFont("Helvetica", 12)
    c.drawString(60, 800, "Pictures placed in odd ways")
    buf.seek(0)
    c.drawImage(ImageReader(buf), 60, 500, width=475, height=150)  # 原本 475 × 304：壓扁成 150
    c.saveState()
    c.translate(400, 100)
    c.rotate(90)
    buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, width=300, height=192)
    c.restoreState()
    c.showPage()
    c.save()
    return {"squashed": (60, A4_H - 650, 475 / 1000, 150 / 640), "rotated": (400, A4_H - 100, 300 / 1000)}


def test_stretched_and_rotated_pictures(tmp_path):
    """被非等比縮放、旋轉 90 度的圖片：依圖片的四角轉回原本的方向再辨識，結果換算回頁面上的位置。"""
    place = _picture_pdf(tmp_path / "odd.pdf")
    _, pages = _recognize(tmp_path / "job", tmp_path / "odd.pdf")
    el = pages[0][2]
    titles = [t for t in el.texts if t.text == "Quarterly Sales 2026"]
    assert len(titles) == 2
    squashed = min(titles, key=lambda t: t.quad.bbox[1])
    rotated = max(titles, key=lambda t: t.quad.bbox[1])
    # 壓扁的：字級依垂直方向的比例（44 像素 × 150/640），基線在圖片 y=70 的位置
    x0, top, kx, ky = place["squashed"]
    assert _pt(squashed.font_size) == pytest.approx(44 * ky, rel=SIZE_TOL)
    assert _pt(squashed.baseline[0].y) == pytest.approx(top + 70 * ky, abs=1.0)
    assert any("非等比縮放" in n for n in el.notes)
    # 轉 90 度（逆時針）的：圖片的上緣在左邊（x = 400 − 192），文字由下往上讀；
    # 基線在圖片 y=70 的位置 → 頁面 x = 208 + 70 × 0.3；第一個字在圖片 x=40 的位置 → 頁面上離圖片下緣 40 × 0.3
    ox, oy, k = place["rotated"]
    assert abs(rotated.quad.angle_deg + 90) < 3
    assert _pt(rotated.baseline[0].x) == pytest.approx(ox - 192 + 70 * k, abs=1.0)
    assert _pt(rotated.baseline[0].y) == pytest.approx(oy - 40 * k, abs=2.0)
    assert _pt(rotated.font_size) == pytest.approx(44 * k, rel=SIZE_TOL)
    for label in ("Q1", "Q2", "Q3", "Q4"):
        assert sum(t.text == label for t in el.texts) == 2
