"""回饋 API：送評分與修正 → 列出回饋 → 匯出回饋包（zip）→ 用 FeedbackStore.import_bundle 與 eval 子指令讀回、
轉成標註、建校準表；網頁送的⑤段落文字修正變成 OCR 訓練資料；GET /api/engine。
另有回饋格式的小測試：③多段文字行的系統值、校準的選擇偏差、不受信任的回饋包（路徑穿越）、舊版程式讀新檔。
用 tests/fixtures 的虛構投影片。"""

from __future__ import annotations

import importlib
import io
import json
import os
import time
import zipfile
from pathlib import Path

import pytest

FIX = Path(__file__).parents[1] / "fixtures" / "slide_basic.pdf"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in ("MORTISE_JOBS", "MORTISE_FEEDBACK", "MORTISE_OFFLINE")}
    os.environ["MORTISE_JOBS"] = str(tmp_path_factory.mktemp("jobs"))
    os.environ.pop("MORTISE_FEEDBACK", None)  # 用預設：工作目錄/_feedback
    os.environ["MORTISE_OFFLINE"] = "1"
    import mortise.api.app as appmod

    importlib.reload(appmod)
    yield TestClient(appmod.app)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _wait(client, job_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.3)
    raise AssertionError("工作逾時")


def _job(client, consent: bool) -> str:
    with FIX.open("rb") as f:
        r = client.post("/api/jobs", files={"file": ("slide_basic.pdf", f, "application/pdf")},
                        data={"outputs": "pptx", "allow_training": "true" if consent else "false"})
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert _wait(client, job_id)["status"] == "done"
    return job_id


def _pipe(job_id: str):
    import mortise.api.app as appmod

    return appmod.JOBS.pipeline(job_id)


def _give_feedback(client, job_id: str) -> dict:
    """評分＋標籤、標一個元素是錯的、一個是對的、像網頁一樣對一個元素回報錯誤（只有標籤）；改一個文字框的字型（⑤）、一行字（③，有好幾個樣式段的標題行），
    以及像網頁一樣用⑤段落編號改一段的字（多行段落只改第二行以後某一行的一個字）。回傳元素資訊。"""
    from mortise.eval.runner import load_artifacts, page_layout

    el = client.get(f"/api/jobs/{job_id}/pages/1/elements").json()
    box, other, third = el["text_boxes"][0], el["text_boxes"][1], el["text_boxes"][2]
    run = load_artifacts(_pipe(job_id))
    lines = {t.id: t for t in run.elements[0].texts}
    title = next(t for t in run.elements[0].texts if len(t.runs) > 1)  # 有好幾個樣式段的行
    lay = page_layout(run, 0)

    def short_later_lines(p) -> list[int]:  # 第二行以後、標籤在 25 字以內的行（太長的行 ocr-dataset 會略過）
        return [n for n in range(1, len(p.line_ids)) if 3 <= len(lines[p.line_ids[n]].text) <= 24]

    par = next(p for p in lay.paragraphs if len(p.line_ids) >= 2 and all(lid in lines for lid in p.line_ids) and short_later_lines(p))
    texts = [lines[lid].text for lid in par.line_ids]
    j = short_later_lines(par)[0]
    line = texts[j]
    k = len(line) // 2
    while not line[k].isalpha():  # 改那一行中間的一個字（不在換行處）
        k += 1
    edited = texts[:j] + [line[:k] + ("Q" if line[k] != "Q" else "R") + line[k + 1:]] + texts[j + 1:]
    joiners = list(par.joiners) + [""] * len(texts)
    par_text = "".join(t + (joiners[n] if n < len(texts) - 1 else "") for n, t in enumerate(edited))
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 4, "page": 1, "comment": "標題字型不對", "tags": ["font_wrong"]}).status_code == 201
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"page": 1, "element_id": box["id"], "correct": False, "tags": ["position_wrong"]}).status_code == 201
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"page": 1, "element_id": other["id"], "correct": True}).status_code == 201
    # 網頁的「回報錯誤」表單：元素編號＋標籤，沒有 correct
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"page": 1, "element_id": third["id"], "tags": ["text_wrong"]}).status_code == 201
    items = [{"field": "font_family", "value": "Carlito", "element_id": box["id"], "page": 1},
             {"field": "text", "value": title.text + "版", "element_id": title.id, "page": 1},
             {"field": "text", "value": par_text, "element_id": par.id, "page": 1}]
    r = client.post(f"/api/jobs/{job_id}/corrections", json=items)
    assert r.status_code == 202
    assert _wait(client, job_id)["status"] == "done"
    return {"box": box["id"], "other": other["id"], "third": third["id"], "title": title, "par": par.id, "par_lines": list(par.line_ids), "edited": edited, "changed": par.line_ids[j]}


def _export(client, job_id: str) -> bytes:
    r = client.get(f"/api/jobs/{job_id}/feedback/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "slide_basic.mortise-feedback.zip" in r.headers["content-disposition"]
    assert "private" in r.headers["cache-control"]  # 使用者的資料：共用快取不可存
    return r.content


def test_list_and_export_without_consent(client, tmp_path):
    from mortise.eval.feedback import FeedbackStore, build_calibration, read_bundle

    job_id = _job(client, consent=False)
    empty = client.get(f"/api/jobs/{job_id}/feedback").json()
    assert empty == {"items": [], "corrections": 0, "consented": False}
    ids = _give_feedback(client, job_id)

    listed = client.get(f"/api/jobs/{job_id}/feedback").json()
    assert listed["consented"] is False and listed["corrections"] == 3
    assert [i["rating"] for i in listed["items"]] == [4, None, None, None]
    assert all(i["id"] and i["created"] for i in listed["items"])
    assert listed["items"][0]["tags"] == ["font_wrong"] and listed["items"][1]["element_id"] == ids["box"]

    data = _export(client, job_id)  # 沒有同意也可以匯出：這是使用者自己的資料
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert {"README.txt", "report.json", "sample/slide_basic.pdf", "sample/slide_basic.feedback.json"} <= names
    assert z.read("sample/slide_basic.pdf") == FIX.read_bytes()
    assert "python -m mortise.eval" in z.read("README.txt").decode("utf-8")
    assert json.loads(z.read("report.json"))["overall_score"] is not None
    fbj = json.loads(z.read("sample/slide_basic.feedback.json"))
    assert fbj["schema"] == "mortise.feedback/1" and fbj["job"] == {"job_id": job_id, "filename": "slide_basic.pdf", "consented": False}
    assert fbj["exported_with"]["version"].startswith("mortise-")
    assert fbj["page_ratings"][0]["score"] == 4 and fbj["issues"][0]["tags"] == ["font_wrong"]
    flag = fbj["issues"][1]
    assert {k: flag[k] for k in ("page_index", "element_id", "tags", "comment", "correct")} == \
        {"page_index": 0, "element_id": ids["box"], "tags": ["position_wrong"], "comment": "", "correct": False}
    assert flag["system_value"] and 0.0 <= flag["system_confidence"] <= 1.0  # 被標錯的元素當時的字與⑦的元素信心值
    reported = fbj["issues"][2]  # 網頁的「回報錯誤」：沒有 correct，但有元素編號與標籤，也記下元素當時的信心值
    assert reported["element_id"] == ids["third"] and reported.get("correct") is None and 0.0 <= reported["system_confidence"] <= 1.0
    seen = fbj["confirmations"][0]  # 「這個元素是對的」：看過但沒改的資料，校準要用；記下當時的信心值
    assert seen["element_id"] == ids["other"] and seen["field"] == "element" and 0.0 <= seen["system_confidence"] <= 1.0
    cor = fbj["corrections"][0]
    assert cor["field"] == "font_family" and cor["new_value"] == "Carlito" and cor["source"] == "user"
    # 沒同意：沒有收進回饋庫，但工作目錄 feedback/corrections.jsonl 記著每筆修正當時系統的值，匯出時照樣有
    assert "snapshot_missing" not in fbj and all("system_value" in c for c in fbj["corrections"])
    assert 0.0 <= cor["system_confidence"] <= 1.0 and all(set(c) <= _correction_keys() for c in fbj["corrections"])
    assert not list((_pipe(job_id).dir.parent / "_feedback" / "samples").glob(f"{job_id}__*"))  # 沒同意：回饋庫裡沒有這份工作
    # 較早的工作（工作目錄沒有這份快照）：誠實標出來（放在頂層的 snapshot_missing，修正單裡不放新的鍵）
    snap_file = _pipe(job_id).dir / "feedback" / "corrections.jsonl"
    kept = snap_file.read_bytes()
    snap_file.unlink()
    old = json.loads(zipfile.ZipFile(io.BytesIO(_export(client, job_id))).read("sample/slide_basic.feedback.json"))
    snap_file.write_bytes(kept)
    assert set(old["snapshot_missing"]) == {c["id"] for c in old["corrections"]} and all(set(c) <= _correction_keys() for c in old["corrections"])

    path = tmp_path / "x.mortise-feedback.zip"
    path.write_bytes(data)
    b = read_bundle(path)
    assert b.sample_name == "slide_basic.pdf" and b.report is not None
    store = FeedbackStore(tmp_path / "store")
    fb = store.import_bundle(path, samples_dir=tmp_path / "samples")
    assert fb.sample == f"{job_id}__slide_basic.pdf" and store.samples() == [fb.sample]
    assert (tmp_path / "samples" / f"{job_id}__slide_basic.pdf").read_bytes() == FIX.read_bytes()
    rows = store.to_annotations()
    cor_row = next(r for r in rows if r["kind"] == "correction" and r["field"] == "font_family")
    assert cor_row["resolved"] is True and cor_row["system_confidence"] is not None and cor_row["correct_value"] == "Carlito"
    # 修正、確認、標錯都有系統當時的信心值，都進校準表（資料還太少：不夠建表）
    assert {r["kind"] for r in rows if r["system_confidence"] is not None} == {"correction", "confirmation", "flag"}
    pairs = [(r["system_confidence"], not r["corrected"]) for r in rows if r["system_confidence"] is not None]
    tab = build_calibration(pairs)
    assert tab.n_total == len(pairs) >= 4 and not tab.sufficient


@pytest.fixture(scope="module")
def consented(client):
    """一份勾了同意的工作，送過評分、標記與三種修正。"""
    job_id = _job(client, consent=True)
    return job_id, _give_feedback(client, job_id)


def test_export_with_consent_roundtrips_to_calibration(client, consented, tmp_path):
    from mortise.eval.__main__ import main
    from mortise.eval.feedback import FeedbackStore, calibration_from_annotations, read_annotations

    job_id, ids = consented
    data = _export(client, job_id)
    fbj = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("sample/slide_basic.feedback.json"))
    cor = fbj["corrections"][0]
    assert "snapshot_missing" not in fbj and cor["system_value"] and 0.0 <= cor["system_confidence"] <= 1.0  # 同意時有快照
    # ③文字行：系統當時的值是整行的字（不是第一個樣式段），信心值是③的文字信心值（原生 PDF 擷取的字是精確的：1.0）
    line_cor = next(c for c in fbj["corrections"] if c["element_id"] == ids["title"].id)
    assert line_cor["target_stage"] == 3 and line_cor["system_value"] == ids["title"].text and line_cor["system_confidence"] == 1.0
    par_cor = next(c for c in fbj["corrections"] if c["element_id"] == ids["par"])
    assert par_cor["target_stage"] == 5 and isinstance(par_cor["system_value"], str) and par_cor["system_confidence"] == 1.0
    zpath = tmp_path / "slide_basic.mortise-feedback.zip"
    zpath.write_bytes(data)

    # 用 eval 子指令讀回：匯入 → 標註 → 校準表（只有一筆：誠實回報資料不足，結束碼 3）
    store_dir, ann = tmp_path / "store", tmp_path / "ann.jsonl"
    assert main(["feedback-import", str(zpath), "--store", str(store_dir)]) == 0
    assert main(["annotations", "--store", str(store_dir), "--out", str(ann)]) == 0
    rows = read_annotations(ann)
    assert any(r["kind"] == "correction" and r["resolved"] and r["system_confidence"] is not None for r in rows)
    assert main(["calibrate", str(ann)]) == 3
    tab = calibration_from_annotations(rows)
    assert tab.n_total >= 1 and not tab.sufficient and "樣本不足" in tab.reason

    # 同一份工作同時在回饋庫（同意後自動收的）和回饋包裡：同一筆修正只算一次
    root = Path(os.environ["MORTISE_JOBS"]) / "_feedback" / "samples"
    store = FeedbackStore(store_dir)
    store.import_sample_dir(root)
    rows = [r for r in store.to_annotations() if r["sample"] == f"{job_id}__slide_basic.pdf"]
    assert len([r for r in rows if r["kind"] == "correction"]) == 3
    # 「這個元素是對的」在回饋庫（沒有信心值）和回饋包（有信心值）各一份：只算一次，用有信心值的那份
    seen = [r for r in rows if r["kind"] == "confirmation"]
    assert len(seen) == 1 and seen[0]["element_id"] == ids["other"] and seen[0]["system_confidence"] is not None
    assert sorted(r["element_id"] for r in rows if r["kind"] == "flag") == sorted([ids["box"], ids["third"]])


def test_web_paragraph_text_edit_becomes_ocr_training_data(consented, tmp_path):
    """網頁改字送的是⑤段落編號：ocr-dataset 依④的 line_ids 找回③的行，只改了其中一行時，每一行都切得出自己的字。"""
    from mortise.eval.ocr_dataset import build_dataset

    job_id, ids = consented
    root = Path(os.environ["MORTISE_JOBS"])
    res = build_dataset(tmp_path / "ds", feedback=[root / job_id], jobs_root=root)
    by_line: dict[str, list] = {}
    for it in res["items"]:
        if it["corrected_element"] == ids["par"]:
            by_line.setdefault(it["element_id"], []).append(it)
    edited = dict(zip(ids["par_lines"], ids["edited"]))
    short = {lid: t for lid, t in edited.items() if len(t) <= 25}
    assert set(short) <= set(by_line) and ids["changed"] in by_line
    assert all(len(by_line[lid]) == 1 and by_line[lid][0]["label"] == short[lid] and by_line[lid][0]["correction_stage"] == 5 for lid in short)
    # 超過 25 個字的行（訓練程式會丟掉）切成幾段：每段不超過 25 個字、是那一行的一部分、標成 segment_of_long_line
    for lid, segs in by_line.items():
        if lid in short:
            continue
        assert len(edited[lid]) > 25 and all(sg.get("segment_of_long_line") and len(sg["label"]) <= 25 and sg["label"] in edited[lid] for sg in segs)
    items = [it for segs in by_line.values() for it in segs]
    assert any(it["line_changed"] for it in by_line[ids["changed"]]) and sum(it["line_changed"] for it in items) == 1
    assert all(it["consented"] is True and (tmp_path / "ds" / it["image"]).exists() for it in res["items"])
    title = [it for it in res["items"] if it["element_id"] == ids["title"].id]
    # ③那一行已經套用了使用者的字（多了一個字，沒有它的位置）：切得成就每段不超過 25 字，切不成就寫明原因
    assert all(len(it["label"]) <= 25 for it in title)
    assert title or any(sk.get("element_id") == ids["title"].id and "切不成" in sk["reason"] for sk in res["skipped"])


# ====================================================================== 回饋格式的小測試（不需要伺服器）

def _correction_keys() -> set[str]:
    from mortise.artifacts import Correction

    return set(Correction.model_fields) | {"system_value", "system_confidence"}  # 舊版 parse_feedback 只認得這些


def test_calibration_refuses_selection_bias():
    """只有「被修正的」資料（全部都錯）時不建表：否則會把任何信心值都校準成 0。"""
    from mortise.eval.feedback import build_calibration

    only_fixed = [(0.5 + 0.5 * k / 59, False) for k in range(60)]
    tab = build_calibration(only_fixed)
    assert not tab.sufficient and "選擇偏差" in tab.reason and tab.calibrate(0.9) == 0.9
    mixed = only_fixed + [(0.5 + 0.5 * k / 59, True) for k in range(60)]
    assert build_calibration(mixed).sufficient


def test_untrusted_bundle_cannot_escape(tmp_path):
    """別人交來的回饋包：job_id、修正單 id、檔名拿來組路徑前都要檢查，不能寫到目標目錄外面。"""
    from mortise.eval.feedback import FeedbackStore, read_bundle

    evil = tmp_path / "evil.mortise-feedback.zip"
    fb = {"schema": "mortise.feedback/1", "sample": "../../x.pdf", "feedback_id": "../../fid",
          "job": {"job_id": "../../pwn_import", "filename": "../../slide_basic.pdf", "consented": True},
          "corrections": [{"id": "../x", "target_stage": 3, "page_index": 0, "element_id": "p001-t-0", "field": "text", "new_value": "A"}]}
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("sample/slide_basic.pdf", FIX.read_bytes())
        z.writestr("sample/slide_basic.feedback.json", json.dumps(fb))
        z.writestr("sample/../../outside.txt", "x")
    b = read_bundle(evil)
    assert "job_id" not in b.feedback.job and b.feedback.job["filename"] == "slide_basic.pdf"
    assert "/" not in b.feedback.corrections[0].id and ".." not in b.feedback.corrections[0].id and "/" not in b.feedback.feedback_id
    inner = tmp_path / "evil" / "samples" / "inner"
    store = FeedbackStore(tmp_path / "evil" / "store")
    store.import_bundle(evil, samples_dir=inner)
    written = [p for p in (tmp_path / "evil").rglob("*") if p.is_file()]
    assert written and all(inner in p.parents or (tmp_path / "evil" / "store") in p.parents for p in written)
    assert not (tmp_path / "pwn_import__slide_basic.pdf").exists() and not (tmp_path / "outside.txt").exists()
    # "job": null 也讀得懂
    with zipfile.ZipFile(tmp_path / "null.zip", "w") as z:
        z.writestr("sample/a.feedback.json", json.dumps({"schema": "mortise.feedback/1", "job": None}))
    assert read_bundle(tmp_path / "null.zip").feedback.job == {}


def test_new_feedback_file_reads_with_old_reader(tmp_path):
    """舊版程式（只拿掉修正單的 system_value 與 system_confidence，Correction 不接受多的鍵）也讀得懂新檔：
    沒記下系統值的修正單放在頂層 snapshot_missing，不在修正單裡加新鍵。"""
    from mortise.artifacts import Correction
    from mortise.eval.feedback import Feedback, parse_feedback

    c = Correction.model_validate({"id": "u-1", "target_stage": 5, "page_index": 0, "element_id": "p001-box-1", "field": "font_family",
                                   "new_value": "Carlito", "source": "user"})
    d = Feedback(sample="a.pdf", corrections=[c], snapshots={"u-1": {"system_value_missing": True}}).to_dict()
    assert d["snapshot_missing"] == ["u-1"]
    for raw in d["corrections"]:  # 舊版 parse_feedback 的做法
        raw = {k: v for k, v in raw.items() if k not in ("system_value", "system_confidence")}
        raw.setdefault("source", "user")
        Correction.model_validate(raw)
    assert parse_feedback(d).snapshots == {"u-1": {"system_value_missing": True}}
    old_style = dict(d, corrections=[dict(d["corrections"][0], system_value_missing=True)])  # 2026-11-23 以前的回饋包
    old_style.pop("snapshot_missing")
    assert parse_feedback(old_style).snapshots == {"u-1": {"system_value_missing": True}}


def test_engine_and_not_found(client):
    r = client.get("/api/engine")
    assert r.status_code == 200
    e = r.json()
    assert e["version"].startswith("mortise-") and "-cfg" in e["version"] and "-ocr" in e["version"]
    assert set(e["components"]["stages"]) == {"s1", "s2", "s3", "s4", "s5", "s6", "s7"}
    # 不透露伺服器上的路徑與其他軟體的版本
    assert "verify_engine" not in e["components"] and all("path" not in f for f in e["components"]["ocr"]["files"].values())
    assert e["name"] and (e["scorecard"] is None or e["scorecard"]["headline"])
    assert client.get("/api/jobs/20260101-000000000000/feedback").status_code == 404
    assert client.get("/api/jobs/20260101-000000000000/feedback/export").status_code == 404
