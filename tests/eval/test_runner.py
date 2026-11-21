"""runner：四份 fixture 跑完整批（原生＋三種合成干擾），產生三種輸出；人工標準答案、回饋、壞檔、基準比較。"""

import json
import shutil
from pathlib import Path

import pytest

from mortise.config import Config
from mortise.eval import runner as R
from mortise.eval.__main__ import main as eval_main

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    root = tmp_path_factory.mktemp("samples")
    (root / "slides").mkdir()
    (root / "docs").mkdir()
    for n in ("slide_basic", "slide_dark"):
        shutil.copy(FIX / f"{n}.pdf", root / "slides" / f"{n}.pdf")
    for n in ("doc_a4", "poster"):
        shutil.copy(FIX / f"{n}.pdf", root / "docs" / f"{n}.pdf")
    # 標準答案：由擷取產線的結果產生草稿（等於人工確認過的答案）
    _, run = R.run_pipeline(tmp_path_factory.mktemp("tjob"), root / "slides" / "slide_basic.pdf", Config.load(), ["pptx"])
    R.write_truth_template(run, root / "slides" / "slide_basic.truth.json")
    (root / "slides" / "slide_basic.feedback.json").write_text(json.dumps({"page_ratings": [{"page_index": 0, "score": 4, "max_score": 5}]}), encoding="utf-8")
    (root / "broken.pdf").write_bytes(b"%PDF-1.7\n this is not really a pdf")
    out = tmp_path_factory.mktemp("out")
    summary = R.run_batch(root, out, variants=["screenshot", "scan", "photo"])
    return root, out, summary


def test_three_outputs_and_grouping(batch):
    root, out, s = batch
    assert (out / "summary.json").exists() and (out / "summary.md").exists()
    assert len(list((out / "samples").glob("*.json"))) == 5
    assert s["n_samples"] == 5 and s["n_failed_samples"] == 1
    assert set(s["by_type"]) == {"native_pdf", "synth_screenshot", "synth_scan", "synth_photo"}
    assert set(s["by_group"]) == {"slides", "docs"}
    assert s["n_evaluations"] == 16  # 4 個樣本 ×（原生 + 3 種干擾）
    assert any(e["sample"] == "broken.pdf" for e in s["errors"])  # 壞檔記錄錯誤，整批照跑
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "各指標平均" in md and "依評測類型" in md and "依分組" in md and "最差的" in md
    assert len(s["worst"]) == 10
    sample = json.loads((out / "samples" / "docs__doc_a4.pdf.json").read_text(encoding="utf-8"))
    photo = next(e for e in sample["evaluations"] if e["variant"] == "photo")
    assert len(photo["degrade"]) == 2 and photo["degrade"][0]["kind"] == "photo"  # 兩頁都評
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


def test_image_line_stub_scores_low_but_honest(batch):
    _, _, s = batch
    for t in ("synth_screenshot", "synth_scan", "synth_photo"):
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
