"""引擎版本（任何一個組成變了版本就變）與成績單（跑一份小考卷、寫出 JSON 與 Markdown、比較與「不可退步」的門檻）。"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mortise import engine
from mortise.config import Config
from mortise.eval import scorecard as S


# ====================================================================== 引擎版本


def test_engine_version_is_stable_and_complete():
    v1, v2 = engine.engine_version(), engine.engine_version()
    assert v1 == v2
    assert re.fullmatch(r"mortise-[\w.]+\+s1v\w+\.s2v\w+\.s3v\w+\.s4v\w+\.s5v\w+\.s6v\w+\.s7v\w+-cfg[0-9a-f]{8}-ocr[0-9a-f]{6}-uv\w+-src[0-9a-f]{8}", v1), v1
    name = engine.short_name(v1)
    assert name == engine.short_name(v1) and re.fullmatch(r"\w\w-\d\d", name)
    comp = engine.components()
    assert set(comp["stages"]) == {f"s{i}" for i in range(1, 8)}
    assert comp["ocr"]["files"]["rec"]["sha256"] and comp["ocr"]["files"]["det"]["sha256"]


def test_engine_version_changes_with_config_and_models(tmp_path):
    base = Config.load()
    v0 = engine.engine_version(base)
    tweaked = Config(copy.deepcopy(base.data))
    tweaked.data["recognize"]["ocr_low_score"] = 0.7
    assert engine.engine_version(tweaked) != v0  # 門檻改了 → 新版本
    rec = engine.ocr_model_paths(base)["rec"]
    fake = tmp_path / "my_rec.onnx"
    shutil.copy(rec, fake)
    swapped = Config(copy.deepcopy(base.data))
    swapped.data["recognize"]["ocr_params"] = {"rec_model_path": str(fake)}
    vs = engine.engine_version(swapped)
    assert vs.split("-ocr")[1][:6] == v0.split("-ocr")[1][:6]  # 同一個檔換個地方：內容沒變，模型雜湊不變
    with fake.open("ab") as f:
        f.write(b"\0")
    engine._HASH_CACHE.clear()
    vf = engine.engine_version(swapped)
    assert vf.split("-ocr")[1][:6] != v0.split("-ocr")[1][:6]  # 換上微調過的模型 → 新版本
    assert engine.components(swapped)["ocr"]["files"]["rec"]["path"] == str(fake)


# ====================================================================== 小零件


def test_format_scores_from_report_notes():
    note = ("各格式的分數：可編輯 PDF 0.545；SVG 0.553；PowerPoint 0.405；Word（可重排） 0.143；Excel 0.700（代用值：這個格式還沒有自己的驗證；分數來自成品文件的中性渲染）。"
            "整體分數以最差的格式為準")
    got = S.format_scores(SimpleNamespace(notes=[note, "其他"]))
    assert got == {"pdf": 0.545, "svg": 0.553, "pptx": 0.405, "docx_flow": 0.143, "xlsx": 0.7}
    assert S.format_scores(SimpleNamespace(notes=["沒有"])) == {}
    assert S.format_scores(SimpleNamespace(notes=[], format_scores={"pdf": 0.9})) == {"pdf": 0.9}


def test_table_metrics():
    assert S.table_metrics([], [(3, 2)]) == {}
    assert S.table_metrics([(4, 3)], [(4, 3)]) == {"table_found": 1.0, "table_shape_exact": 1.0}
    assert S.table_metrics([(4, 3)], [(5, 3)]) == {"table_found": 1.0, "table_shape_exact": 0.0}
    assert S.table_metrics([(4, 3), (2, 2)], []) == {"table_found": 0.0, "table_shape_exact": 0.0}


def _card(version: str, evals: list[tuple[str, str, dict]], seed: int = 0) -> dict:
    ev = [{"key": f"{s}|{v}", "sample": s, "group": "g", "variant": v, "category": S.category(v), "pages": 1, "seconds": 1.0, "metrics": m}
          for s, v, m in evals]
    return {"schema": S.SCHEMA, "engine": {"version": version, "name": "測試-00", "components": {}}, "suite": "quick", "seed": seed, "evaluations": ev}


def test_compare_gates_and_exit_codes(tmp_path):
    old = _card("old", [("a.pdf", "photo", {"page_cer": 0.05, "quad_success": 1.0, "seconds_per_page": 10.0}),
                        ("a.pdf", "screenshot", {"page_cer": 0.01, "e2e_score": 0.8}),
                        ("b.pdf", "scan", {"page_cer": 0.02})])
    same = S.compare(old, old)
    assert same["regressions"] == [] and same["common"] == 3 and same["same_engine"]
    new = copy.deepcopy(old)
    new["engine"]["version"] = "new"
    new["evaluations"][0]["metrics"]["quad_success"] = 0.0  # 照片四角變差：門檻 0，一定擋
    new["evaluations"][1]["metrics"]["e2e_score"] = 0.79  # 小退步：在門檻 0.02 內，只標粗體
    new["evaluations"][0]["metrics"]["seconds_per_page"] = 30.0  # 時間預設不設門檻
    res = S.compare(old, new)
    regs = {(r["scope"], r["metric"]) for r in res["regressions"]}
    assert regs == {("overall", "quad_success"), ("photo", "quad_success")} and res["problems"] == []
    e2e = next(r for r in res["rows"] if r["scope"] == "overall" and r["metric"] == "e2e_score")
    assert e2e["status"] == "worse"
    text = S.render_compare(res)
    assert "**退步（超過門檻）**" in text and "不建議採用" in text and "**-0.010**" in text and "**退步**（門檻內）" in text
    # 門檻：拿掉預設、相對值
    assert S.compare(old, new, S.parse_gates(["quad_success=off"]))["regressions"] == []
    slow = S.compare(old, new, S.parse_gates(["quad_success=off", "seconds_per_page=50%"]))
    assert {r["metric"] for r in slow["regressions"]} == {"seconds_per_page"}
    with pytest.raises(ValueError):
        S.parse_gates(["page_cer"])
    # 命令列：沒有退步 0、有退步 1、檔案不對 2
    po, pn = tmp_path / "old.json", tmp_path / "new.json"
    po.write_text(json.dumps(old), encoding="utf-8")
    pn.write_text(json.dumps(new), encoding="utf-8")
    assert S.main_cli(["compare", str(po), str(po)], print) == 0
    assert S.main_cli(["compare", str(po), str(pn), "--md", str(tmp_path / "cmp.md")], print) == 1
    assert "退步" in (tmp_path / "cmp.md").read_text(encoding="utf-8")
    assert S.main_cli(["compare", str(po), str(pn), "--gate", "quad_success=off"], print) == 0
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    assert S.main_cli(["compare", str(po), str(tmp_path / "bad.json")], print) == 2


def test_compare_blocks_crashed_items_missing_metrics_and_new_errors(tmp_path):
    """新版本在照片題當掉（題目不見、只剩錯誤）時，閘門一定要擋：缺題、新的錯誤、缺指標都算超過門檻的退步。"""
    old = _card("old", [("a.pdf", "photo", {"page_cer": 0.05, "quad_success": 1.0}),
                        ("a.pdf", "screenshot", {"page_cer": 0.01, "e2e_score": 0.8}),
                        ("b.pdf", "scan", {"page_cer": 0.02, "font_top1": 0.5}),
                        ("b.pdf", "curved_bend", {"page_cer": 0.10})])
    crashed = copy.deepcopy(old)
    crashed["engine"]["version"] = "new"
    crashed["evaluations"] = [e for e in crashed["evaluations"] if e["category"] not in ("photo", "curved")]
    crashed["errors"] = [{"sample": "a.pdf", "where": "synth_photo", "error": "ValueError: boom"}]
    res = S.compare(old, crashed)
    kinds = sorted((p["kind"], p["key"]) for p in res["problems"])
    assert kinds == [("missing_item", "a.pdf|photo"), ("missing_item", "b.pdf|curved_bend"), ("new_error", "a.pdf|synth_photo")]
    assert len(res["regressions"]) == 3 and next(p for p in res["problems"] if p["kind"] == "new_error")["scope"] == "photo"
    text = S.render_compare(res)
    assert "**缺題：a.pdf|photo**" in text and "不建議採用" in text and "可以採用" not in text
    po, pn = tmp_path / "old.json", tmp_path / "crashed.json"
    po.write_text(json.dumps(old), encoding="utf-8")
    pn.write_text(json.dumps(crashed), encoding="utf-8")
    assert S.main_cli(["compare", str(po), str(pn)], print) == 1
    assert S.main_cli(["compare", str(po), str(pn), "--allow-missing"], print) == 0  # 確定是故意的：只列出
    # 舊的也有同一個錯誤：不是新的錯誤
    old_err = copy.deepcopy(old)
    old_err["errors"] = list(crashed["errors"])
    assert [p["kind"] for p in S.compare(old_err, crashed)["problems"]] == ["missing_item", "missing_item"]
    # 同一題缺了設門檻的指標：退步；沒設門檻的只列出。平均是配對的：只比兩邊都有的題目
    gap = copy.deepcopy(old)
    gap["engine"]["version"] = "gap"
    del gap["evaluations"][2]["metrics"]["page_cer"]
    del gap["evaluations"][2]["metrics"]["font_top1"]
    res = S.compare(old, gap)
    assert [(p["kind"], p["key"], p["metric"]) for p in res["problems"]] == [("missing_metric", "b.pdf|scan", "page_cer")]
    assert [r["metric"] for r in res["ungated_missing"]] == ["font_top1"]
    cer = next(r for r in res["rows"] if r["scope"] == "overall" and r["metric"] == "page_cer")
    assert cer["n"] == 3 and cer["old"] == cer["new"] and cer["status"] == "same"


def test_compare_warns_when_the_exam_changed(tmp_path):
    items = S.SUITES["quick"]
    a = _card("a", [("a.pdf", "photo", {"page_cer": 0.05})])
    b = copy.deepcopy(a)
    a["exam"] = S.exam_fingerprint(items)
    b["exam"] = copy.deepcopy(a["exam"])
    assert a["exam"]["files"]["slide_basic.pdf"] and set(a["exam"]["code"]) == set(S.EXAM_CODE)
    assert S.exam_diff(a, b) == {"same": True, "notes": []}
    b["exam"]["code"]["synth.py"] = "0" * 64
    b["exam"]["files"]["doc_a4.pdf"] = "1" * 64
    d = S.exam_diff(a, b)
    assert d["same"] is False and any("synth.py" in n for n in d["notes"]) and any("doc_a4.pdf" in n for n in d["notes"])
    assert "考卷不一定相同" in S.render_compare(S.compare(a, b))
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    assert S.main_cli(["compare", str(pa), str(pb)], print) == 0  # 只警告
    assert S.main_cli(["compare", str(pa), str(pb), "--strict-exam"], print) == 3
    assert S.main_cli(["compare", str(pa), str(pa), "--strict-exam"], print) == 0
    del a["exam"]
    assert S.exam_diff(a, b)["same"] is None  # 較早的成績單沒有指紋：不能確認


def test_latest_summary_prefers_current_engine(tmp_path):
    a = _card("v-a", [("a.pdf", "photo", {"page_cer": 0.1})])
    b = _card("v-b", [("a.pdf", "photo", {"page_cer": 0.2})])
    for c, created in ((a, "2026-01-01T00:00:00"), (b, "2026-02-01T00:00:00")):
        c.update({"created": created, "headline": {"page_cer": {"mean": c["evaluations"][0]["metrics"]["page_cer"], "n": 1, "scope": "images"}}})
        (tmp_path / f"{c['engine']['version']}.json").write_text(json.dumps(c), encoding="utf-8")
    assert S.latest_summary("v-a", [tmp_path])["version"] == "v-a"
    newest = S.latest_summary("other", [tmp_path])
    assert newest["version"] == "v-b" and newest["matches_current"] is False and newest["headline"]["page_cer"]["mean"] == 0.2
    assert S.latest_summary("x", [tmp_path / "none"]) is None


# ====================================================================== 跑一份小考卷


def test_small_scorecard_end_to_end(tmp_path):
    items = [S.Item("slide_basic.pdf", "fixture:slide_basic.pdf", "簡報", ["photo"])]
    card = S.run_scorecard(tmp_path / "out", "quick", seed=0, jobs=1, items=items, outputs=["pdf"])
    js, md = Path(card["_paths"]["json"]), Path(card["_paths"]["md"])
    assert js.name == f"{card['engine']['version']}.json" and md.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["schema"] == S.SCHEMA and data["errors"] == [] and data["engine"]["version"] == engine.engine_version()
    assert data["exam"]["files"] == {"slide_basic.pdf": S.exam_fingerprint(items)["files"]["slide_basic.pdf"]} and data["exam"]["hash"]
    by = {e["variant"]: e for e in data["evaluations"]}
    assert set(by) == {"native", "photo"}
    photo = by["photo"]["metrics"]
    assert photo["fmt_pdf"] > 0 and photo["page_cer"] < 0.2 and photo["seconds_per_page"] > 0
    assert photo["quad_success"] == 1.0 and photo["quad_confident_wrong"] == 0.0  # 平的照片：四角要找對
    if "page_quad_success" in photo:  # ②有交出照片四角的欄位時，也量整條流程
        assert photo["page_quad_success"] == 1.0 and photo["page_quad_silent_wrong"] == 0.0
    assert by["native"]["metrics"]["fmt_pdf"] > 0.9
    assert data["headline"]["quad_success"]["n"] == 1 and data["summary"]["by_type"]["photo"]["page_cer"]["n"] == 1
    text = md.read_text(encoding="utf-8")
    assert "成績單" in text and "照片四角成功率" in text and "平的照片" in text
    # 同一份成績單比自己：沒有退步
    assert S.main_cli(["compare", str(js), str(js)], print) == 0
