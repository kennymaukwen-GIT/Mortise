"""描圖向量化評測（mortise/eval/vector_bench.py）的測試：合成真值的決定性、計分本身、以及一個小子集的寬鬆回歸門檻。

完整評測（64 個樣本 × 6 種干擾，含 VTracer 對照組）約要幾分鐘，不在這裡跑：
  python -m mortise.eval vector --out 輸出目錄 --save-baseline tests/eval/baseline_vector_20261121.json
基準檔裡的 regression_subset 是下面 _subset() 這個小子集當時的數字，這裡重跑同一個子集來比。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mortise.eval import vector_bench as vb

BASELINE = Path(__file__).with_name("baseline_vector_20261121.json")
SUBSET = {"variants": ["clean", "jpeg", "noise"], "limit": 8}


def _subset() -> dict:
    return vb.run_bench(None, variants=SUBSET["variants"], limit=SUBSET["limit"], with_vtracer=False, panels=False)


def test_samples_fixed_and_cover_all_categories():
    a, b = vb.make_samples(), vb.make_samples()
    assert len(a) >= 60
    assert {s.category for s in a} == set(vb.CATEGORIES)
    assert [s.name for s in a] == [s.name for s in b]
    assert all(x.path.path == y.path.path for sa, sb in zip(a, b) for x, y in zip(sa.shapes, sb.shapes))


@pytest.mark.parametrize("variant", vb.VARIANTS)
def test_perturbations_are_deterministic(variant):
    s = vb.make_samples()[3]
    i1, k1, d1 = vb.perturb(s, variant, np.random.default_rng([vb.SEED, 3, vb.VARIANTS.index(variant)]))
    i2, k2, d2 = vb.perturb(s, variant, np.random.default_rng([vb.SEED, 3, vb.VARIANTS.index(variant)]))
    assert i1.dtype == np.uint8 and np.array_equal(i1, i2) and k1 == k2 and d1 == d2


def test_truth_scores_perfectly():
    # 拿真值本身去計分：IoU 1、邊界距離 0、節點數比 1、各項命中率滿分（確認計分沒有偏差）
    for s in vb.make_samples()[::9]:
        img = vb.truth_image(s)
        ev = vb.evaluate(s, [t.path for t in s.shapes], 1.0, s.background, img)
        assert ev["iou"] > 0.999 and ev["de_truth"] < 0.01
        assert ev["chamfer"] is None or ev["chamfer"] < 0.02
        assert ev["node_ratio"] == pytest.approx(1.0)
        for key in ("completion", "stroke", "gradient"):  # 真值路徑本身沒有標 primitive，所以不看基本幾何那一項
            got, tot = ev["hits"][key]
            assert got == tot, key


def test_vtracer_output_is_parsed():
    pytest.importorskip("vtracer")
    s = vb.make_samples()[0]
    img = vb.truth_image(s)
    paths = vb.vtracer_paths(img)
    assert paths and all(p.fill is not None and p.path and int(p.path[0][0]) == 0 for p in paths)
    kept, bg = vb._drop_background(paths, img.shape[1], img.shape[0])
    assert bg is not None and len(kept) == len(paths) - 1


def test_cli_writes_summary(tmp_path):
    from mortise.eval.__main__ import main

    out = tmp_path / "vec"
    base = tmp_path / "base.json"
    assert main(["vector", "--out", str(out), "--limit", "2", "--variants", "clean", "--no-vtracer", "--no-panels", "--save-baseline", str(base)]) == 0
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["n_runs"] == 2 and (out / "summary.md").read_text(encoding="utf-8").startswith("# 描圖向量化評測")
    assert json.loads(base.read_text(encoding="utf-8"))["overall"]["n"] == 2
    # 跟自己的基準比一定沒有退步
    assert main(["vector", "--out", str(out), "--limit", "2", "--variants", "clean", "--no-vtracer", "--no-panels", "--baseline", str(base)]) == 0


def test_regression_subset_against_baseline():
    """小子集（8 個樣本 × clean、jpeg、noise）與基準檔比，用寬鬆門檻（compare_with_baseline 的預設值）。"""
    assert BASELINE.exists(), "缺基準檔：先跑完整評測並 --save-baseline"
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert base["n_samples"] >= 60 and "vtracer" in base
    s = _subset()
    regs = vb.compare_with_baseline(s, base["regression_subset"])
    assert not regs, "；".join(regs)
    o = s["overall"]
    # 絕對門檻（量到的數字留餘裕）：全部通過自我驗證、對真值色差小、邊界距離在十分之幾像素內
    assert o["accepted_rate"] >= 0.9
    assert o["de_truth"] < 0.6 and o["iou"] > 0.97 and o["chamfer"] < 0.25 and o["node_ratio"] < 3.0
