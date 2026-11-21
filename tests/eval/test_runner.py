"""runner：四份 fixture 跑完整批（原生＋三種合成干擾），產生三種輸出；人工標準答案、回饋、壞檔、基準比較。

整批測試把影像產線換成「整頁當一張圖」的替身（只測評測機制本身，且不受影像辨識模組的速度影響）；
另有一個測試用真的影像產線跑一張截圖，確認座標對回與指標在真實辨識結果上數字合理。"""

import json
import shutil
from pathlib import Path

import pytest

from mortise.artifacts import ElementList, ImageElement, ImageKind, ProductionLine, Provenance, Quad, make_element_id, measured
from mortise.artifacts import Layout
from mortise.config import Config
from mortise.eval.truth import render_truth
from mortise.stages import s3_recognize, s4_structure
from mortise.eval import runner as R
from mortise.eval.__main__ import main as eval_main

FIX = Path(__file__).resolve().parents[1] / "fixtures"


_REAL_S3 = s3_recognize.run


def _stub_s3(doc, spec, cfg, corrections, store, *args, **kwargs):
    """影像產線替身：整頁當成一張圖片（原生 PDF 照常走擷取產線）。"""
    if doc.media_type == "application/pdf":
        return _REAL_S3(doc, spec, cfg, corrections, store, *args, **kwargs)
    page = Quad.from_rect(0.0, 0.0, 1.0, spec.aspect_ratio)
    img = ImageElement(id=make_element_id(spec.page_index, "img", page, spec.working_image.sha256[:12]), quad=page, z=0, image=spec.working_image,
                       pixel_size=spec.working_image_size, kind=measured(ImageKind.FULL_PAGE, 1.0, Provenance.INFERRED, "test.stub@1"))
    return ElementList(page_index=spec.page_index, production_line=ProductionLine.IMAGE, images=[img])


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(s3_recognize, "run", _stub_s3)
        yield _batch(tmp_path_factory)


def _batch(tmp_path_factory):
    root = tmp_path_factory.mktemp("samples")
    (root / "slides").mkdir()
    (root / "docs").mkdir()
    for n in ("slide_basic", "slide_dark"):
        shutil.copy(FIX / f"{n}.pdf", root / "slides" / f"{n}.pdf")
    for n in ("doc_a4", "poster"):
        shutil.copy(FIX / f"{n}.pdf", root / "docs" / f"{n}.pdf")
    # 標準答案：由擷取產線的結果產生草稿（等於人工確認過的答案）；直接呼叫③④，不跑完整流水線以節省時間
    _, el, spec = render_truth(root / "slides" / "slide_basic.pdf", 0, 150)
    layout = Layout(pages=[s4_structure.run_page(el, spec, Config.load(), [])])
    R.write_truth_template(R.RunArtifacts(None, [spec], [el], layout, None, None), root / "slides" / "slide_basic.truth.json")
    (root / "slides" / "slide_basic.feedback.json").write_text(json.dumps({"page_ratings": [{"page_index": 0, "score": 4, "max_score": 5}]}), encoding="utf-8")
    (root / "broken.pdf").write_bytes(b"%PDF-1.7\n this is not really a pdf")
    out = tmp_path_factory.mktemp("out")
    # 截圖干擾由 test_real_image_line_screenshot_through_runner 走過 runner；這裡只跑照片（座標對回最複雜的一種），控制測試時間
    summary = R.run_batch(root, out, variants=["photo"], params={"max_pages_per_sample": 1})
    return root, out, summary


def test_three_outputs_and_grouping(batch):
    root, out, s = batch
    assert (out / "summary.json").exists() and (out / "summary.md").exists()
    assert len(list((out / "samples").glob("*.json"))) == 5
    assert s["n_samples"] == 5 and s["n_failed_samples"] == 1
    assert set(s["by_type"]) == {"native_pdf", "synth_photo"}
    assert set(s["by_group"]) == {"slides", "docs"}
    assert s["n_evaluations"] == 8  # 4 個樣本 ×（原生 + 照片干擾）
    assert any(e["sample"] == "broken.pdf" for e in s["errors"])  # 壞檔記錄錯誤，整批照跑
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "各指標平均" in md and "依評測類型" in md and "依分組" in md and "最差的" in md
    assert len(s["worst"]) == 8 and s["worst"][0]["composite"] <= s["worst"][-1]["composite"]
    sample = json.loads((out / "samples" / "docs__doc_a4.pdf.json").read_text(encoding="utf-8"))
    photo = next(e for e in sample["evaluations"] if e["variant"] == "photo")
    assert len(photo["degrade"]) == 1 and photo["degrade"][0]["kind"] == "photo"  # max_pages_per_sample=1：兩頁的文件只評第一頁
    assert sample["pages"] == 2 and photo["degrade"][0]["page_index"] == 0
    assert "3_辨識" in sample["evaluations"][0]["confidence"]


def test_native_numbers_and_truth(batch):
    _, out, s = batch
    nat = s["by_type"]["native_pdf"]
    # 擷取產線 + ⑦目標驗證：基準數字（LibreOffice 替身引擎）
    assert nat["e2e_score"]["mean"] > 0.85 and nat["e2e_similarity"]["mean"] > 0.75
    assert nat["rasterized_page"]["mean"] == 0.0
    sb = json.loads((out / "samples" / "slides__slide_basic.pdf.json").read_text(encoding="utf-8"))
    m = sb["evaluations"][0]["metrics"]
    assert m["text_recall"] == 1.0 and m["cer"] == 0.0 and m["font_top1"] == 1.0 and m["paragraph_pair_f1"] == 1.0
    assert m["user_rating"] == pytest.approx(0.8)


def test_rasterized_image_line_scores_low_but_honest(batch):
    _, _, s = batch
    for t in ("synth_photo",):
        tab = s["by_type"][t]
        assert tab["text_recall"]["mean"] == 0.0 and tab["page_cer"]["mean"] == 1.0  # 暫時版影像產線：整頁當圖片
        assert tab["rasterized_page"]["mean"] == 1.0
        assert tab["composite"]["mean"] < 0.35


def test_baseline_comparison_flags_regressions(batch):
    _, out, s = batch
    base = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    base["overall"]["e2e_score"]["mean"] += 0.05  # 假裝之前比較好
    base["overall"]["page_cer"]["mean"] += 0.5  # 假裝之前比較差
    cmp = R.compare_to_baseline(s, base)
    assert cmp["overall"]["e2e_score"]["status"] == "regression"
    assert cmp["overall"]["page_cer"]["status"] == "improved"
    assert cmp["overall"]["text_recall"]["status"] == "same"
    md = R.render_markdown({**s, "baseline": cmp})
    assert "退步" in md and "還原度總分" in md


def test_cli_run_with_limit_and_no_variants(batch, tmp_path):
    root, out, _ = batch
    code = eval_main(["run", str(root / "slides"), "--out", str(tmp_path), "--variants", "", "--limit", "1", "--baseline", str(out / "summary.json")])
    s = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert s["n_samples"] == 1 and "baseline" in s and code in (0, 1)


def test_real_image_line_screenshot_through_runner(tmp_path):
    """真的影像產線：slide_basic 畫成 100 dpi 的截圖（不壓 JPEG），評測數字應接近實測水準。"""
    root = tmp_path / "s"
    root.mkdir()
    shutil.copy(FIX / "slide_basic.pdf", root / "slide_basic.pdf")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(s3_recognize, "run", _REAL_S3)  # 整批測試的替身在模組結束前都還在，這裡明確換回真的
        s = R.run_batch(root, tmp_path / "out", variants=["screenshot"],
                        params={"synth": {"screenshot_dpi_min": 100.0, "screenshot_dpi_max": 100.0, "screenshot_jpeg_prob": 0.0}})
    m = {k: v["mean"] for k, v in s["by_type"]["synth_screenshot"].items()}
    print({k: round(v, 4) for k, v in m.items()})
    # 門檻設在 2026-11-21 實測值附近（見回報）：召回 1.0、精確 1.0、整頁字元錯誤率約 0.006、位置誤差約 1.7 pt
    assert m["rasterized_page"] == 0.0 and m["text_recall"] >= 0.85 and m["text_precision"] >= 0.85
    assert m["page_cer"] <= 0.05 and m["position_error_pt"] <= 3.0 and m["orientation_accuracy"] == 1.0
    assert m["size_error_pct"] <= 5.0 and m["font_top3"] >= 0.7 and m["paragraph_pair_f1"] >= 0.7
