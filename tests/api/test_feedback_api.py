"""回饋 API：送評分與修正 → 列出回饋 → 匯出回饋包（zip）→ 用 FeedbackStore.import_bundle 與 eval 子指令讀回、
轉成標註、建校準表；GET /api/engine。用 tests/fixtures 的虛構投影片。"""

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


def _give_feedback(client, job_id: str) -> dict:
    """評分＋標籤、標一個元素是錯的、改一個文字框的字型（⑤）與一行字（③）。回傳元素資訊。"""
    el = client.get(f"/api/jobs/{job_id}/pages/1/elements").json()
    box = el["text_boxes"][0]
    line_id = box["paragraphs"][0]["line_ids"][0] if box["paragraphs"][0].get("line_ids") else None
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 4, "page": 1, "comment": "標題字型不對", "tags": ["font_wrong"]}).status_code == 201
    assert client.post(f"/api/jobs/{job_id}/feedback", json={"page": 1, "element_id": box["id"], "correct": False, "tags": ["position_wrong"]}).status_code == 201
    items = [{"field": "font_family", "value": "Carlito", "element_id": box["id"], "page": 1}]
    r = client.post(f"/api/jobs/{job_id}/corrections", json=items)
    assert r.status_code == 202
    assert _wait(client, job_id)["status"] == "done"
    return {"box": box["id"], "line": line_id}


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
    assert listed["consented"] is False and listed["corrections"] == 1
    assert [i["rating"] for i in listed["items"]] == [4, None]
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
    assert fbj["issues"][1] == {"page_index": 0, "element_id": ids["box"], "tags": ["position_wrong"], "comment": "", "correct": False}
    cor = fbj["corrections"][0]
    assert cor["field"] == "font_family" and cor["new_value"] == "Carlito" and cor["source"] == "user"
    assert cor.get("system_value_missing") is True  # 沒同意：系統當時的值沒有收進回饋庫，誠實標出來

    path = tmp_path / "x.mortise-feedback.zip"
    path.write_bytes(data)
    b = read_bundle(path)
    assert b.sample_name == "slide_basic.pdf" and b.report is not None
    store = FeedbackStore(tmp_path / "store")
    fb = store.import_bundle(path, samples_dir=tmp_path / "samples")
    assert fb.sample == f"{job_id}__slide_basic.pdf" and store.samples() == [fb.sample]
    assert (tmp_path / "samples" / f"{job_id}__slide_basic.pdf").read_bytes() == FIX.read_bytes()
    rows = store.to_annotations()
    cor_row = next(r for r in rows if r["kind"] == "correction")
    assert cor_row["resolved"] is False and cor_row["system_confidence"] is None and cor_row["correct_value"] == "Carlito"
    # 沒有系統信心值的修正不進校準表
    tab = build_calibration([(r["system_confidence"], not r["corrected"]) for r in rows if r["system_confidence"] is not None])
    assert tab.n_total == 0 and not tab.sufficient


def test_export_with_consent_roundtrips_to_calibration(client, tmp_path):
    from mortise.eval.__main__ import main
    from mortise.eval.feedback import FeedbackStore, calibration_from_annotations, read_annotations

    job_id = _job(client, consent=True)
    _give_feedback(client, job_id)
    data = _export(client, job_id)
    fbj = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("sample/slide_basic.feedback.json"))
    cor = fbj["corrections"][0]
    assert "system_value_missing" not in cor and cor["system_value"] and 0.0 <= cor["system_confidence"] <= 1.0  # 同意時有快照
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
    both = [r for r in store.to_annotations() if r["kind"] == "correction" and r["sample"] == f"{job_id}__slide_basic.pdf"]
    assert len(both) == 1


def test_engine_and_not_found(client):
    r = client.get("/api/engine")
    assert r.status_code == 200
    e = r.json()
    assert e["version"].startswith("mortise-") and "-cfg" in e["version"] and "-ocr" in e["version"]
    assert set(e["components"]["stages"]) == {"s1", "s2", "s3", "s4", "s5", "s6", "s7"}
    assert e["name"] and (e["scorecard"] is None or e["scorecard"]["headline"])
    assert client.get("/api/jobs/20260101-000000000000/feedback").status_code == 404
    assert client.get("/api/jobs/20260101-000000000000/feedback/export").status_code == 404
