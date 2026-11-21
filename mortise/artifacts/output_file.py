"""檔案 OutputFile（⑥輸出 交出）。"""

from __future__ import annotations

from .common import SCHEMA_VERSION, BlobRef, Model, OutputFormat


class Degradation(Model):
    """降級紀錄：成品文件中有、但該格式表達不了的東西，做了什麼替代。"""

    element_id: str
    concept: str  # 哪個概念
    action: str  # 做了什麼替代
    lossy: bool


class OutputFile(Model):
    schema_version: str = SCHEMA_VERSION
    format: OutputFormat
    file: BlobRef
    filename: str
    degradations: list[Degradation] = []
    fonts_used: list[str] = []  # 輸出檔中寫入的字型名稱（使用者電腦需要有）
    selfcheck_ok: bool = True
    selfcheck_notes: list[str] = []
