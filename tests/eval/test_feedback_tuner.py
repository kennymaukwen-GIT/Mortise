"""回饋的存取、轉標註資料、套用到 Pipeline、校準表；tuner 的座標下降與在小樣本上跑一輪。"""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from mortise.artifacts import Intent, StyledDoc
from mortise.config import Config
from mortise.eval import feedback as F
from mortise.eval import tuner as T
from mortise.eval.runner import load_artifacts
from mortise.pipeline import Pipeline

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    pipe = Pipeline(tmp_path_factory.mktemp("fbjob"))
    pipe.start(FIX / "slide_basic.pdf", "slide_basic.pdf", Intent())
    pipe.run()
    return pipe, load_artifacts(pipe)


def _feedback(run):
    line = run.elements[0].texts[0]
    box = run.styled.pages[0].text_boxes[0]
    return {
        "sample": "slides/slide_basic.pdf", "comment": "標題字型不對",
        "page_ratings": [{"page_index": 0, "score": 3, "max_score": 5}],
        "corrections": [
            {"target_stage": 5, "page_index": 0, "element_id": box.id, "field": "font_family", "new_value": "Carlito"},
            {"target_stage": 3, "page_index": 0, "element_id": line.id, "field": "text", "new_value": "Mortise 季度報告", "system_value": line.text, "system_confidence": 0.5},
        ],
        "confirmations": [{"target_stage": 3, "page_index": 0, "element_id": run.elements[0].texts[1].id, "field": "font_size"}],
    }


def test_store_roundtrip_annotations_and_stats(job, tmp_path):
    pipe, run = job
    store = F.FeedbackStore(tmp_path / "fb")
    p1 = store.add(_feedback(run))
    p2 = store.add(_feedback(run))  # 同內容重複存：同一個 id，不會變兩份
    assert p1 == p2 and store.samples() == ["slides/slide_basic.pdf"]
    fbs = store.get("slides/slide_basic.pdf")
    assert len(fbs) == 1 and len(fbs[0].corrections) == 2 and fbs[0].comments == ["標題字型不對"]
    assert all(c.source.value == "user" and c.id.startswith("fb-") for c in fbs[0].corrections)
    rows = store.to_annotations(resolver=lambda s: run)
    assert len(rows) == 3
    font = next(r for r in rows if r["field"] == "font_family")
    assert font["resolved"] and font["system_value"] == run.styled.pages[0].text_boxes[0].paragraphs[0].runs[0].font.family.value
    assert font["correct_value"] == "Carlito" and font["corrected"] is True
    text = next(r for r in rows if r["field"] == "text")
    assert text["system_confidence"] == 0.5  # 快照優先
    conf = next(r for r in rows if r["kind"] == "confirmation")
    expect = run.elements[0].texts[1].runs[0].font_size
    assert conf["system_value"] == expect.value and conf["system_confidence"] == expect.confidence and conf["corrected"] is False
    n = store.write_annotations(tmp_path / "ann.jsonl", resolver=lambda s: run)
    stats = F.correction_stats(F.read_annotations(tmp_path / "ann.jsonl"))
    assert n == 3 and stats["5:font_family"]["correction_ratio"] == 1.0 and stats["3:font_size"]["correction_ratio"] == 0.0


def test_apply_feedback_locks_user_value(job):
    pipe, run = job
    fb = F.parse_feedback(_feedback(run))
    fb.corrections = [c for c in fb.corrections if c.field == "font_family"]
    F.apply_feedback(pipe, fb)
    F.apply_feedback(pipe, fb)  # 再套一次不會重複加入
    assert sum(1 for c in pipe.corrections if c.field == "font_family") == 1
    fam = pipe.store.load("s5/styled_doc", StyledDoc).pages[0].text_boxes[0].paragraphs[0].runs[0].font.family
    assert fam.value == "Carlito" and fam.locked


def test_calibration_table_and_insufficient_data():
    rng = np.random.default_rng(0)
    conf = rng.uniform(0, 1, 3000)
    ok = rng.uniform(0, 1, 3000) < conf ** 2  # 真正的可靠度是信心值的平方（系統過度自信）
    tab = F.build_calibration(zip(conf, ok))
    assert tab.sufficient and tab.ece > 0.1
    assert tab.calibrate(0.5) == pytest.approx(0.25, abs=0.05) and tab.calibrate(0.9) == pytest.approx(0.81, abs=0.05)
    assert all(a <= b + 1e-12 for a, b in zip(tab.knots_y, tab.knots_y[1:]))
    small = F.build_calibration([(0.9, True)] * 10)
    assert not small.sufficient and "不足" in small.reason and small.calibrate(0.9) == 0.9
    narrow = F.build_calibration([(0.95, True)] * 80)
    assert not narrow.sufficient and "分箱" in narrow.reason


def test_coordinate_descent_finds_peak():
    ts = [T.TunableParam("a.x", 0.0, 2.0, 0.1), T.TunableParam("a.y", values=[1, 2, 4, 8])]
    res = T.coordinate_descent(ts, {"a.x": 0.5, "a.y": 1}, lambda v: -(v["a.x"] - 1.2) ** 2 - abs(v["a.y"] - 4), {"max_steps_per_param": 20, "max_rounds": 3})
    assert res.best["a.x"] == pytest.approx(1.2) and res.best["a.y"] == 4 and res.train_after > res.train_before
    assert {p.path: p.after for p in res.params} == res.best


def test_tunable_list_and_override_roundtrip(tmp_path):
    ts = T.load_tunable()
    paths = {t.path for t in ts}
    assert "structure.paragraph_gap_factor" in paths and any(p.startswith("settings.") for p in paths)
    cfg = Config.load()
    assert all(T.get_value(cfg, t.path) is not None for t in ts)  # 清單裡的參數都真的存在於設定檔
    path = T.write_override({"structure.paragraph_gap_factor": 1.6, "settings.single_line_no_wrap": False}, tmp_path / "o.toml", "測試")
    cfg2 = Config.load(override=path)
    assert cfg2.section("structure")["paragraph_gap_factor"] == 1.6 and cfg2.section("settings")["single_line_no_wrap"] is False
    assert cfg2.section("structure")["line_pitch_tolerance"] == cfg.section("structure")["line_pitch_tolerance"]


def test_tune_runs_one_round_on_small_sample(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    shutil.copy(FIX / "slide_basic.pdf", root / "a.pdf")
    shutil.copy(FIX / "slide_basic.pdf", root / "b.pdf")
    ts = [t for t in T.load_tunable() if t.path in ("structure.paragraph_gap_factor", "settings.box_width_slack_pt")]
    res = T.tune(root, tmp_path / "out", ts, params={"max_rounds": 1, "max_steps_per_param": 1, "validation_fraction": 0.5})
    assert res.train_before is not None and res.train_after >= res.train_before
    assert len(res.train_samples) == 1 and len(res.validation_samples) == 1 and res.validation_before is not None
    assert res.adopt is not None and 3 <= res.evaluations <= 5
    rep = json.loads((tmp_path / "out" / "tune_report.json").read_text(encoding="utf-8"))
    assert len(rep["params"]) == 2 and all(p["tried"] for p in rep["params"])
    Config.load(override=Path(res.override_path))  # 覆寫檔可讀
