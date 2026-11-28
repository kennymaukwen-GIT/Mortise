"""回饋與引擎版本的 API（掛在 app.py：app.include_router(feedback_router)）。

    GET  /api/jobs/{id}/feedback          這份工作已送出的回饋（評分、意見、標籤）、修正單數量、是否同意用於改善辨識
    GET  /api/jobs/{id}/feedback/export   下載回饋包（zip）：原始檔、回饋（mortise.feedback/1）、還原度報告、白話說明
    GET  /api/engine                      目前的引擎版本、組成、最新成績單的摘要

送出回饋仍是 app.py 的 POST /api/jobs/{id}/feedback（rating、page、element_id、correct、comment、tags），格式不變。
回饋包是使用者自己的資料：匯出不需要「同意用於改善辨識」。內容與用法見 mortise/eval/feedback.py 的「三之一」與 docs/training.md。

修正單「系統當時的值」從哪裡來（依序）：工作目錄 feedback/ 裡的修正單快照（有的話）、回饋資料庫裡這份工作的
.feedback.json（使用者同意時才有）。都沒有的修正單在回饋包裡記進 snapshot_missing，不拿來校準。
「這個元素是對的／錯的」與對元素的「回報錯誤」，在匯出時從工作目錄的產物查出元素當時的值與信心值（⑦報告的元素信心值，網頁上顯示的那個）。
GET /api/engine 只回檔名，不回伺服器上的完整路徑與其他軟體的版本。
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

router = APIRouter()


def _jobs():
    """app.py 的工作管理員（在呼叫時才取：測試會 reload app 模組，換成新的 JobManager）。"""
    from . import app as appmod

    return appmod.JOBS


def _cfg():
    from . import app as appmod

    return appmod.CFG


def _pipe(job_id: str):
    try:
        return _jobs().pipeline(job_id)
    except KeyError:
        raise HTTPException(404, "找不到這份工作（可能已過保留期限而刪除）。")


def _attachment(name: str) -> str:
    """Content-Disposition：中文檔名用 RFC 5987 的 filename*，另附只有 ASCII 的後備檔名。"""
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _dataset_feedback(pipe, job_id: str) -> Optional[Path]:
    """同意用於改善辨識時，回饋資料庫裡這份工作的 .feedback.json（api/jobs.py 的 _to_dataset 寫的）。"""
    root = getattr(_jobs(), "feedback_root", None)
    if root is None:
        return None
    name = f"{job_id}__{pipe.state.get('filename', 'upload')}"
    path = Path(root) / "samples" / (Path(name).stem + ".feedback.json")
    return path if path.exists() else None


def _job_feedback(pipe, job_id: str):
    from ..artifacts import Correction
    from ..eval.feedback import correction_snapshots, feedback_from_job, job_feedback_records

    records = job_feedback_records(pipe.dir)
    sources = [pipe.dir / "feedback"]
    ds = _dataset_feedback(pipe, job_id)
    if ds is not None:
        sources.append(ds)
    snaps = correction_snapshots(*sources)
    corrections = [Correction.model_validate(c) for c in pipe.state.get("corrections", [])]
    created = pipe.state.get("created_at")
    created_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(created)) if isinstance(created, (int, float)) else ""
    filename = pipe.state.get("filename") or "upload"
    job = {"job_id": job_id, "filename": filename, "consented": bool(pipe.state.get("allow_training", False))}
    return records, feedback_from_job(filename, records, corrections, snaps, job, _engine_brief(), created_iso, _resolver(pipe, records))


def _resolver(pipe, records: list[dict[str, Any]]):
    """查元素當時的值與信心值（只有使用者標過「對／錯」或回報過錯誤的元素才需要；讀產物要一點時間，所以用到才讀）。"""
    if not any(r.get("element_id") and (r.get("correct") is not None or r.get("tags")) for r in records):
        return None
    from ..eval.feedback import resolve_value
    from ..eval.runner import load_artifacts

    try:
        arts = load_artifacts(pipe)
    except Exception:  # noqa: BLE001 —— 產物讀不出來（例如還在處理中）：照樣匯出，只是沒有信心值
        return None
    return lambda stage, field, eid, page: resolve_value(arts, stage, field, eid, page)


@router.get("/api/jobs/{job_id}/feedback")
def list_feedback(job_id: str) -> dict[str, Any]:
    """已送出的回饋（依時間排序，每筆有 id 與 created）、修正單數量、是否同意用於改善辨識。"""
    pipe = _pipe(job_id)
    from ..eval.feedback import job_feedback_records

    return {"items": job_feedback_records(pipe.dir), "corrections": len(pipe.state.get("corrections", [])),
            "consented": bool(pipe.state.get("allow_training", False))}


@router.get("/api/jobs/{job_id}/feedback/export")
def export_feedback(job_id: str) -> FileResponse:
    """回饋包（application/zip，檔名 <原檔名>.mortise-feedback.zip）。"""
    pipe = _pipe(job_id)
    from ..artifacts import FidelityReport
    from ..eval.feedback import BUNDLE_SUFFIX, write_bundle

    source = pipe.dir / pipe.state.get("source", "")
    if not pipe.state.get("source") or not source.is_file():
        raise HTTPException(409, "這份工作的原始檔不見了，無法匯出。")
    _, fb = _job_feedback(pipe, job_id)
    report = None
    if pipe.store.has("s7/fidelity_report"):
        try:
            report = pipe.store.load("s7/fidelity_report", FidelityReport).model_dump(mode="json", exclude={"target_render"})
        except Exception:  # noqa: BLE001 —— 報告讀不出來（例如格式升級中）：回饋包照樣匯出，只是沒有報告
            report = None
    filename = pipe.state.get("filename") or source.name
    fd, tmp = tempfile.mkstemp(prefix="mortise-feedback-", suffix=".zip")
    os.close(fd)
    write_bundle(Path(tmp), source, filename, fb, report)
    name = Path(filename).stem + BUNDLE_SUFFIX
    return FileResponse(tmp, media_type="application/zip", headers={"Content-Disposition": _attachment(name)},
                        background=BackgroundTask(lambda: Path(tmp).unlink(missing_ok=True)))


# ====================================================================== 引擎版本

_ENGINE: dict[str, Any] = {}
_ENGINE_LOCK = threading.Lock()


def _engine_info() -> dict[str, Any]:
    """引擎版本與組成（算一次要讀約 30 MB 的模型檔雜湊，所以依設定內容快取；設定或程式改了要重新啟動伺服器才會生效，版本也跟著重算）。"""
    from .. import engine

    cfg = _cfg()
    try:  # UVDoc 可能在伺服器啟動後才在背景下載完成：它的狀態也算進快取鍵（只讀一個小檔，很快）
        uv = engine._uvdoc_component(cfg).get("tag")
    except Exception:  # noqa: BLE001
        uv = None
    key = engine._digest([cfg.data, uv], 16)
    with _ENGINE_LOCK:
        hit = _ENGINE.get(key)
    if hit is None:
        hit = engine.describe(cfg)
        with _ENGINE_LOCK:
            _ENGINE.clear()
            _ENGINE[key] = hit
    return hit


def _engine_brief() -> dict[str, Any]:
    try:
        info = _engine_info()
        return {"version": info["version"], "name": info["name"]}
    except Exception:  # noqa: BLE001 —— 引擎版本算不出來不影響匯出
        return {}


@router.get("/api/engine")
def engine_info() -> dict[str, Any]:
    """目前的引擎版本（規則＋門檻＋模型檔組成的版本字串）、好記的短名、組成明細、最新成績單的摘要（沒有成績單時為 null）。"""
    from ..eval.scorecard import latest_summary

    info = _engine_info()
    try:
        card = latest_summary(info["version"])
    except Exception:  # noqa: BLE001
        card = None
    return {"version": info["version"], "name": info["name"], "components": _public(info["components"]), "scorecard": card}


def _public(comp: dict[str, Any]) -> dict[str, Any]:
    """給網頁看的組成：拿掉伺服器上的完整路徑（自己換上的模型只留檔名）與其他軟體的版本（verify_engine）。"""
    import copy

    out = copy.deepcopy(comp)
    out.pop("verify_engine", None)
    for f in (out.get("ocr") or {}).get("files", {}).values():
        if isinstance(f, dict):
            f.pop("path", None)
    return out
