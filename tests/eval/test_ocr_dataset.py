"""文字辨識訓練資料：原生 PDF 自我監督（PDF 文字層當標籤、裁切對得上字、照片的字夠大、旋轉的字略過）、字典外的字、
使用者的文字修正（③文字行與網頁送的⑤段落；工作目錄、回饋包、工作目錄已刪除時重跑流水線、沒有同意就不收）、
不受信任的回饋包（路徑穿越）、依行數切驗證集。只用 tests/fixtures 的虛構資料。"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mortise.eval import ocr_dataset as D

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def selfsup(tmp_path_factory):
    fx = tmp_path_factory.mktemp("fx")
    shutil.copy(FIX / "slide_basic.pdf", fx / "slide_basic.pdf")
    out = tmp_path_factory.mktemp("ds")
    res = D.build_dataset(out, from_fixtures=True, fixtures=fx, variants=["clean", "photo"], max_pages=1, val_fraction=0.0)
    return out, res


def test_self_supervised_format_and_manifest(selfsup):
    out, res = selfsup
    rows = [ln.split("\t") for ln in (out / "rec_gt.txt").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == res["n_lines"] > 6 and all(len(r) == 2 for r in rows)
    assert res["by_source"] == {"self_supervised": res["n_lines"]} and res["n_train"] + res["n_val"] == res["n_lines"]
    labels = [lab for _, lab in rows]
    assert "Mortise 季度報告 Quarterly" in labels  # 長行在空白處切段，標籤來自 PDF 文字層
    assert all(0 < len(lab) <= D.DEFAULT_PARAMS["max_label_chars"] and lab == lab.strip() and "\t" not in lab for lab in labels)
    for rel, _ in rows:
        assert not rel.startswith("/") and (out / rel).exists()
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["schema"] == D.SCHEMA and m["engine"]["version"].startswith("mortise-")
    assert {it["variant"] for it in m["items"]} == {"clean", "photo"}
    # slide_basic.pdf 是隨原始碼提交的（不是 make_fixtures.py 產生的）：授權說明分開寫
    assert all(it["license"] == D.FIXTURE_LICENSE_OTHER and it["consented"] is None and it["source"] == "self_supervised" for it in m["items"])
    assert D.fixture_license(FIX / "slide_basic.pdf") == D.FIXTURE_LICENSE_OTHER and D.fixture_license(FIX / "poster.pdf") == D.FIXTURE_LICENSE
    assert all(it["seed"] is None for it in m["items"] if it["variant"] == "clean")
    # 照片干擾先畫成 300 dpi 再翻拍：字夠大（看得清楚），行高分佈寫在 manifest
    photo = [it for it in m["items"] if it["variant"] == "photo"]
    assert photo and all(it["height"] >= D.DEFAULT_PARAMS["min_height_px_photo"] and it["dpi"] == D.DEFAULT_PARAMS["photo_dpi"] for it in photo)
    assert m["height_px"]["photo"]["median"] >= 24 and m["height_px"]["photo"]["n"] == len(photo) and m["height_px"]["clean"]["n"] > 0
    assert (out / "dict.txt").exists() and len((out / "dict.txt").read_text(encoding="utf-8").splitlines()) == m["dict_size"] > 1000
    assert m["out_of_dict"] == [] and (out / "out_of_dict.txt").read_text(encoding="utf-8") == ""


def test_crops_line_up_with_labels(selfsup):
    """裁出來的圖丟給目前的文字辨識，要讀得出標籤（裁切位置對得上字，沒有裁到別行）。"""
    from mortise.recognition.ocr import OcrEngine

    out, _ = selfsup
    rows = [ln.split("\t") for ln in (out / "rec_gt.txt").read_text(encoding="utf-8").splitlines()]
    eng = OcrEngine()
    sims = []
    for rel, lab in [r for r in rows if ".clean." in r[0]][:4]:
        img = np.asarray(Image.open(out / rel).convert("RGB"))
        # 單行的圖：四周補白再讀（偵測模型需要一點邊界）
        pad = np.full((img.shape[0] * 3, img.shape[1] + 2 * img.shape[0], 3), 255, np.uint8)
        pad[img.shape[0]:2 * img.shape[0], img.shape[0]:img.shape[0] + img.shape[1]] = img
        got = "".join(ln.text for ln in eng.read(pad)).replace(" ", "")
        sims.append(difflib.SequenceMatcher(None, got, lab.replace(" ", "")).ratio())
    assert min(sims) > 0.8, sims


def test_crops_of_photos_are_readable(selfsup):
    """照片干擾的圖人眼要讀得出來：丟給目前的文字辨識，多數行要讀得出標籤（150 dpi 翻拍時平均錯字率約 0.6）。"""
    from mortise.recognition.ocr import OcrEngine

    def cer(truth: str, got: str) -> float:  # 編輯距離 ÷ 答案字數
        prev = list(range(len(got) + 1))
        for i, a in enumerate(truth, 1):
            cur = [i]
            for j, b in enumerate(got, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
            prev = cur
        return prev[-1] / max(1, len(truth))

    out, _ = selfsup
    rows = [ln.split("\t") for ln in (out / "rec_gt.txt").read_text(encoding="utf-8").splitlines() if ".photo." in ln]
    eng = OcrEngine()
    errs = []
    for rel, lab in rows[:12]:
        img = np.asarray(Image.open(out / rel).convert("RGB"))
        pad = np.full((img.shape[0] * 3, img.shape[1] + 2 * img.shape[0], 3), 255, np.uint8)
        pad[img.shape[0]:2 * img.shape[0], img.shape[0]:img.shape[0] + img.shape[1]] = img
        got = " ".join(ln.text for ln in eng.read(pad))
        errs.append(cer(lab.replace(" ", ""), got.replace(" ", "")))
    assert float(np.mean(errs)) < 0.25, errs


def test_rotated_text_is_skipped(tmp_path):
    """海報側邊的直排字（PDFium 的字元角度是 270 度）整串略過，不會切成一字一行配上正立的標籤。"""
    fx = tmp_path / "fx"
    fx.mkdir()
    shutil.copy(FIX / "poster.pdf", fx / "poster.pdf")
    res = D.build_dataset(tmp_path / "out", from_fixtures=True, fixtures=fx, variants=["clean"], max_pages=1, val_fraction=0.0)
    labels = [it["label"] for it in res["items"]]
    assert "SUMMER FEST" in " ".join(labels)
    assert not any(len(lab) == 1 for lab in labels), labels  # 側標的「Organised by…」不會變成一個字一筆
    assert any("旋轉的字" in sk["reason"] for sk in res["skipped"])


def test_split_paragraph_text():
    f = D.split_paragraph_text
    assert f("ABX DEF GHI", ["ABC", "DEF", "GHI"], [" ", " "]) == ["ABX", "DEF", "GHI"]  # 改在行尾的字：空白還在，切得開
    assert f("ABC DEFx GHI", ["ABC", "DEF", "GHI"], [" ", " "]) == ["ABC", "DEFx", "GHI"]  # 插在行尾（後面接空白）：算上一行
    assert f("第一行文子第二行字", ["第一行文字", "第二行字"], [""]) == ["第一行文子", "第二行字"]  # 中文：改的是行內的字
    assert f("第一行文字插入第二行字", ["第一行文字", "第二行字"], [""]) == [None, None]  # 插在中文的換行處：不知道屬於哪一行
    assert f("ABC-DEF", ["ABC", "DEF"], [" "]) == [None, None]  # 換行處的空白被改成別的字
    assert f("one\ntwo", ["onx", "twx"], [" "]) == ["one", "two"]  # 使用者自己打了換行、行數相同
    assert f("Hello world", ["Hello"], []) == ["Hello world"]


def test_val_split_follows_line_count():
    items = [D.Item(f"i{k}.png", "x", "self_supervised", {}, None, "", f"pdf|a.pdf|{k}") for k in range(40) for _ in range(1 + k % 7)]
    D._assign_splits(items, 0.1)
    frac = sum(it.split == "val" for it in items) / len(items)
    assert 0.07 <= frac <= 0.13, frac
    by_page: dict = {}
    for it in items:
        by_page.setdefault(it.split_key, set()).add(it.split)
    assert all(len(v) == 1 for v in by_page.values())  # 同一頁的行不會分到兩邊


def test_out_of_dict_and_clean_label():
    assert D.clean_label("  ﬁle\tname \n x ") == "file name x"
    assert D.clean_label("（萬元）") == "（萬元）"  # 全形標點保留
    ood = D.out_of_dict(["ab c", "a𠀀", "𠀀"], ["a", "b", "c"])
    assert [o["char"] for o in ood] == ["𠀀"] and ood[0]["count"] == 2 and ood[0]["codepoint"] == "U+20000"


@pytest.fixture(scope="module")
def image_job(tmp_path_factory):
    """一份影像（截圖）工作，使用者改了③的一行字。"""
    from mortise.artifacts import Intent, OutputFormat
    from mortise.eval.runner import load_artifacts
    from mortise.eval.synth import render_pdf_page
    from mortise.pipeline import Pipeline
    from mortise.pipeline.runner import user_correction

    root = tmp_path_factory.mktemp("jobs")
    img, _ = render_pdf_page(FIX / "slide_basic.pdf", 0, 150.0)
    png = root / "slide.png"
    Image.fromarray(img).save(png)
    job_id = "20261123-0123456789ab"
    pipe = Pipeline(root / job_id)
    pipe.start(png, "slide.png", Intent(outputs=[OutputFormat.PDF]))
    pipe.state["allow_training"] = False
    pipe._save_state()
    pipe.run()
    run = load_artifacts(pipe)
    line = next(t for t in run.elements[0].texts if "-t-" in t.id and len(t.text) > 6)
    fixed = line.text + "（改）"
    pipe.add_corrections([user_correction(3, "text", fixed, line.id, 0)])
    return root, job_id, png, line, fixed


def test_feedback_from_job_dir_needs_consent(image_job, tmp_path):
    root, job_id, _, line, fixed = image_job
    res = D.build_dataset(tmp_path / "a", feedback=[root / job_id], jobs_root=root)
    assert res["n_lines"] == 0 and any("同意" in s["reason"] for s in res["skipped"])
    res = D.build_dataset(tmp_path / "b", feedback=[root / job_id], jobs_root=root, include_unconsented=True)
    assert res["n_lines"] == 1 and res["by_source"] == {"user_correction": 1}
    it = res["items"][0]
    assert it["label"] == fixed and it["system_text"] == line.text and it["element_id"] == line.id
    assert it["consented"] is False and it["license"] == D.FEEDBACK_LICENSE_UNCONSENTED and it["image_from"] == "工作目錄"
    img = Image.open(tmp_path / "b" / it["image"])
    assert img.height >= 10 and img.width > 2 * img.height
    assert (tmp_path / "b" / "rec_gt.txt").read_text(encoding="utf-8") == f"{it['image']}\t{fixed}\n"


def test_feedback_from_bundle_and_rerun_when_job_deleted(image_job, tmp_path):
    from mortise.eval.feedback import feedback_from_job, write_bundle
    from mortise.pipeline import Pipeline

    root, job_id, png, line, fixed = image_job
    pipe = Pipeline(root / job_id)
    fb = feedback_from_job("slide.png", [], pipe.corrections, {}, {"job_id": job_id, "filename": "slide.png", "consented": True})
    z = write_bundle(tmp_path / "slide.mortise-feedback.zip", png, "slide.png", fb)
    res = D.build_dataset(tmp_path / "a", feedback=[z], jobs_root=root)  # 工作目錄還在
    assert res["n_lines"] == 1 and res["items"][0]["image_from"] == "工作目錄" and res["items"][0]["consented"] is True
    empty = tmp_path / "no-jobs"
    empty.mkdir()
    res = D.build_dataset(tmp_path / "b", feedback=[z, z], jobs_root=empty)  # 工作目錄已刪除：用回饋包裡的原始檔重跑；同一筆只收一次
    assert res["n_lines"] == 1 and res["items"][0]["image_from"] == "重新跑流水線" and res["items"][0]["label"] == fixed


def test_embed_dictionary_for_finetuned_model(tmp_path):
    """微調後的模型（paddle2onnx 轉出來沒有字典）放回 Mortise：把字典寫進 metadata，OcrEngine 用設定的路徑就能載入；字典對不上要拒絕。"""
    from mortise.engine import ocr_model_paths
    from mortise.eval.__main__ import main
    from mortise.recognition.ocr import OcrEngine

    charset, _ = D.model_charset()
    good = tmp_path / "dict.txt"
    good.write_text("".join(c + "\n" for c in charset), encoding="utf-8")
    info = D.embed_dictionary(ocr_model_paths()["rec"], good, tmp_path / "rec.onnx")
    assert info["metadata_ok"] and info["model_classes"] == len(charset) + 2
    assert len(OcrEngine({"rec_model_path": str(tmp_path / "rec.onnx")}).charset) == len(charset) + 2
    bad = tmp_path / "bad.txt"
    bad.write_text("".join(c + "\n" for c in charset[:-3]), encoding="utf-8")
    with pytest.raises(ValueError, match="字典和模型不是同一組"):
        D.embed_dictionary(ocr_model_paths()["rec"], bad, tmp_path / "bad.onnx")
    assert not (tmp_path / "bad.onnx").exists()
    assert main(["ocr-model", str(tmp_path / "rec.onnx"), "--dict", str(good), "--out", str(tmp_path / "again.onnx")]) == 0
    assert main(["ocr-model", str(tmp_path / "rec.onnx"), "--dict", str(bad), "--out", str(tmp_path / "x.onnx")]) == 2
