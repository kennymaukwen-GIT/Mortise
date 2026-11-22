"""前端約定的 API：health 的新欄位、label_en、輸出尺寸清單、上傳時指定輸出尺寸、各頁的 placement、
依輸出格式畫的預覽圖（含快取與失效）、進度串流的結尾、前端靜態檔案與單頁應用的後備（/api 永遠不被攔走）。"""

from __future__ import annotations

import importlib
import io
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image

FIX = Path(__file__).parents[1] / "fixtures" / "slide_basic.pdf"
A3 = {"target_width": "297", "target_height": "420", "target_unit": "mm"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """有前端建置結果（假的 web/dist）的 API。"""
    from fastapi.testclient import TestClient

    dist = tmp_path_factory.mktemp("dist")
    (dist / "index.html").write_text("<!doctype html><title>Mortise UI</title><div id=root></div>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "index-3f9a1c.js").write_text("console.log('mortise')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
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
        time.sleep(0.5)
    raise AssertionError("工作逾時")


def _post(client, data):
    with FIX.open("rb") as f:
        return client.post("/api/jobs", files={"file": ("slide_basic.pdf", f, "application/pdf")}, data=data)


@pytest.fixture(scope="module")
def sized_job(client):
    """指定 A3 直式輸出尺寸的工作：輸出 pdf、svg、pptx、docx_fixed（docx_fixed 沒有自己的渲染，預覽退回中性渲染）。"""
    r = _post(client, {"outputs": "pdf,svg,pptx,docx_fixed", **A3})
    assert r.status_code == 201, r.text
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "done", st
    return st["job_id"]


@pytest.fixture(scope="module")
def plain_job(client):
    r = _post(client, {"outputs": "pdf"})
    assert r.status_code == 201, r.text
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "done", st
    return st["job_id"]


# ---------------------------------------------------------------- 基本資訊


def test_health_has_limits_and_accepted_types(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["max_file_mb"] == 100
    assert h["accepted"] == ["application/pdf", "image/png", "image/jpeg"]


def test_formats_have_english_labels(client):
    fm = {f["format"]: f for f in client.get("/api/formats").json()}
    want = {
        "pdf": "Editable PDF (.pdf)", "svg": "SVG (.svg)", "svg_assets": "SVG graphics only (.zip)", "pptx": "PowerPoint (.pptx)",
        "docx_fixed": "Word, fixed layout (.docx)", "docx_flow": "Word, reflowable (.docx)", "xlsx": "Excel (.xlsx)",
        "ai_pdf": "PDF for Illustrator (.pdf)", "idml": "InDesign (.idml)", "google_slides": "Google Slides (.pptx)", "template": "Layout template (.pptx)",
    }
    assert {k: v["label_en"] for k, v in fm.items()} == want
    assert all(v["label"] and isinstance(v["available"], bool) for v in fm.values())


def test_page_sizes_portrait_in_order(client):
    sizes = client.get("/api/page-sizes").json()
    assert [s["id"] for s in sizes] == ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5", "Letter", "Legal", "Tabloid", "Slide 16:9", "Slide 4:3"]
    by = {s["id"]: s for s in sizes}
    assert (by["A3"]["width_mm"], by["A3"]["height_mm"]) == (297.0, 420.0)
    assert (by["Slide 16:9"]["width_mm"], by["Slide 16:9"]["height_mm"]) == (338.67, 190.5)
    assert (by["Slide 4:3"]["width_mm"], by["Slide 4:3"]["height_mm"]) == (254.0, 190.5)
    assert all(s["width_mm"] <= s["height_mm"] for s in sizes if not s["id"].startswith("Slide"))  # 一律直式（簡報例外）
    assert all(set(s) == {"id", "label", "width_mm", "height_mm"} for s in sizes)


# ---------------------------------------------------------------- 上傳時指定輸出尺寸


@pytest.mark.parametrize("data", [{"target_width": "297"}, {"target_height": "420"}, {"target_height": "420", "target_unit": "mm"}])
def test_target_size_needs_both_sides(client, data):
    """只填寬或只填高：回 422（不默默忽略，免得使用者以為指定了尺寸）。"""
    r = _post(client, {"outputs": "pdf", **data})
    assert r.status_code == 422 and "寬與高" in r.json()["detail"]


@pytest.mark.parametrize("data", [{"target_width": "0", "target_height": "420"}, {"target_width": "297", "target_height": "99999"}, {"target_width": "297", "target_height": "420", "target_unit": "furlong"}])
def test_target_size_rejects_bad_values(client, data):
    assert _post(client, {"outputs": "pdf", **data}).status_code == 422


def test_target_size_is_recorded_and_applied(client, sized_job, plain_job):
    st = client.get(f"/api/jobs/{sized_job}").json()
    assert st["intent"]["target_size"] == {"width": 297.0, "height": 420.0, "unit": "mm"}
    page = client.get(f"/api/jobs/{sized_job}/pages").json()[0]
    assert page["width_pt"] == pytest.approx(841.89, abs=0.01) and page["height_pt"] == pytest.approx(1190.55, abs=0.01)
    pl = page["placement"]
    assert pl["x"] == pytest.approx(0.0) and pl["w"] == pytest.approx(841.89, abs=0.01) and pl["y"] > 300  # 16:9 放進 A3 直式：上下留白
    assert page["source_width_pt"] == pytest.approx(960.0, abs=0.1)
    assert set(page["result_images"]) == {"pdf", "svg", "pptx", "docx_fixed"}
    plain = client.get(f"/api/jobs/{plain_job}/pages").json()[0]
    assert plain["placement"] is None and plain["width_pt"] == 960.0
    assert client.get(f"/api/jobs/{plain_job}").json()["intent"]["target_size"] is None


# ---------------------------------------------------------------- 預覽圖


def _img(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(r.content))


def test_result_image_per_format(client, sized_job):
    from mortise.tools import libreoffice

    base = f"/api/jobs/{sized_job}/pages/1/image"
    engines = {}
    for fmt in ("pdf", "svg", "pptx", "docx_fixed"):
        r = client.get(base, params={"kind": "result", "format": fmt, "width": 400})
        im = _img(r)
        assert im.size[0] == 400 and im.size[1] == round(400 * 420 / 297)  # 整張輸出頁面（含留白）
        engines[fmt] = r.headers["x-mortise-render"]
        # 留白處（最上方）是白色，原始內容在中間
        top = im.convert("L").crop((0, 0, 400, 40))
        assert top.getextrema()[0] > 240, fmt
    assert engines["pdf"] == "pdf"
    assert engines["svg"] in ("svg-resvg", "svg-verify")
    assert engines["docx_fixed"] == "neutral"
    assert engines["pptx"] == ("pptx-libreoffice" if libreoffice.available() else "neutral")
    # format 省略：用這份工作的第一個輸出格式（pdf）
    r = client.get(base, params={"kind": "result", "width": 400})
    assert r.headers["x-mortise-render"] == "pdf"
    # original 仍是原圖（16:9）；overlay 以輸出頁面為底
    orig = _img(client.get(base, params={"kind": "original", "width": 400}))
    assert orig.size == (400, round(400 * 540 / 960))
    over = _img(client.get(base, params={"kind": "overlay", "format": "svg", "width": 400}))
    assert over.size == (400, round(400 * 420 / 297))


def test_result_image_rejects_bad_format(client, sized_job, plain_job):
    base = f"/api/jobs/{plain_job}/pages/1/image"
    assert client.get(base, params={"kind": "result", "format": "bogus"}).status_code == 422
    assert client.get(base, params={"kind": "result", "format": "svg"}).status_code == 404  # 這份工作沒有輸出 svg
    assert client.get(f"/api/jobs/{sized_job}/pages/9/image", params={"kind": "result"}).status_code == 404


def test_result_image_without_target_matches_original(client, plain_job):
    base = f"/api/jobs/{plain_job}/pages/1/image"
    res = _img(client.get(base, params={"kind": "result", "format": "pdf", "width": 320}))
    orig = _img(client.get(base, params={"kind": "original", "width": 320}))
    assert res.size == orig.size == (320, 180)


def test_preview_cache_and_invalidation(client, sized_job):
    from mortise.pipeline import Pipeline

    base = f"/api/jobs/{sized_job}/pages/1/image"
    params = {"kind": "result", "format": "svg", "width": 600}  # 前端使用的級距之一（只有這些寬度寫進磁碟快取）
    r1 = client.get(base, params=params)
    folder = Path(os.environ["MORTISE_JOBS"]) / sized_job / "previews"
    cached = sorted(p.name for p in folder.glob("result-svg-p1-w600-*.png"))
    assert len(cached) == 1
    odd = client.get(base, params={**params, "width": 333})  # 不常用的寬度照畫，但不存檔（免得任意寬度塞滿磁碟）
    assert odd.status_code == 200 and not list(folder.glob("result-svg-p1-w333-*"))
    t0 = time.time()
    r2 = client.get(base, params=params)
    assert r2.content == r1.content and r2.headers["etag"] == r1.headers["etag"]
    assert time.time() - t0 < 2.0  # 快取：不重畫
    assert client.get(base, params=params, headers={"If-None-Match": r1.headers["etag"]}).status_code == 304
    # 修正後重跑：產物內容改變，快取跟著失效（ETag 改變、舊檔被換掉）
    el = client.get(f"/api/jobs/{sized_job}/pages/1/elements").json()
    par = el["text_boxes"][0]["paragraphs"][0]
    r = client.post(f"/api/jobs/{sized_job}/corrections", json=[{"field": "text", "value": "Mortise 年度報告 Annual", "element_id": par["id"], "page": 1}])
    assert r.status_code == 202
    assert _wait(client, sized_job)["status"] == "done"
    r3 = client.get(base, params=params)
    assert r3.status_code == 200 and r3.headers["etag"] != r1.headers["etag"]
    now = sorted(p.name for p in folder.glob("result-svg-p1-w600-*.png"))
    assert len(now) == 1 and now != cached
    pipe = Pipeline(Path(os.environ["MORTISE_JOBS"]) / sized_job)
    assert pipe.store.has("s7/fidelity_report")


def test_events_stream_ends_with_final(client, plain_job):
    with client.stream("GET", f"/api/jobs/{plain_job}/events") as r:
        msgs = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert msgs[-1]["final"] is True and msgs[-1]["status"] == "done" and msgs[-1]["progress"] == 1.0
    stages = [m for m in msgs if "stage" in m]
    assert all(1 <= m["stage"] <= 7 and m["stage_name"] and 0.0 <= m["progress"] <= 1.0 for m in stages)


# ---------------------------------------------------------------- 前端靜態檔案


def test_frontend_index_and_spa_fallback(client):
    r = client.get("/")
    assert r.status_code == 200 and "Mortise UI" in r.text and r.headers["cache-control"] == "no-cache"
    r = client.get("/jobs/20261122-abc/page/3")  # 前端自己的路由
    assert r.status_code == 200 and "Mortise UI" in r.text and r.headers["cache-control"] == "no-cache"
    r = client.get("/assets/index-3f9a1c.js")
    assert r.status_code == 200 and "mortise" in r.text and "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and "max-age=3600" in r.headers["cache-control"]
    assert client.get("/assets/missing-000.js").status_code == 404  # 找不到的程式碼檔不回 HTML


def test_api_paths_never_go_to_frontend(client):
    for method, path in (("GET", "/api/nope"), ("GET", "/api"), ("POST", "/api/jobs/x/whatever"), ("GET", "/api/jobs/nonexistent-1"), ("DELETE", "/api/nope")):
        r = client.request(method, path)
        assert r.status_code in (404, 405), (method, path, r.status_code)
        assert "Mortise UI" not in r.text and r.headers["content-type"].startswith("application/json"), (method, path)
    assert client.get("/api/health").json()["ok"]
    assert client.get("/docs").status_code == 200


def test_dev_page_moved(client):
    r = client.get("/dev")
    assert r.status_code == 200 and "開發測試頁" in r.text


def test_build_help_when_not_built(tmp_path):
    """沒有 web/dist/index.html 時，首頁顯示建置說明；其他路徑 404；/api 照常。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import mortise.api.app as appmod

    mini = FastAPI()

    @mini.get("/api/ping")
    def ping():
        return {"ok": True}

    mini.mount("/", appmod._Frontend(directory=str(tmp_path / "missing"), check_dir=False), name="web")
    c = TestClient(mini)
    r = c.get("/")
    assert r.status_code == 200 and "npm run build" in r.text
    assert c.get("/some/route").status_code == 404
    assert c.get("/api/ping").json() == {"ok": True}
    assert c.get("/api/other").status_code == 404
    # 建置完不必重新啟動：目錄出現後立刻生效
    (tmp_path / "missing").mkdir()
    (tmp_path / "missing" / "index.html").write_text("<title>built</title>", encoding="utf-8")
    assert "built" in c.get("/").text and "built" in c.get("/some/route").text
