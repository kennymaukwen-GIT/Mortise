"""紙張攤平（dewarp）：平的紙不動、彎的紙由量測攤平、UVDoc 的下載與 ONNX 組裝、rectify 與 set_dewarper 的介面、enhance。

合成照片用 mortise.eval.synth.degrade_curved（逐像素真值），位置誤差用 metrics.dewarp_geometry。
四角用合成時的真值（不依賴 find_page_quad 的版本）；UVDoc 的測試只在使用者快取裡已有模型時跑（不在測試裡下載）。"""

import io
import pickle
import zipfile

import cv2
import numpy as np
import pytest

from mortise.eval import metrics as M
from mortise.eval import synth as S
from mortise.recognition import dewarp as D
from mortise.recognition import page_geometry as PG
from mortise.recognition.types import PageQuad

SYN = {"curved_out_long": 1000, "curved_glare": False, "curved_hard_shadow": False, "curved_second_sheet": False, "curved_background": "desk"}


@pytest.fixture(scope="module")
def table():
    img, lines = S.table_page(0, dpi=100)
    return img, lines


def _gt_quad(info, page):
    Hp, Wp = page.shape[:2]
    return PageQuad(corners=np.array(info["page_corners"]), confidence=0.9, method="edge_fit", aspect_ratio=Hp / Wp)


def _persp_maps(Hm, Wo, Ho):
    Hi = np.linalg.inv(Hm)
    ys, xs = np.mgrid[0:Ho, 0:Wo].astype(np.float64) + 0.5
    d = Hi[2, 0] * xs + Hi[2, 1] * ys + Hi[2, 2]
    return ((Hi[0, 0] * xs + Hi[0, 1] * ys + Hi[0, 2]) / d).astype(np.float32), ((Hi[1, 0] * xs + Hi[1, 1] * ys + Hi[1, 2]) / d).astype(np.float32)


def test_flat_paper_keeps_perspective(table):
    page, _ = table
    ph, pm, info = S.degrade_curved(page, "flat", 3, params=SYN)
    q = _gt_quad(info, page)
    r = D.dewarp(ph, q, {"uvdoc": "off"})
    assert not r.applied and r.source == "" and not r.curved
    ref, Hm = PG.rectify(ph, q)
    assert np.array_equal(r.image, ref)  # 平的紙：與透視攤平逐像素相同
    g = M.dewarp_geometry(r.map_x, r.map_y, pm, page.shape[1::-1])
    assert g["dewarp_pos_p95_px"] < 1.0 and g["page_coverage"] > 0.97
    assert any("平的紙" in n for n in r.notes)


@pytest.mark.parametrize("kind", ["crease", "curl_crease"])
def test_curved_paper_measured_dewarp_beats_perspective(table, kind):
    page, _ = table
    Wp, Hp = page.shape[1], page.shape[0]
    ph, pm, info = S.degrade_curved(page, kind, 1, params=SYN)
    q = _gt_quad(info, page)
    out, Hm = PG.rectify(ph, q)
    before = M.dewarp_geometry(*_persp_maps(Hm, out.shape[1], out.shape[0]), pm, (Wp, Hp))
    r = D.dewarp(ph, q, {"uvdoc": "off"})
    assert r.applied and r.source == "measured" and r.curved
    after = M.dewarp_geometry(r.map_x, r.map_y, pm, (Wp, Hp))
    assert after["dewarp_pos_p95_px"] < 0.9 * before["dewarp_pos_p95_px"], (before, after, r.notes)  # 捲起處的壓縮量不到，所以只要求明顯變好
    assert after["page_coverage"] > 0.93 and after["background_frac"] < 0.05
    assert r.residual_px is not None and r.residual_before_px is not None and r.residual_px < r.residual_before_px
    assert r.image.shape[:2] == r.map_x.shape and len(r.boundary) == 4 and len(r.support) == 4
    # 對應圖與影像一致：用對應圖重新取樣原照片就是輸出影像
    again = cv2.remap(ph, r.map_x - 0.5, r.map_y - 0.5, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    assert np.abs(again.astype(int) - r.image.astype(int)).max() <= 1
    z = np.load(io.BytesIO(r.maps_npz()))
    assert set(z.files) == {"map_x", "map_y"} and z["map_x"].dtype == np.float32


def test_no_quad_and_no_model_falls_back(table, monkeypatch, tmp_path):
    page, _ = table
    ph, _, info = S.degrade_curved(page, "crease", 2, params=SYN)
    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path))
    r = D.dewarp(ph, None, {"uvdoc": "cache_only"})
    assert r.image is None and not r.applied
    assert any("不下載" in n for n in r.notes) and any("無法攤平" in n for n in r.notes)
    # 有四角時退回純量測式，照樣能攤平
    r2 = D.dewarp(ph, _gt_quad(info, page), {"uvdoc": "cache_only"})
    assert r2.image is not None and any("量測式" in n for n in r2.notes)


def test_download_failure_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("MORTISE_OFFLINE", raising=False)

    def boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(D, "_download", boom)
    path, notes = D.ensure_uvdoc_model({"uvdoc": "auto"})
    assert path is None and "無法取得" in notes[0] and "量測式" in notes[0]


def _uvdoc_cached() -> bool:
    path, _ = D.ensure_uvdoc_model({"uvdoc": "cache_only"})
    return path is not None


@pytest.mark.skipif(not _uvdoc_cached(), reason="使用者快取裡沒有 UVDoc 模型（不在測試裡下載）")
def test_uvdoc_outline_when_no_quad(table):
    page, _ = table
    Wp, Hp = page.shape[1], page.shape[0]
    ph, pm, info = S.degrade_curved(page, "curl_crease", 4, params=dict(SYN, curved_out_long=1200))
    r = D.dewarp(ph, None, {"uvdoc": "cache_only"})
    assert r.image is not None and r.corners is not None
    g = M.dewarp_geometry(r.map_x, r.map_y, pm, (Wp, Hp))
    assert g["page_coverage"] > 0.9 and g["background_frac"] < 0.08, (g, r.notes)
    assert g["dewarp_pos_p95_px"] < 25
    # 外框的四角離真值不遠（UVDoc 只給候選，精修後的角）
    err = np.linalg.norm(r.corners - np.array(info["page_corners"]), axis=1)
    assert np.median(err) < 0.02 * max(ph.shape[:2]), (err, r.notes)


def test_torch_unpickler_rejects_unknown_objects(tmp_path):
    class Evil:
        def __reduce__(self):
            return (print, ("不該執行",))

    f = tmp_path / "evil.pkl"
    with zipfile.ZipFile(f, "w") as zf:
        zf.writestr("archive/data.pkl", pickle.dumps({"model_state": Evil()}, protocol=2))
    with pytest.raises(pickle.UnpicklingError):
        D.load_torch_state_dict(f)


def test_onnx_encoder_conv_reflect_prelu_matches_numpy():
    ort = pytest.importorskip("onnxruntime")
    rng = np.random.default_rng(0)
    st = {"c.weight": rng.normal(size=(4, 3, 3, 3)).astype(np.float32), "c.bias": rng.normal(size=4).astype(np.float32),
          "bn.weight": rng.uniform(0.5, 1.5, 4).astype(np.float32), "bn.bias": rng.normal(size=4).astype(np.float32),
          "bn.running_mean": rng.normal(size=4).astype(np.float32), "bn.running_var": rng.uniform(0.5, 2, 4).astype(np.float32),
          "a.weight": np.array([0.25], np.float32)}
    b = D._OnnxBuilder(st)
    y = b.conv("img", "c.weight", "c.bias", "bn", pad=1, reflect=True)
    y = b.prelu(y, "a.weight")
    b.nodes.append(D._onnx_node("Identity", [y], ["out"]))
    x = rng.normal(size=(1, 3, 9, 7)).astype(np.float32)
    old = D.UVDOC_INPUT
    try:
        D.UVDOC_INPUT = (7, 9)
        model = b.model("img", "out", (1, 4, 9, 7))
    finally:
        D.UVDOC_INPUT = old
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    got = sess.run(None, {"img": x})[0]
    xp = np.pad(x[0], ((0, 0), (1, 1), (1, 1)), mode="reflect")
    ref = np.zeros((4, 9, 7))
    for o in range(4):
        for i in range(3):
            for dy in range(3):
                for dx in range(3):
                    ref[o] += st["c.weight"][o, i, dy, dx] * xp[i, dy : dy + 9, dx : dx + 7]
        ref[o] += st["c.bias"][o]
    k = st["bn.weight"] / np.sqrt(st["bn.running_var"] + 1e-5)
    ref = (ref - st["bn.running_mean"][:, None, None]) * k[:, None, None] + st["bn.bias"][:, None, None]
    ref = np.where(ref > 0, ref, 0.25 * ref)
    assert np.abs(got[0] - ref).max() < 1e-4


def test_rectify_interface_and_custom_dewarper(table):
    page, _ = table
    ph, _, info = S.degrade_curved(page, "flat", 3, params=SYN)
    q = _gt_quad(info, page)
    out, Hm = PG.rectify(ph, q)
    out3, Hm3, dres = PG.rectify(ph, q, return_dewarp=True)
    assert np.array_equal(out, out3) and dres is None
    same, eye = PG.rectify(ph, None)
    assert same.shape == ph.shape and np.allclose(eye, np.eye(3))
    seen = {}

    def fake(photo, quad, params):
        seen["shape"] = photo.shape
        return D.DewarpResult(image=np.zeros((10, 20, 3), np.uint8), map_x=np.zeros((10, 20), np.float32), map_y=np.zeros((10, 20), np.float32),
                              applied=True, source="measured", curved=True, homography=np.eye(3))

    PG.set_dewarper(fake)
    try:
        o, h, d = PG.rectify(ph, q, {"dewarp": True}, return_dewarp=True)
        assert seen["shape"] == ph.shape  # 拿到的是原照片，不是透視攤平後的影像
        assert o.shape == (10, 20, 3) and d.applied
        PG.set_dewarper(lambda *a: (_ for _ in ()).throw(RuntimeError("壞掉")))
        o2, _, d2 = PG.rectify(ph, q, {"dewarp": True}, return_dewarp=True)
        assert np.array_equal(o2, out) and "攤平失敗" in d2.notes[0]
    finally:
        PG.set_dewarper(None)
    # 預設的攤平函數：平的紙維持透視
    o4, _, d4 = PG.rectify(ph, q, {"dewarp": True, "uvdoc": "off"}, return_dewarp=True)
    assert np.array_equal(o4, out) and d4 is not None and not d4.applied


def test_straightness_sees_curved_lines():
    img = np.full((600, 800, 3), 250, np.uint8)
    for k in range(8):
        y0 = 60 + 65 * k
        xs = np.arange(60, 740)
        ys = y0 + 0.00012 * (xs - 400) ** 2 * (k % 2)  # 單數行彎，雙數行直
        pts = np.stack([xs, ys], 1).astype(np.int32)
        cv2.polylines(img, [pts], False, (20, 20, 20), 2, lineType=cv2.LINE_AA)
    st = D.straightness(D.find_lines(img, D._p(None)), 800, 600)
    assert st["n_h"] == 8
    devs = sorted(float(np.max(np.abs(d["dev"]))) for d in st["per"]["h"])
    assert devs[3] < 0.6 and devs[4] > 5  # 直線的偏離 < 0.6 像素；彎線弓高約 0.00012 × 340² ≈ 14 像素，離最佳直線約一半


def _hard_shadow_case():
    page, _ = S.table_page(0, dpi=100)
    h, w = page.shape[:2]
    m = np.zeros((h, w), np.float32)
    cv2.fillPoly(m, [np.array([[0.45 * w, 0], [w, 0], [w, 0.7 * h], [0.6 * w, h]], np.int32)], 1.0)
    m = cv2.GaussianBlur(m, (0, 0), 1.5)
    shadowed = np.clip(page.astype(np.float32) * (1 - 0.45 * m)[..., None], 0, 255).astype(np.uint8)
    return page, shadowed, m


def test_enhance_removes_hard_shadow_without_bands():
    page, shadowed, m = _hard_shadow_case()
    out = PG.enhance(shadowed)
    g = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gt = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
    paper = cv2.erode((gt > 245).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    inside, outside = paper & (m > 0.99), paper & (m < 0.01)
    assert abs(float(np.median(g[inside])) - float(np.median(g[outside]))) < 6  # 陰影內外的紙一樣白
    edge = (m > 0.05) & (m < 0.95)
    d = cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, 3)
    near = paper & (d > 2) & (d < 10)
    assert np.percentile(np.abs(g[near] - np.median(g[outside])), 90) < 12  # 陰影邊上沒有亮暗帶
    ink = gt < 100
    assert float(np.median(g[ink & (m > 0.99)])) < 140  # 陰影裡的字沒有被洗掉


def test_enhance_flags_glare():
    page, _ = S.table_page(0, dpi=100)
    f = page.astype(np.float32) * 0.85
    h, w = page.shape[:2]
    blob = np.zeros((h, w), np.float32)
    cv2.ellipse(blob, (w // 2, h // 3), (90, 50), 20, 0, 360, 1.0, -1)
    blob = cv2.GaussianBlur(blob, (0, 0), 6)
    f = f + (255 - f) * blob[..., None]
    out, info = PG.enhance(np.clip(f, 0, 255).astype(np.uint8), return_info=True)
    assert out.shape == page.shape
    assert info["glare_mask"].shape == (h, w) and info["glare_mask"][h // 3, w // 2]
    assert 0.002 < info["glare_frac"] < 0.05 and info["notes"] and "反光" in info["notes"][0]
    _, clean = PG.enhance(page, return_info=True)
    assert clean["glare_frac"] == 0.0 and not clean["notes"]


# ------------------------------------------------------------------ 審查後修正：驗證不可跳過、學習式外框只當候選、下載


FIX = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures"


def _flat_photo(name: str, seed: int, strength: float):
    page = S.render_pdf_page(str(FIX / f"{name}.pdf"), 0, dpi=150)[0]
    ph, Mh, _ = S.degrade(page, "photo", seed=seed, strength=strength)
    h, w = page.shape[:2]
    gt = S.apply_h(Mh, np.array([[0, 0], [w, 0], [w, h], [0, h]], float))
    return ph, gt, page


@pytest.mark.parametrize("name,seed,strength,bow_hint", [
    ("slide_dark", 502, 2.0, None),  # 深色投影片：以前把字間的深色底量成線，攤平後只剩 1 條線卻「殘差變小」，p95 0.1 → 28 px
    ("deck", 502, 1.0, None),
    ("deck", 502, 1.0, [0.0, 0.0, 25.0, 0.0]),  # 底部色帶被量成弓高（頁內線少於 3 條）：以前不驗證直接攤平
])
def test_flat_slides_are_not_dewarped(name, seed, strength, bow_hint):
    ph, gt, page = _flat_photo(name, seed, strength)
    q = PG.find_page_quad(ph)
    if bow_hint is not None:
        q.edge_bow_px = bow_hint
    r = D.dewarp(ph, q, {"uvdoc": "cache_only"})
    assert not r.applied, r.notes
    # 平的紙：維持透視攤平（與 rectify 的透視結果逐像素相同）
    ref, _ = PG.rectify(ph, q)
    assert np.array_equal(r.image, ref)


def test_lines_must_be_kept_to_compare_residuals():
    p = D._p(None)
    assert D._lines_kept(10, 7, p) and not D._lines_kept(10, 6, p) and not D._lines_kept(10, 1, p)
    assert D._lines_kept(2, 2, p) and not D._lines_kept(2, 1, p) and D._lines_kept(0, 0, p)


def test_dark_page_lines_are_measured_on_the_text():
    """深色底、亮字：量線前要反轉，量到的是文字行（直的），不是字間的深色底。"""
    img = np.full((600, 800, 3), 30, np.uint8)
    for k in range(8):
        cv2.putText(img, "Market overview and quarterly plan 2027", (50, 70 + 65 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (235, 235, 235), 2, cv2.LINE_AA)
    st = D.straightness(D.find_lines(img, D._p(None)), 800, 600)
    assert st["n_h"] >= 7 and st["p95_px"] < 1.5, st
    # 不反轉時量到的是字間的深色底（舊行為）：線數不對或偏離大
    st_old = D.straightness(D.find_lines(img, D._p({"dark_page_luma": 0.0})), 800, 600)
    assert st_old.get("n_h", 0) != st["n_h"] or st_old.get("p95_px", 0) > st["p95_px"], st_old


def test_param_prefix_from_config_file():
    p = D._p({"dewarp_bow_px": 7.5, "dewarp_flat_sag_median_pct": 0.2, "dewarp_uvdoc": "off", "dewarp_photos": True})
    assert p["bow_px"] == 7.5 and p["flat_sag_median_pct"] == 0.2 and p["uvdoc"] == "off"
    assert D._p({"dewarp_bow_px": 7.5, "bow_px": 3.0})["bow_px"] == 3.0  # 不帶前綴的優先
    with pytest.warns(UserWarning):
        D._p({"dewarp_bow_pxx": 1.0})


def test_download_failure_is_remembered(monkeypatch, tmp_path):
    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("MORTISE_OFFLINE", raising=False)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("network unreachable")

    monkeypatch.setattr(D, "_download", boom)
    D.ensure_uvdoc_model({"uvdoc": "sync"})
    path, notes = D.ensure_uvdoc_model({"uvdoc": "sync"})
    assert path is None and len(calls) == 1 and "不再重試" in notes[0]  # 一小時內不再重試


def test_partial_download_is_removed(monkeypatch, tmp_path):
    class Resp:
        def __init__(self):
            self.n = 0

        def read(self, k):
            self.n += 1
            if self.n > 2:
                raise ConnectionResetError("cut")
            return b"x" * 1000

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(D.urllib.request, "urlopen", lambda *a, **k: Resp())
    dest = tmp_path / "w.pkl"
    with pytest.raises(ConnectionResetError):
        D._download("https://example.invalid/w.pkl", dest, "0" * 64, 10_000, 5.0)
    assert not dest.exists() and not (tmp_path / "w.pkl.part").exists()


def test_first_use_downloads_in_background(monkeypatch, tmp_path):
    """快取裡沒有模型：攤平時不等下載（背景執行緒），這張先只用量測式。"""
    import threading
    import time as _t

    monkeypatch.setenv("MORTISE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("MORTISE_OFFLINE", raising=False)
    gate = threading.Event()

    def slow(params=None):
        gate.wait(5)
        return None, ["假的"]

    monkeypatch.setattr(D, "ensure_uvdoc_model", slow)
    t0 = _t.time()
    sess, notes = D._uvdoc_session(D._p({"uvdoc": "auto"}))
    assert sess is None and _t.time() - t0 < 1.0 and "背景" in notes[0]
    sess2, notes2 = D._uvdoc_session(D._p({"uvdoc": "auto"}))
    assert "正在背景下載" in notes2[0]
    gate.set()


def _closeup_table():
    page, _ = S.table_page(3, dpi=100)
    h, w = page.shape[:2]
    crop = np.ascontiguousarray(page[int(0.05 * h): int(0.75 * h), int(0.02 * w): int(0.98 * w)])
    W, H = 1200, 900
    src = np.float32([[0, 0], [crop.shape[1], 0], [crop.shape[1], crop.shape[0]], [0, crop.shape[0]]])
    dst = np.float32([[-25, -25], [W + 25, -40], [W + 30, H + 30], [-15, H + 25]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(crop, M, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


@pytest.mark.skipif(not _uvdoc_cached(), reason="使用者快取裡沒有 UVDoc 模型（不在測試裡下載）")
def test_no_paper_edges_uvdoc_outline_is_only_a_candidate():
    """表格特寫，畫面裡沒有紙邊：學習式模型找的外框不可直接當答案（鐵律），維持整張圖，外框只放在候選。"""
    ph = _closeup_table()
    r = D.dewarp(ph, None, {"uvdoc": "cache_only"})
    assert not r.applied and r.image is None and r.pseudo_quad is None, r.notes
    assert len(r.candidate_outline) == 4 and r.needs_review
    out, Hm, d = PG.rectify(ph, None, {"dewarp": True, "uvdoc": "cache_only"}, return_dewarp=True)
    assert out.shape == ph.shape and np.allclose(Hm, np.eye(3))


@pytest.mark.skipif(not _uvdoc_cached(), reason="使用者快取裡沒有 UVDoc 模型（不在測試裡下載）")
def test_rectify_uses_measured_corners_when_detection_failed(table):
    """find_page_quad 給整張圖時，攤平函數由紙邊量出的四角（至少三邊）要被 rectify 採用（平的紙做透視），並標成需要確認。"""
    page, _ = table
    Wp, Hp = page.shape[1], page.shape[0]
    ph, pm, info = S.degrade_curved(page, "flat", 5, params=SYN)
    full = PageQuad(corners=np.array([[0, 0], [ph.shape[1], 0], [ph.shape[1], ph.shape[0]], [0, ph.shape[0]]], float),
                    confidence=0.15, method="full_image", aspect_ratio=ph.shape[0] / ph.shape[1])
    out, Hm, d = PG.rectify(ph, full, {"dewarp": True, "uvdoc": "cache_only"}, return_dewarp=True)
    assert d.pseudo_quad is not None and d.measured_sides >= 3 and d.needs_review, d.notes
    assert out.shape[:2] == d.map_x.shape
    g = M.dewarp_geometry(d.map_x, d.map_y, pm, (Wp, Hp))
    assert g["dewarp_pos_p95_px"] < 2.0 and g["page_coverage"] > 0.95 and g["background_frac"] < 0.03, g
