"""評測與回饋的命令列：

  python -m mortise.eval run 樣本目錄 [--out 輸出目錄] [--baseline 先前的 summary.json]
                         [--variants screenshot,scan,photo] [--limit N] [--seed 0] [--strength 1.0] [--config 覆寫檔.toml]
  python -m mortise.eval tune 樣本目錄 [--out 輸出目錄] [--tunable tunable.toml] [--objective composite]
                         [--validation 0.25] [--rounds 2] [--limit N] [--config 覆寫檔.toml]
  python -m mortise.eval truth-template 樣本檔 [--out 樣本.truth.json]      由流水線結果產生標準答案草稿
  python -m mortise.eval feedback-import 樣本目錄 --store 回饋庫目錄          匯入樣本旁的 .feedback.json
  python -m mortise.eval annotations --store 回饋庫目錄 --out 標註.jsonl [--jobs 評測輸出目錄]
  python -m mortise.eval calibrate 標註.jsonl [--stage 3] [--field font_size]
  python -m mortise.eval vector --out 輸出目錄 [--samples 真實樣本目錄] [--limit N] [--variants clean,jpeg,...] [--no-vtracer]
                         [--baseline 基準.json] [--save-baseline 基準.json]      描圖向量化評測
  python -m mortise.eval scorecard [--quick] --out 目錄 [--jobs 2] [--seed 0] [--config 覆寫檔.toml]   引擎成績單（見 scorecard.py）
  python -m mortise.eval scorecard compare 舊.json 新.json [--gate 指標=容許量 ...]          比較兩份成績單，退步超過門檻時結束碼 1
  python -m mortise.eval ocr-dataset --out 目錄 [--from-fixtures] [--pdf PDF ...] [--from-feedback 回饋來源 ...] [--include-unconsented]
                         文字辨識（PaddleOCR 辨識模型）訓練資料（見 ocr_dataset.py）
  python -m mortise.eval ocr-model 微調後.onnx --dict dict.txt --out 放回用.onnx   把字典寫進模型、檢查能不能用
  python -m mortise.eval feedback-import 匯出的.mortise-feedback.zip --store 回饋庫目錄      也接受網頁匯出的回饋包

樣本目錄的放法見 mortise/eval/runner.py 開頭的說明。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ..config import Config


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "scorecard":  # 成績單有自己的子指令（compare），整段交給它解析
        from .scorecard import main_cli

        return main_cli(args[1:], lambda s: print(s, file=sys.stderr))
    ap = argparse.ArgumentParser(prog="python -m mortise.eval", description="Mortise 評測、回饋與自動調參")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="批次評測")
    r.add_argument("samples", type=Path)
    r.add_argument("--out", type=Path)
    r.add_argument("--baseline", type=Path)
    r.add_argument("--variants", default="screenshot,scan,photo", help="逗號分隔；空字串表示不做自我監督")
    r.add_argument("--limit", type=int)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--strength", type=float, default=1.0)
    r.add_argument("--config", type=Path, help="設定檔覆寫檔（例如自動調參產生的 override.toml）")
    t = sub.add_parser("tune", help="自動調參")
    t.add_argument("samples", type=Path)
    t.add_argument("--out", type=Path)
    t.add_argument("--tunable", type=Path)
    t.add_argument("--objective", default="composite", help="指標名稱，或 JSON：{\"text_f1\": 2, \"e2e_score\": 1}")
    t.add_argument("--validation", type=float, default=0.25)
    t.add_argument("--rounds", type=int, default=2)
    t.add_argument("--limit", type=int)
    t.add_argument("--variants", default="", help="調參時也做自我監督的干擾種類（逗號分隔，預設不做）")
    t.add_argument("--config", type=Path)
    tt = sub.add_parser("truth-template", help="由流水線結果產生標準答案草稿")
    tt.add_argument("file", type=Path)
    tt.add_argument("--out", type=Path)
    fi = sub.add_parser("feedback-import", help="匯入樣本目錄中的 .feedback.json，或網頁匯出的回饋包（.mortise-feedback.zip）")
    fi.add_argument("samples", type=Path, help="樣本目錄或回饋包")
    fi.add_argument("--store", type=Path, required=True)
    an = sub.add_parser("annotations", help="把回饋轉成標註資料（JSONL）")
    an.add_argument("--store", type=Path, required=True)
    an.add_argument("--out", type=Path, required=True)
    an.add_argument("--jobs", type=Path, help="評測輸出目錄（找系統當時的值用；裡面的 jobs/<樣本>/main）")
    ca = sub.add_parser("calibrate", help="由標註資料建信心值校準表")
    ca.add_argument("annotations", type=Path)
    ca.add_argument("--stage", type=int)
    ca.add_argument("--field")
    vb = sub.add_parser("vector", help="描圖向量化評測（合成真值，對照 VTracer 0.6.15）")
    vb.add_argument("--out", type=Path, required=True)
    vb.add_argument("--samples", type=Path, help="真實樣本目錄（PNG、JPEG；沒有真值，只報重繪誤差、節點數、耗時）")
    vb.add_argument("--limit", type=int, help="只跑前 N 個合成樣本（每一類平均取）")
    vb.add_argument("--variants", default="clean,down,up,blur,jpeg,noise", help="干擾種類，逗號分隔")
    vb.add_argument("--categories", default="", help="只跑這些圖形類型（逗號分隔；空字串表示全部）")
    vb.add_argument("--no-vtracer", action="store_true", help="不跑 VTracer 對照組")
    vb.add_argument("--no-panels", action="store_true", help="不輸出每個樣本的並排圖")
    vb.add_argument("--baseline", type=Path, help="與這個基準比較（寬鬆門檻），退步就回傳 1")
    vb.add_argument("--save-baseline", type=Path, help="把這次的分組數字存成基準檔")
    vb.add_argument("--seed", type=int)
    vb.add_argument("--config", type=Path)
    sub.add_parser("scorecard", help="引擎成績單：scorecard [--quick] --out 目錄；scorecard compare 舊.json 新.json", add_help=False)
    od = sub.add_parser("ocr-dataset", help="文字辨識（PaddleOCR 辨識模型）訓練資料")
    od.add_argument("--out", type=Path, required=True)
    od.add_argument("--from-fixtures", action="store_true", help="用 tests/fixtures 的原生 PDF 自我監督產生")
    od.add_argument("--pdf", type=Path, action="append", default=[], help="另外加入的原生 PDF（檔案或目錄，可重複；授權要自己確認）")
    od.add_argument("--from-feedback", type=Path, action="append", default=[], help="回饋來源：回饋庫目錄、匯出的 .mortise-feedback.zip 或工作目錄（可重複）")
    od.add_argument("--jobs-root", type=Path, help="工作目錄的根目錄（讀回饋時找工作影像用，預設 MORTISE_JOBS 或 ./jobs）")
    od.add_argument("--variants", default="clean,screenshot,scan,photo", help="原生 PDF 要做的干擾（逗號分隔；clean 表示不加干擾）")
    od.add_argument("--seed", type=int, default=0)
    od.add_argument("--max-pages", type=int, default=5, help="每份 PDF 最多用幾頁")
    od.add_argument("--val", type=float, default=0.1, help="驗證集比例（依行數逼近；依頁切分，同一頁的行不會分到兩邊）")
    od.add_argument("--include-unconsented", action="store_true", help="也收沒有勾選「同意用於改善辨識」或同意狀態不明的回饋（確定是你自己的資料時）")
    om = sub.add_parser("ocr-model", help="把字典寫進微調好的辨識模型 ONNX，檢查能不能給 Mortise 用，並印出換上的設定")
    om.add_argument("onnx", type=Path, help="paddle2onnx 轉出來的辨識模型")
    om.add_argument("--dict", type=Path, required=True, help="訓練時用的字典（ocr-dataset 輸出的 dict.txt，或你擴充過的版本）")
    om.add_argument("--out", type=Path, required=True, help="寫出的 ONNX（放在固定的位置，設定檔會指到它）")
    a = ap.parse_args(args)
    say = lambda s: print(s, file=sys.stderr)  # noqa: E731

    if a.cmd == "run":
        from .runner import run_batch

        variants = [v for v in a.variants.split(",") if v.strip()]
        s = run_batch(a.samples, a.out, a.baseline, variants, a.limit, Config.load(a.config), {"seed": a.seed, "strength": a.strength}, say)
        regs = s.get("baseline", {}).get("regressions", [])
        print(f"樣本 {s['n_samples']} 個、評測 {s['n_evaluations']} 次、錯誤 {len(s['errors'])} 筆；綜合分數平均 {s['overall'].get('composite', {}).get('mean')}")
        if regs:
            print(f"有 {len(regs)} 項退步超過容許範圍（見 summary.md）")
            return 1
        return 0
    if a.cmd == "tune":
        from .tuner import tune

        obj = json.loads(a.objective) if a.objective.strip().startswith("{") else a.objective
        out = a.out or a.samples.parent / f"{a.samples.name}-tune"
        variants = [v for v in a.variants.split(",") if v.strip()]
        res = tune(a.samples, out, a.tunable, Config.load(a.config), {"objective": obj, "validation_fraction": a.validation, "max_rounds": a.rounds,
                                                                     "eval": {"variants": variants}}, a.limit, say)
        print(f"調參分數 {res.train_before} → {res.train_after}；驗證分數 {res.validation_before} → {res.validation_after}；建議採用：{res.adopt}")
        for pr in res.params:
            if pr.before != pr.after:
                print(f"  {pr.path}：{pr.before} → {pr.after}（分數 {pr.score_before:.4f} → {pr.score_after:.4f}）")
        print(f"覆寫檔：{res.override_path}")
        return 0
    if a.cmd == "truth-template":
        from .runner import run_pipeline, sidecar, write_truth_template

        _, run = run_pipeline(Path(tempfile.mkdtemp(prefix="mortise-truth-")), a.file, Config.load(), ["pptx"])
        print(write_truth_template(run, a.out or sidecar(a.file, ".truth.json")))
        return 0
    if a.cmd == "feedback-import":
        from .feedback import FeedbackStore

        store = FeedbackStore(a.store)
        if a.samples.is_file() and a.samples.suffix.lower() == ".zip":  # 網頁匯出的回饋包
            fb = store.import_bundle(a.samples)
            print(f"匯入 1 份回饋包：{fb.sample}（修正 {len(fb.corrections)} 筆、評分 {len(fb.page_ratings)} 筆）")
            return 0
        print(f"匯入 {store.import_sample_dir(a.samples)} 份回饋")
        return 0
    if a.cmd == "ocr-dataset":
        from .ocr_dataset import build_dataset

        if not a.from_fixtures and not a.pdf and not a.from_feedback:
            print("要給資料來源：--from-fixtures、--pdf 或 --from-feedback", file=sys.stderr)
            return 2
        res = build_dataset(a.out, from_fixtures=a.from_fixtures, pdfs=a.pdf, feedback=a.from_feedback, jobs_root=a.jobs_root,
                            variants=[v for v in a.variants.split(",") if v.strip()], seed=a.seed, max_pages=a.max_pages, val_fraction=a.val,
                            include_unconsented=a.include_unconsented, say=say)
        print(f"寫出 {res['n_lines']} 行（訓練 {res['n_train']}、驗證 {res['n_val']}（{100 * res['val_fraction_actual']:.1f}%）；自我監督 {res['by_source'].get('self_supervised', 0)}、"
              f"使用者修正 {res['by_source'].get('user_correction', 0)}）；字典外的字 {len(res['out_of_dict'])} 個：{a.out}")
        return 0
    if a.cmd == "annotations":
        from .feedback import FeedbackStore, correction_stats, read_annotations

        resolver = None
        if a.jobs:
            from ..pipeline import Pipeline
            from .runner import load_artifacts

            def resolver(sample: str):
                job = a.jobs / "jobs" / sample.replace("/", "__") / "main"
                return load_artifacts(Pipeline(job)) if (job / "job.json").exists() else None

        n = FeedbackStore(a.store).write_annotations(a.out, resolver)
        print(f"寫出 {n} 筆標註資料：{a.out}")
        print(json.dumps(correction_stats(read_annotations(a.out)), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "calibrate":
        from .feedback import calibration_from_annotations, read_annotations

        tab = calibration_from_annotations(read_annotations(a.annotations), a.stage, a.field)
        print(json.dumps(tab.to_dict(), ensure_ascii=False, indent=1))
        return 0 if tab.sufficient else 3
    if a.cmd == "ocr-model":
        from .ocr_dataset import embed_dictionary

        try:
            info = embed_dictionary(a.onnx, a.dict, a.out)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"不能用：{e}", file=sys.stderr)
            return 2
        print(f"可以用：字典 {info['dict_size']} 個字、模型輸出 {info['model_classes']} 類。換上的方法：設定覆寫檔（例如 new_ocr.toml）寫")
        print(f"\n[recognize.ocr_params]\nrec_model_path = \"{a.out.resolve()}\"\n")
        print("再跑 python -m mortise.eval scorecard --config new_ocr.toml --out 目錄，用 scorecard compare 和舊的成績單比，沒有退步才採用。")
        print("採用時：網頁伺服器只讀 mortise/config/default.toml（不讀覆寫檔），所以把上面那兩行加在 default.toml 的 [recognize] 那一節後面"
              "（[recognize] 底下不能已經有 ocr_params 這個鍵），然後重新啟動伺服器（同一個程式只載入一次文字辨識模型）。")
        return 0
    if a.cmd == "vector":
        from . import vector_bench as vbm

        return vbm.main_cli(a, Config.load(a.config), say)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
