"""頁面幾何 API（照片的四角確認）：合成照片上傳 → geometry 回 needs_review 與候選 → 送四角修正 → 重跑後 status "user"。

合成照片：一張紙的下緣超出畫面（不放任何真實照片）。UVDoc 權重不下載（MORTISE_OFFLINE=1）。
"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_photo_pipeline import CUT_GT, cut_off_photo  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    saved = {k: os.environ.get(k) for k in ("MORTISE_JOBS", "MORTISE_FEEDBACK", "MORTISE_OFFLINE")}
    root = tmp_path_factory.mktemp("geo-jobs")
    os.environ.update({"MORTISE_JOBS": str(root), "MORTISE_FEEDBACK": str(root / "_feedback"), "MORTISE_OFFLINE": "1"})
    from fastapi.testclient import TestClient

    import mortise.api.app as appmod

    importlib.reload(appmod)
    yield TestClient(appmod.app), root, appmod
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _wait(client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/jobs/{job_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.5)
    raise AssertionError("工作逾時")


def _jpeg(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(scope="module")
def photo_job(client):
    c, _, _ = client
    r = c.post("/api/jobs", files={"file": ("cut.jpg", _jpeg(cut_off_photo()), "image/jpeg")}, data={"outputs": "pdf", "allow_training": "true"})
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    st = _wait(c, job_id)
    assert st["status"] == "done", st
    return job_id


def test_pages_list_review_flag_and_geometry_url(client, photo_job):
    c, _, _ = client
    pages = c.get(f"/api/jobs/{photo_job}/pages").json()
    p = pages[0]
    assert p["input_type"] == "photo"
    assert p["needs_review"] is True and p["quad_status"] == "cut_off"
    assert p["geometry"] == f"/api/jobs/{photo_job}/pages/1/geometry"
    assert p["images"]["source"].endswith("kind=source")


def test_geometry_asks_and_offers_candidates(client, photo_job):
    c, _, _ = client
    g = c.get(f"/api/jobs/{photo_job}/pages/1/geometry").json()
    assert g["page"] == 1 and g["input_type"] == "photo"
    assert g["source"] == {"width": 1000, "height": 1100, "image": f"/api/jobs/{photo_job}/pages/1/image?kind=source"}
    assert g["status"] == "cut_off" and g["needs_review"] is True and g["review_reason"]
    assert g["user_set"] is False
    assert len(g["corners"]) == 4 and all(len(pt) == 2 for pt in g["corners"])
    assert g["alternatives"] and g["alternatives"][0]["label"] == "三條邊＋畫面邊"
    assert 0.0 <= g["alternatives"][0]["score"] <= 1.0 and len(g["alternatives"][0]["corners"]) == 4
    assert g["preselect"] == 0 and np.abs(np.array(g["alternatives"][0]["corners"]) - np.array(g["corners"])).max() < 0.05
    assert set(g["dewarp"]) == {"applied", "source", "residual_px", "needs_review"}
    assert g["corner_error_px"] is None or len(g["corner_error_px"]) == 4
    assert isinstance(g["notes"], list) and g["method"] == "frame_edges"
    assert "glare" in g and "outline" in g


def test_source_image_is_unflattened_photo(client, photo_job):
    c, _, _ = client
    r = c.get(f"/api/jobs/{photo_job}/pages/1/image", params={"kind": "source", "width": 500})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(r.content))
    assert im.size == (500, 550)  # 原圖 1000×1100（不是攤平後的工作影像）
    r2 = c.get(f"/api/jobs/{photo_job}/pages/1/image", params={"kind": "source", "width": 500}, headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    assert c.get(f"/api/jobs/{photo_job}/pages/2/image", params={"kind": "source"}).status_code == 404


def test_user_corners_rerun_and_feedback_snapshot(client, photo_job):
    c, root, _ = client
    r = c.post(f"/api/jobs/{photo_job}/corrections", json=[{"field": "corners", "page": 1, "value": CUT_GT.tolist()}])
    assert r.status_code == 202, r.text
    g = c.get(f"/api/jobs/{photo_job}/pages/1/geometry")
    assert g.status_code == 409 or g.json()["status"] == "user"  # 重跑中：不給舊的（還沒依使用者四角處理的）結果
    st = _wait(c, photo_job)
    assert st["status"] == "done", st
    g = c.get(f"/api/jobs/{photo_job}/pages/1/geometry").json()
    assert g["status"] == "user" and g["user_set"] is True and g["needs_review"] is False and g["review_reason"] == ""
    assert g["method"] == "user" and g["confidence"] == 1.0 and g["preselect"] is None
    assert np.abs(np.array(g["corners"]) - CUT_GT).max() < 0.05
    p = c.get(f"/api/jobs/{photo_job}/pages").json()[0]
    assert p["needs_review"] is False and p["quad_status"] == "user"
    # 同意用於改善辨識：四角修正連同系統當時的四角、status、信心值、各角誤差收進回饋庫（校準用）
    files = list((root / "_feedback" / "samples").glob("*.feedback.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    cor = [x for x in data["corrections"] if x["field"] == "corners"]
    assert len(cor) == 1
    sv = cor[0]["system_value"]
    assert sv["status"] == "cut_off" and sv["method"] == "frame_edges" and len(sv["corners"]) == 4
    assert "corner_error_px" in sv and sv["needs_review"] is True and sv["source_size"] == [1000, 1100]
    assert cor[0]["system_confidence"] == pytest.approx(sv["confidence"])
    assert len(sv["shift_px"]) == 4 and sv["within_tolerance"] is False  # 下面兩個角使用者拉到畫面外，差很多
    # 回饋庫的檔案要能被評測工具讀回
    from mortise.eval.feedback import load_feedback

    fb = load_feedback(files[0])
    assert any(x.field == "corners" for x in fb.corrections)


def test_pdf_page_is_not_photo(client):
    c, _, _ = client
    with (FIX / "slide_basic.pdf").open("rb") as f:
        r = c.post("/api/jobs", files={"file": ("slide_basic.pdf", f, "application/pdf")}, data={"outputs": "pdf"})
    job_id = r.json()["job_id"]
    assert _wait(c, job_id)["status"] == "done"
    g = c.get(f"/api/jobs/{job_id}/pages/1/geometry").json()
    assert g["status"] == "not_photo" and g["needs_review"] is False and g["user_set"] is False
    assert g["alternatives"] == [] and g["preselect"] is None and g["dewarp"]["applied"] is False
    p = c.get(f"/api/jobs/{job_id}/pages").json()[0]
    assert p["needs_review"] is False and p["quad_status"] is None
    r = c.get(f"/api/jobs/{job_id}/pages/1/image", params={"kind": "source", "width": 300})
    assert r.status_code == 200  # 原生 PDF：source 與 original 相同
    assert c.get(f"/api/jobs/{job_id}/pages/9/geometry").status_code == 404


def test_geometry_409_before_page_stage(client):
    """還沒處理到②（這裡直接登記一份工作、不執行）：geometry 回 409；原圖（kind=source）已經可以看。"""
    c, root, appmod = client
    from mortise.artifacts import Intent
    from mortise.pipeline import Pipeline

    job_id = "20990101-0000aaaa0000"
    src = root / "_tmp_cut.jpg"
    src.write_bytes(_jpeg(cut_off_photo()))
    pipe = Pipeline(root / job_id, cfg=appmod.CFG)
    pipe.start(src, "cut.jpg", Intent())
    assert c.get(f"/api/jobs/{job_id}/pages/1/geometry").status_code == 409
    r = c.get(f"/api/jobs/{job_id}/pages/1/image", params={"kind": "source", "width": 200})
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (200, 220)
    assert c.get(f"/api/jobs/{job_id}/pages/1/image", params={"kind": "original"}).status_code == 409
