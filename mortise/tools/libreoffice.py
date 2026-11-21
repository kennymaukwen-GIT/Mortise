"""用 LibreOffice 無介面模式把 Office 檔轉成 PDF（供⑦目標驗證用）。

LibreOffice 為 MPL-2.0。它是替身引擎，不等於使用者手上的 PowerPoint 或 Word（風險 R1），
所以用它量出來的分數必須在還原度報告中註明引擎。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class RenderError(Exception):
    pass


def available() -> bool:
    return shutil.which("soffice") is not None


def version() -> str:
    try:
        return subprocess.run(["soffice", "--version"], capture_output=True, text=True, timeout=30).stdout.strip().split("\n")[0]
    except Exception:
        return "LibreOffice"


def to_pdf(source: Path, timeout_s: int = 120) -> bytes:
    """轉成 PDF 並回傳內容。每次用獨立的設定檔目錄，互不干擾。"""
    with tempfile.TemporaryDirectory(prefix="mortise-lo-") as tmp:
        profile = Path(tmp) / "profile"
        out = Path(tmp) / "out"
        out.mkdir()
        cmd = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out), str(source)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as e:
            raise RenderError("LibreOffice 轉檔逾時。") from e
        pdfs = list(out.glob("*.pdf"))
        if not pdfs:
            raise RenderError("LibreOffice 沒有產生 PDF。")
        return pdfs[0].read_bytes()
