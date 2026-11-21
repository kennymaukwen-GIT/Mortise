"""評測與回饋的命令列：

  python -m mortise.eval run 樣本目錄 [--out 輸出目錄] [--baseline 先前的 summary.json]
                         [--variants screenshot,scan,photo] [--limit N] [--seed 0] [--strength 1.0] [--config 覆寫檔.toml]
  python -m mortise.eval tune 樣本目錄 [--out 輸出目錄] [--tunable tunable.toml] [--objective composite]
                         [--validation 0.25] [--rounds 2] [--limit N] [--config 覆寫檔.toml]
  python -m mortise.eval truth-template 樣本檔 [--out 樣本.truth.json]      由流水線結果產生標準答案草稿
  python -m mortise.eval feedback-import 樣本目錄 --store 回饋庫目錄          匯入樣本旁的 .feedback.json
  python -m mortise.eval annotations --store 回饋庫目錄 --out 標註.jsonl [--jobs 評測輸出目錄]
  python -m mortise.eval calibrate 標註.jsonl [--stage 3] [--field font_size]

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
    fi = sub.add_parser("feedback-import", help="匯入樣本目錄中的 .feedback.json")
    fi.add_argument("samples", type=Path)
    fi.add_argument("--store", type=Path, required=True)
    an = sub.add_parser("annotations", help="把回饋轉成標註資料（JSONL）")
    an.add_argument("--store", type=Path, required=True)
    an.add_argument("--out", type=Path, required=True)
    an.add_argument("--jobs", type=Path, help="評測輸出目錄（找系統當時的值用；裡面的 jobs/<樣本>/main）")
    ca = sub.add_parser("calibrate", help="由標註資料建信心值校準表")
    ca.add_argument("annotations", type=Path)
    ca.add_argument("--stage", type=int)
    ca.add_argument("--field")
    a = ap.parse_args(argv)
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

        print(f"匯入 {FeedbackStore(a.store).import_sample_dir(a.samples)} 份回饋")
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
