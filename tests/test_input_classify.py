"""影像輸入是截圖還是照片（①s1_input.classify_image），以及②在照片模式下的增強（s2_page）。

要防的問題：沒有 EXIF 的 JPEG 截圖（平塗色塊、清楚的文字）以前會被當成照片，於是②做了白平衡與去光照，
顏色被改（白底變成 245 灰），⑦又拿增強後的影像比，分數仍然很高。現在依影像內容判斷，不看檔案格式。

樣本：
  - 截圖：tests/fixtures 的 PDF 以 150 dpi 畫成 JPEG（品質 85、沒有 EXIF），也存成 PNG；判斷都要是截圖。
  - 照片：mortise/eval/synth.py 合成的手機翻拍（透視、桌面背景、光照漸層、偏色、雜訊、JPEG），強度 0.5 到 1.6；判斷都要是照片。
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mortise.artifacts import InputType, Intent
from mortise.config import Config
from mortise.eval import synth
from mortise.pipeline.store import Store
from mortise.recognition import page_geometry as pg
from mortise.stages import s1_input, s2_page
from mortise.stages.s1_input import ImageFeatures, classify_image
from mortise.tools import pdfium as mp

FIX = Path(__file__).parent / "fixtures"
SHOTS = ["slide_basic", "slide_dark", "doc_a4", "poster", "deck", "cv_two_column", "flattened_outlined", "flattened_embedded",
         "vector_logo", "vector_outlined", "vector_paint"]
# （fixture、亂數種子、強度）：強度 0.5 的種子 2 雜訊最少、又被 JPEG 壓得最平，最像截圖
PHOTOS = [("doc_a4", 1, 1.0), ("slide_dark", 2, 0.5), ("poster", 3, 1.6), ("cv_two_column", 2, 0.5), ("deck", 1, 1.0), ("vector_paint", 3, 0.5),
          ("flattened_outlined", 2, 0.5)]


def render(name: str, dpi: float = 150.0) -> Image.Image:
    d = mp.open_document(str(FIX / f"{name}.pdf"))
    try:
        r = mp.PageReader(d, 0)
        try:
            return r.render(dpi).convert("RGB")
        finally:
            r.close()
    finally:
        d.close()


def save_jpeg(im: Image.Image, path: Path, quality: int = 85, exif: bytes | None = None) -> Path:
    kw = {"exif": exif} if exif else {}
    im.save(path, "JPEG", quality=quality, **kw)
    return path


def camera_exif() -> bytes:
    ex = Image.Exif()
    ex[271] = "Apple"
    ex[272] = "iPhone 15"
    return ex.tobytes()


def run_s1(path: Path, store: Store):
    return s1_input.run(path, path.name, Intent(), Config.load(), store)


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Store:
    return Store(tmp_path_factory.mktemp("classify-store"))


@pytest.fixture(scope="module")
def shot_jpeg(tmp_path_factory) -> Path:
    return save_jpeg(render("doc_a4"), tmp_path_factory.mktemp("shot") / "doc_a4.jpg")


@pytest.fixture(scope="module")
def photo_jpeg(tmp_path_factory) -> Path:
    deg, _, _ = synth.degrade(np.asarray(render("doc_a4")), "photo", seed=1, strength=1.0)
    return save_jpeg(Image.fromarray(deg), tmp_path_factory.mktemp("photo") / "doc_a4_photo.jpg")


# ---------------------------------------------------------------- ①：截圖


@pytest.mark.parametrize("name", SHOTS)
def test_jpeg_screenshot_of_fixture_is_screenshot(name, tmp_path, store):
    if not (FIX / f"{name}.pdf").exists():
        pytest.skip(f"沒有 {name}.pdf")
    im = render(name)
    jpg = save_jpeg(im, tmp_path / f"{name}.jpg")
    assert not Image.open(jpg).getexif()  # 沒有 EXIF
    doc = run_s1(jpg, store)
    ref = doc.pages[0]
    assert ref.input_type.value == InputType.SCREENSHOT, doc.metadata
    assert ref.input_type.confidence >= 0.75
    assert "截圖" in doc.metadata["classify"] and "平塗" in doc.metadata["classify_features"]
    assert "camera" not in doc.metadata and not doc.warnings
    # 同一張畫面存成 PNG：判斷相同（不看檔案格式）
    png = tmp_path / f"{name}.png"
    im.save(png)
    assert run_s1(png, store).pages[0].input_type.value == InputType.SCREENSHOT


def test_screenshot_with_a_big_photo_inside_is_screenshot(tmp_path, store):
    """投影片裡放了一大張照片（有雜訊、四邊與畫面平行）：照片的四邊不是拍到的紙張，四邊以外是平塗的白底。"""
    rng = np.random.default_rng(3)
    canvas = np.full((620, 900, 3), 255, np.uint8)
    yy, xx = np.mgrid[0:500, 0:780]
    scene = np.stack([90 + 60 * np.sin(xx / 70.0), 120 + 40 * np.cos(yy / 50.0), 150 + 30 * np.sin((xx + yy) / 90.0)], axis=2)
    canvas[60:560, 60:840] = np.clip(scene + rng.normal(0, 4, scene.shape), 0, 255).astype(np.uint8)
    path = tmp_path / "slide_with_photo.png"
    Image.fromarray(canvas).save(path)
    ref = run_s1(path, store).pages[0]
    assert ref.input_type.value == InputType.SCREENSHOT


# ---------------------------------------------------------------- ①：照片


@pytest.mark.parametrize("name,seed,strength", PHOTOS)
def test_synthetic_phone_photo_is_photo(name, seed, strength, tmp_path, store):
    if not (FIX / f"{name}.pdf").exists():
        pytest.skip(f"沒有 {name}.pdf")
    deg, _, info = synth.degrade(np.asarray(render(name)), "photo", seed=seed, strength=strength)
    jpg = save_jpeg(Image.fromarray(deg), tmp_path / f"{name}-{seed}.jpg")
    doc = run_s1(jpg, store)
    ref = doc.pages[0]
    assert ref.input_type.value == InputType.PHOTO, (info, doc.metadata)
    assert ref.input_type.confidence >= 0.7 and "照片" in doc.metadata["classify"]
    # 存成 PNG 也一樣是照片（不看檔案格式）
    png = tmp_path / f"{name}-{seed}.png"
    Image.fromarray(deg).save(png)
    assert run_s1(png, store).pages[0].input_type.value == InputType.PHOTO


def test_camera_exif_does_not_override_content(tmp_path, store):
    """相機資訊只是旁證：帶相機資訊的截圖仍判為截圖（信心值降低、提醒使用者）；帶相機資訊的照片信心值更高。"""
    jpg = save_jpeg(render("slide_basic"), tmp_path / "shot_exif.jpg", exif=camera_exif())
    doc = run_s1(jpg, store)
    assert doc.metadata["camera"] == "Apple iPhone 15"
    assert doc.pages[0].input_type.value == InputType.SCREENSHOT and doc.pages[0].input_type.confidence == pytest.approx(0.6)
    assert doc.warnings and "相機資訊" in doc.warnings[0]
    deg, _, _ = synth.degrade(np.asarray(render("slide_basic")), "photo", seed=1, strength=1.0)
    p = run_s1(save_jpeg(Image.fromarray(deg), tmp_path / "photo_exif.jpg", exif=camera_exif()), store)
    assert p.pages[0].input_type.value == InputType.PHOTO and p.pages[0].input_type.confidence == pytest.approx(0.95)


def test_classify_rules():
    c = Config.load().section("input")

    def f(flat=0.0, big=0.0, noise=3.0, method="full_image", conf=0.9, area=1.0, outside=None):
        return ImageFeatures(flat, big, noise, method, conf, area, outside)

    assert classify_image(f(0.9, 0.85, 0.0), False, c)[0] == InputType.SCREENSHOT
    assert classify_image(f(0.9, 0.85, 0.0), False, c)[1] == pytest.approx(0.9)
    assert classify_image(f(0.6, 0.1, 0.0), False, c)[0] == InputType.SCREENSHOT  # 漸層多：沒有大片平塗，但沒有雜訊、平塗像素多
    assert classify_image(f(0.4, 0.1, 0.2), False, c)[0] == InputType.PHOTO  # 介於中間：先當照片
    assert classify_image(f(0.0, 0.0, 4.0), False, c)[:2] == (InputType.PHOTO, 0.7)
    assert classify_image(f(0.0, 0.0, 4.0), True, c)[:2] == (InputType.PHOTO, 0.8)
    # 有透視變形的紙張四邊：照片；四邊以外是平塗的畫面、四邊形又只佔一部分：截圖裡的色塊
    assert classify_image(f(0.3, 0.2, 0.0, "edge_fit", 0.95, 0.5, 0.0), False, c)[0] == InputType.PHOTO
    assert classify_image(f(0.3, 0.3, 1.4, "edge_fit", 0.95, 0.7, 0.95), False, c)[0] == InputType.SCREENSHOT
    assert classify_image(f(0.8, 0.7, 0.0, "edge_fit", 0.95, 0.95, 0.6), False, c)[0] == InputType.PHOTO  # 佔滿畫面：掃到的紙張
    assert classify_image(f(0.0, 0.0, 4.0, "edge_fit", 0.3, 0.5, 0.0), False, c)[0] == InputType.PHOTO  # 四邊信心值低：不算四邊，看雜訊


# ---------------------------------------------------------------- ②：截圖不增強，照片的原圖另外保留


def test_screenshot_keeps_original_colors(shot_jpeg, store):
    cfg = Config.load()
    doc = run_s1(shot_jpeg, store)
    spec = s2_page.run(doc, 0, cfg, [], store)
    assert spec.quad_method == "full_image" and not spec.enhanced and spec.color_image is None
    work = np.asarray(Image.open(store.blob_path(spec.working_image)).convert("RGB"))
    orig = np.asarray(Image.open(shot_jpeg).convert("RGB"))
    assert work.shape == orig.shape and np.array_equal(work, orig)  # 工作影像就是上傳的影像，一個色階都沒改
    assert np.median(work.reshape(-1, 3), axis=0).tolist() == [255, 255, 255]  # 白底仍是白底（以前會變成 245）
    assert not any("白平衡" in n and "已去陰影" in n for n in spec.notes)


def test_photo_mode_on_digital_content_does_not_enhance(shot_jpeg, store):
    """②的第二道防線：①判成照片（例如使用者指定、或檔案帶相機資訊而內容分不出來）但畫面其實是平塗的數位內容時，不做增強。"""
    cfg = Config.load()
    doc = run_s1(shot_jpeg, store)
    ref = doc.pages[0].model_copy(update={"input_type": doc.pages[0].input_type.model_copy(update={"value": InputType.PHOTO})})
    forced = doc.model_copy(update={"pages": [ref]})
    spec = s2_page.run(forced, 0, cfg, [], store)
    assert not spec.enhanced and spec.color_image is None
    assert any("平塗的數位畫面" in n for n in spec.notes)
    work = np.asarray(Image.open(store.blob_path(spec.working_image)).convert("RGB"))
    assert np.array_equal(work, np.asarray(Image.open(shot_jpeg).convert("RGB")))


def test_photo_is_rectified_and_enhanced_but_keeps_the_original(photo_jpeg, store):
    cfg = Config.load()
    doc = run_s1(photo_jpeg, store)
    assert doc.pages[0].input_type.value == InputType.PHOTO
    spec = s2_page.run(doc, 0, cfg, [], store)
    assert spec.quad_method == "edge_fit" and spec.enhanced and spec.color_image is not None
    work = np.asarray(Image.open(store.blob_path(spec.working_image)).convert("RGB"))
    color = np.asarray(Image.open(store.blob_path(spec.color_image)).convert("RGB"))
    # color_image 是「上傳的原圖」透視攤平的結果，沒有經過增強：自己用同一個模組攤平一次，逐像素相同
    raw = np.asarray(Image.open(photo_jpeg).convert("RGB"))
    quad = pg.find_page_quad(raw, {"input_type": "photo"})
    flat, _ = pg.rectify(raw, quad)
    assert color.shape == flat.shape and np.array_equal(color, flat)
    assert work.shape == color.shape and np.abs(work.astype(int) - color.astype(int)).mean() > 5  # 工作影像是增強過的
    assert any("color_image" in n for n in spec.notes)
