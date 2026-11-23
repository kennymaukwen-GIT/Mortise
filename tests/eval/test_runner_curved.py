"""runner 的彎曲照片變化（curved_<種類>）：逐像素真值對回、攤平指標、以原生 PDF 文字層為答案的 CER。

影像產線換成「整頁當一張圖」的替身（只測評測機制本身）；另有一個逐點座標轉換的單元測試。"""

import shutil
from pathlib import Path

import numpy as np

from mortise.artifacts import ElementList, ImageElement, ImageKind, ProductionLine, Provenance, Quad, make_element_id, measured
from mortise.eval import metrics as M
from mortise.eval import runner as R
from mortise.stages import s3_recognize

FIX = Path(__file__).resolve().parents[1] / "fixtures"
_REAL_S3 = s3_recognize.run


def _stub_s3(doc, spec, cfg, corrections, store, *args, **kwargs):
    if doc.media_type == "application/pdf":
        return _REAL_S3(doc, spec, cfg, corrections, store, *args, **kwargs)
    page = Quad.from_rect(0.0, 0.0, 1.0, spec.aspect_ratio)
    img = ImageElement(id=make_element_id(spec.page_index, "img", page, spec.working_image.sha256[:12]), quad=page, z=0, image=spec.working_image,
                       pixel_size=spec.working_image_size, kind=measured(ImageKind.FULL_PAGE, 1.0, Provenance.INFERRED, "test.stub@1"))
    return ElementList(page_index=spec.page_index, production_line=ProductionLine.IMAGE, images=[img])


def test_curved_variant_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(s3_recognize, "run", _stub_s3)
    samples = tmp_path / "samples"
    samples.mkdir()
    shutil.copy(FIX / "slide_basic.pdf", samples / "slide_basic.pdf")
    summary = R.run_batch(samples, tmp_path / "out", variants=["curved_crease"],
                          params={"synth": {"curved_out_long": 900, "curved_glare": False}, "max_pages_per_sample": 1})
    assert not summary["errors"], summary["errors"]
    t = summary["by_type"]["synth_curved_crease"]
    assert t["page_coverage"]["mean"] > 0.5 and 0.0 <= t["background_frac"]["mean"] < 0.5
    assert t["dewarp_pos_p95_px"]["mean"] < 80  # 整頁當圖的替身也有頁面幾何：透視攤平後的位置誤差
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "合成彎曲照片：折痕" in md and "攤平位置誤差 p95" in md


def test_transform_elements_pointwise_drops_unmapped():
    q1 = Quad.from_rect(0.1, 0.1, 0.3, 0.2)
    q2 = Quad.from_rect(0.8, 0.8, 0.9, 0.9)
    els = ElementList(page_index=0, production_line=ProductionLine.IMAGE, images=[
        ImageElement(id="a", quad=q1, z=0, image=R._DUMMY_BLOB, pixel_size=(1, 1), kind=measured(ImageKind.PHOTO, 1.0, Provenance.INFERRED, "t@1")),
        ImageElement(id="b", quad=q2, z=1, image=R._DUMMY_BLOB, pixel_size=(1, 1), kind=measured(ImageKind.PHOTO, 1.0, Provenance.INFERRED, "t@1"))])

    def fn(pts):
        out = pts * 2.0 + 0.05 * np.sin(pts[:, ::-1] * 3)  # 非線性
        out[pts[:, 0] > 0.7] = np.nan  # 右下那塊對不到紙
        return out

    got = M.transform_elements_pointwise(els, fn)
    assert [i.id for i in got.images] == ["a"]
    tl = got.images[0].quad.tl
    assert abs(tl.x - (0.2 + 0.05 * np.sin(0.3))) < 1e-9 and abs(tl.y - (0.2 + 0.05 * np.sin(0.3))) < 1e-9
