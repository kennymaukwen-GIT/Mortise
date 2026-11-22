"""審查發現的修正：中文檔名的報告下載、原始尺寸的範圍檢查、方法不對回 405、使用者資料的快取標頭與前端的安全標頭、
執行中刪除工作（標記後中止、停下來立刻刪檔）、逐頁進度不倒退、驗證渲染的像素上限。"""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

FIX = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    dist = tmp_path_factory.mktemp("dist")
    (dist / "index.html").write_text("<!doctype html><title>Mortise UI</title><div id=root></div>", encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("MORTISE_JOBS", "MORTISE_WEB_DIST")}
    os.environ["MORTISE_JOBS"] = str(tmp_path_factory.mktemp("jobs"))
    os.environ["MORTISE_WEB_DIST"] = str(dist)
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


def _post(client, name: str, upload_name: str | None = None, **data):
    with (FIX / name).open("rb") as f:
        return client.post("/api/jobs", files={"file": (upload_name or name, f, "application/pdf")}, data={"outputs": "pdf", **data})


def test_report_download_with_chinese_filename(client):
    r = _post(client, "slide_basic.pdf", upload_name='季度報告 "final".pdf')
    assert r.status_code == 201, r.text
    job = r.json()["job_id"]
    assert _wait(client, job)["status"] == "done"
    rep = client.get(f"/api/jobs/{job}/download/report")
    assert rep.status_code == 200
    cd = rep.headers["content-disposition"]
    assert "filename*=UTF-8''%E5%AD%A3%E5%BA%A6%E5%A0%B1%E5%91%8A" in cd and cd.startswith("attachment;")
    assert json.loads(rep.content)["overall_score"] is not None
    assert rep.headers["cache-control"].startswith("private")


@pytest.mark.parametrize(
    "data",
    [
        {"width": "100000", "height": "50000"},  # 曾讓驗證時的渲染要求 348 GB 而使整個服務崩潰
        {"width": "3000", "height": "1690", "unit": "cm"},
        {"width": "0.01", "height": "0.01"},  # 曾讓流水線除以零
        {"width": "nan", "height": "50"},
        {"width": "-100", "height": "10"},
        {"width": "100"},  # 只填一邊
        {"target_width": "6000", "target_height": "6000"},
    ],
)
def test_size_ranges_rejected(client, data):
    r = _post(client, "slide_basic.pdf", **data)
    assert r.status_code == 422, (data, r.status_code, r.text)
    assert "mm" in r.json()["detail"] or "一起填" in r.json()["detail"] or "大於 0" in r.json()["detail"]


def test_wrong_method_is_405_not_404(client):
    r = client.put("/api/jobs/20261122-aaaaaaaaaaaa")
    assert r.status_code == 405 and "GET" in r.headers["allow"] and "DELETE" in r.headers["allow"]
    assert client.get("/api/jobs").status_code == 405
    assert client.post("/api/health").status_code == 405
    assert client.get("/api/nope").status_code == 404


def test_privacy_and_security_headers(client):
    r = client.get("/api/jobs/20261122-aaaaaaaaaaaa")
    assert r.status_code == 404 and r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    home = client.get("/")
    csp = home.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "https:" not in csp
    assert home.headers["cache-control"] == "no-cache"


def test_delete_while_running_stops_and_removes(client):
    r = _post(client, "deck.pdf", outputs="pptx")
    job = r.json()["job_id"]
    folder = Path(os.environ["MORTISE_JOBS"]) / job
    assert client.delete(f"/api/jobs/{job}").status_code == 204
    assert client.get(f"/api/jobs/{job}").status_code == 404  # 對外立刻當作不存在
    assert client.get(f"/api/jobs/{job}/pages").status_code == 404
    t0 = time.time()
    while folder.exists() and time.time() - t0 < 300:
        time.sleep(0.3)
    assert not folder.exists(), "流水線停下來後檔案要刪掉"


def test_progress_is_page_by_page_and_never_goes_back(client):
    r = _post(client, "deck.pdf")
    job = r.json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job}/events") as s:
        msgs = [json.loads(line[5:]) for line in s.iter_lines() if line.startswith("data:")]
    prog = [m["progress"] for m in msgs if "stage" in m]
    assert prog == sorted(prog), prog
    assert {m["page"] for m in msgs if m.get("stage") == 3} == {0, 1, 2}
    st = client.get(f"/api/jobs/{job}").json()
    assert st["status"] == "done" and st["pages"] == 3


def test_interrupted_job_has_a_message(client):
    """伺服器在處理途中重新啟動：工作記錄停在 running，狀態要是 failed 並帶說明（不是空的 error）。"""
    import mortise.api.app as appmod

    r = _post(client, "slide_basic.pdf")
    job = r.json()["job_id"]
    _wait(client, job)
    pipe = appmod.JOBS.pipeline(job)
    pipe.state["status"] = "running"
    pipe._save_state()
    appmod.JOBS.progress.pop(job, None)
    st = client.get(f"/api/jobs/{job}").json()
    assert st["status"] == "failed" and st["error"] and "中斷" in st["error"]


def test_verify_dpi_caps_page_pixels():
    from mortise.stages.s7_verify import MAX_RENDER_PIXELS, verify_dpi

    big = SimpleNamespace(width_pt=283464.0, height_pt=141732.0, placement=None)  # 100 × 50 公尺
    d = verify_dpi(100.0, big, None)
    assert (big.width_pt * d / 72) * (big.height_pt * d / 72) <= MAX_RENDER_PIXELS * 1.0001
    a4 = SimpleNamespace(width_pt=595.0, height_pt=842.0, placement=None)
    assert verify_dpi(100.0, a4, None) == 100.0  # 一般頁面不受影響
