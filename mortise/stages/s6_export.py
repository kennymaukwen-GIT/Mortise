"""⑥ 輸出：收成品文件，交出各格式的檔案 OutputFile。

每種格式一個獨立的輸出器（mortise/exporters/ 下各一個模組），登記在 EXPORTERS。
新增格式只要加一個模組並在這裡登記，不必動其他階段。
"""

from __future__ import annotations

import importlib
from typing import Callable

from ..artifacts import OutputFile, OutputFormat, StyledDoc
from ..config import Config
from ..pipeline.store import Store

NAME = "s6"
VERSION = "2"

# 格式 → 輸出器模組（mortise.exporters 底下的模組名稱）
EXPORTERS: dict[OutputFormat, str] = {
    OutputFormat.PPTX: "pptx",
    OutputFormat.GOOGLE_SLIDES: "google_slides",
    OutputFormat.DOCX_FIXED: "docx_fixed",
    OutputFormat.DOCX_FLOW: "docx_flow",
    OutputFormat.XLSX: "xlsx",
    OutputFormat.AI_PDF: "ai_pdf",
    OutputFormat.IDML: "idml",
    OutputFormat.TEMPLATE: "template",
}


class ExportNotImplemented(Exception):
    pass


def exporter(fmt: OutputFormat) -> Callable[[StyledDoc, str, Config, Store], OutputFile]:
    name = EXPORTERS.get(fmt)
    try:
        module = importlib.import_module(f"..exporters.{name}", __package__) if name else None
    except ModuleNotFoundError as e:
        if name and e.name and e.name.endswith(f"exporters.{name}"):
            module = None
        else:
            raise
    if module is None or not hasattr(module, "export"):
        raise ExportNotImplemented(f"輸出格式 {fmt.value} 尚未實作。")
    return module.export


def available() -> list[OutputFormat]:
    out = []
    for fmt in EXPORTERS:
        try:
            exporter(fmt)
            out.append(fmt)
        except ExportNotImplemented:
            pass
    return out


def run(doc: StyledDoc, fmt: OutputFormat, basename: str, cfg: Config, store: Store) -> OutputFile:
    return exporter(fmt)(doc, basename, cfg, store)
