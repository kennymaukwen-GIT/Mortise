"""API 的端到端測試：上傳 → 等待 → 預覽 → 修正 → 重跑 → 下載 → 回饋 → 刪除。"""

import importlib
import time
from pathlib import Path

import pytest

FIX = Path(__file__).parents[1] / "fixtures" / "slide_basic.pdf"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os

    os.environ["MORTISE_JOBS"] = str(tmp_path_factory.mktemp("jobs"))
    from fastapi.testclient import TestClient

    import mortise.api.app as appmod

    importlib.reload(appmod)
    return TestClient(appmod.app)


def _wait(client, job_id, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.5)
    raise AssertionError("工作逾時")


@pytest.fixture(scope="module")
def job(client):
    with FIX.open("rb") as f:
        r = client.post("/api/jobs", files={"file": ("slide_basic.pdf", f, "application/pdf")}, data={"outputs": "pptx"})
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    st = _wait(client, job_id)
    assert st["status"] == "done", st
    return job_id


def test_health_and_formats(client):
    assert client.get("/api/health").json()["ok"]
    fm = {f["format"]: f for f in client.get("/api/formats").json()}
    assert fm["pptx"]["available"]


def test_rejects_unknown_file(client):
    r = client.post("/api/jobs", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 201  # 建立工作成功，但處理會失敗並說明原因
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "failed" and "不支援" in st["error"]


def test_pages_previews_and_elements(client, job):
    pages = client.get(f"/api/jobs/{job}/pages").json()
    assert len(pages) == 1 and pages[0]["width_pt"] == 960.0
    for kind in ("original", "result", "overlay"):
        r = client.get(f"/api/jobs/{job}/pages/1/image", params={"kind": kind, "width": 400})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", kind
    el = client.get(f"/api/jobs/{job}/pages/1/elements").json()
    assert len(el["text_boxes"]) == 3
    title = el["text_boxes"][0]["paragraphs"][0]
    assert title["runs"][0]["size_pt"] == 40.0 and title["role"] == "title"


def test_correction_reruns_and_download(client, job):
    el = client.get(f"/api/jobs/{job}/pages/1/elements").json()
    par = el["text_boxes"][0]["paragraphs"][0]
    r = client.post(f"/api/jobs/{job}/corrections", json=[{"field": "text", "value": "Mortise 年度報告", "element_id": par["id"], "page": 1}])
    assert r.status_code == 202
    st = _wait(client, job)
    assert st["status"] == "done" and st["corrections"] == 1
    el2 = client.get(f"/api/jobs/{job}/pages/1/elements").json()
    assert el2["text_boxes"][0]["paragraphs"][0]["text"] == "Mortise 年度報告"
    rep = client.get(f"/api/jobs/{job}/report").json()
    assert rep["pages"][0]["elements"][0]["text_match"] is True  # 驗證改以使用者的文字為準
    dl = client.get(f"/api/jobs/{job}/download/pptx")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    assert client.get(f"/api/jobs/{job}/download/report").status_code == 200


def test_feedback_and_delete(client, job):
    r = client.post(f"/api/jobs/{job}/feedback", json={"rating": 4, "comment": "字型對了，標題位置稍偏", "tags": ["layout_broken"]})
    assert r.status_code == 201
    assert client.delete(f"/api/jobs/{job}").status_code == 204
    assert client.get(f"/api/jobs/{job}").status_code == 404


def test_consented_feedback_flows_into_dataset(client):
    import json
    import os

    with FIX.open("rb") as f:
        r = client.post("/api/jobs", files={"file": ("slide_basic.pdf", f, "application/pdf")}, data={"outputs": "pptx", "allow_training": "true"})
    job_id = r.json()["job_id"]
    assert _wait(client, job_id)["status"] == "done"
    el = client.get(f"/api/jobs/{job_id}/pages/1/elements").json()
    box = el["text_boxes"][0]
    client.post(f"/api/jobs/{job_id}/corrections", json=[{"field": "font_family", "value": "Carlito", "element_id": box["id"], "page": 1}])
    _wait(client, job_id)
    client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 5, "comment": "很好", "page": 1})
    root = os.environ["MORTISE_JOBS"] + "/_feedback/samples"
    names = os.listdir(root)
    assert any(n.endswith("slide_basic.pdf") for n in names)
    fb = json.loads(open(os.path.join(root, next(n for n in names if n.endswith(".feedback.json"))), encoding="utf-8").read())
    assert fb["schema"] == "mortise.feedback/1" and fb["corrections"][0]["field"] == "font_family"
    assert fb["page_ratings"][0]["score"] == 5
    from mortise.eval.feedback import load_feedback

    parsed = load_feedback(os.path.join(root, next(n for n in names if n.endswith(".feedback.json"))))
    assert parsed.corrections and parsed.corrections[0].new_value == "Carlito"
