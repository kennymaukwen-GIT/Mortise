"""命令列介面。

  mortise run 檔案 [-o 輸出目錄] [--job 工作目錄] [--size 297x210mm] [--format pptx]
  mortise correct 工作目錄 --stage 5 --field font_family --value Arial [--element 編號] [--page 1]
  mortise report 工作目錄
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from .artifacts import FidelityReport, Intent, OutputFormat, PhysicalSize
from .pipeline import Pipeline, PipelineError
from .pipeline.runner import user_correction


def _size(text: str) -> PhysicalSize:
    m = re.fullmatch(r"\s*([\d.]+)\s*[x×*]\s*([\d.]+)\s*(mm|cm|in|pt|px)\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("尺寸格式為 寬x高單位，例如 297x210mm")
    return PhysicalSize(width=float(m.group(1)), height=float(m.group(2)), unit=m.group(3))


def _summary(report: FidelityReport, pipe: Pipeline) -> str:
    lines = []
    score = "未驗證" if report.overall_score is None else f"{report.overall_score:.3f}"
    lines.append(f"整體分數：{score}（0 到 1）")
    if report.target_engine:
        lines.append(f"驗證引擎：{report.target_engine}")
    for r in pipe.state.get("rounds", []):
        kept = "" if "kept" not in r else ("，採用" if r["kept"] else "，沒有進步，已退回")
        lines.append(f"  第 {r['round']} 輪：分數 {r['score']}，提出 {r['proposed']} 張修正單{kept}")
    fmt_pages = {(fr.format.value, fp.page_index): fp for fr in report.format_reports for fp in fr.pages}
    for p in report.pages:
        boxes = [e for e in p.elements if e.kind == "text_box"]
        parts = []
        if p.target_similarity is not None:  # PowerPoint 的目標驗證（LibreOffice 渲染）
            ok = sum(1 for e in boxes if e.text_match and e.linebreak_match)
            parts.append(f"PowerPoint 渲染相似度 {p.target_similarity:.3f}；文字框 {len(boxes)} 個，其中 {ok} 個文字與斷行都一致")
        if p.neutral_similarity is not None:
            parts.append(f"成品自行渲染相似度 {p.neutral_similarity:.3f}")
        for (fmt, idx), fp in sorted(fmt_pages.items()):
            if idx == p.page_index and fp.similarity is not None:
                extra = f"，讀回文字一致 {fp.text_match_ratio:.0%}" if fp.text_match_ratio is not None else ""
                parts.append(f"{fmt} 讀回相似度 {fp.similarity:.3f}{extra}")
        lines.append(f"第 {p.page_index + 1} 頁：" + ("；".join(parts) if parts else "沒有做渲染比對"))
        for i in p.issues:
            if i.severity.value != "info":
                lines.append(f"  [{i.severity.value}] {i.message}" + (f"（{i.element_id}）" if i.element_id else ""))
    if report.field_counts:
        lines.append("欄位來源：" + "、".join(f"{k} {v}" for k, v in sorted(report.field_counts.items())))
    for n in report.notes:
        lines.append(f"備註：{n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mortise", description="把不可編輯的文件還原成可編輯、排版一致的檔案")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="處理一個檔案")
    run.add_argument("file", type=Path)
    run.add_argument("-o", "--out", type=Path, default=Path("."), help="輸出目錄")
    run.add_argument("--job", type=Path, help="工作目錄（保留各階段產物，之後可下修正單）")
    run.add_argument("--size", type=_size, help="實際尺寸，例如 297x210mm")
    run.add_argument("--format", action="append", choices=[f.value for f in OutputFormat], help="輸出格式（可重複）")
    cor = sub.add_parser("correct", help="下一張修正單並重跑")
    cor.add_argument("job", type=Path)
    cor.add_argument("--stage", type=int, required=True, choices=range(1, 8))
    cor.add_argument("--field", required=True)
    cor.add_argument("--value", required=True, help="新值（JSON 或純文字）")
    cor.add_argument("--element")
    cor.add_argument("--page", type=int, help="頁碼（從 1 起算）")
    cor.add_argument("-o", "--out", type=Path)
    rep = sub.add_parser("report", help="顯示還原度報告")
    rep.add_argument("job", type=Path)
    args = parser.parse_args(argv)

    def progress(e: dict) -> None:
        if e["status"] != "done":
            page = f" 第 {e['page'] + 1} 頁" if e.get("page") is not None else ""
            print(f"  {'（沿用）' if e['status'] == 'cached' else ''}階段 {e['stage']} {e['stage_name']}{page}", file=sys.stderr)

    try:
        if args.cmd == "run":
            job = args.job or Path(tempfile.mkdtemp(prefix="mortise-job-"))
            pipe = Pipeline(job, on_event=progress)
            outputs = [OutputFormat(f) for f in (args.format or ["pptx"])]
            pipe.start(args.file, args.file.name, Intent(outputs=outputs, known_size=args.size))
            report = pipe.run()
            written = pipe.export_to(args.out)
            print(_summary(report, pipe))
            print("已寫出：" + "、".join(str(p) for p in written))
            print(f"工作目錄：{job}")
        elif args.cmd == "correct":
            pipe = Pipeline(args.job, on_event=progress)
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError:
                value = args.value
            page = args.page - 1 if args.page else None
            pipe.add_corrections([user_correction(args.stage, args.field, value, args.element, page, n=len(pipe.corrections))])
            report = pipe.run()
            if args.out:
                pipe.export_to(args.out)
            print(_summary(report, pipe))
        else:
            pipe = Pipeline(args.job)
            print(_summary(pipe.store.load("s7/fidelity_report", FidelityReport), pipe))
    except PipelineError as e:
        print(f"無法處理：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
