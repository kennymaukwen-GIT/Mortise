"""來源文件 SourceDoc（①輸入 交出）。"""

from __future__ import annotations

from typing import Optional

from .common import SCHEMA_VERSION, BlobRef, InputType, Measured, Model, OutputFormat, PhysicalSize, UsageMode


class Intent(Model):
    """使用者意圖：要什麼輸出、用途、已知尺寸、語言。"""

    outputs: list[OutputFormat] = [OutputFormat.PPTX]
    mode: UsageMode = UsageMode.RESCUE
    known_size: Optional[PhysicalSize] = None  # 使用者已知的實際尺寸（永遠優先）
    target_size: Optional[PhysicalSize] = None  # 使用者指定的輸出尺寸（例如 A3）：⑤把整頁等比例縮放到這個尺寸並置中；不指定就維持原始尺寸
    languages: list[str] = ["zh-Hant", "en"]


class FakeNativeFlags(Model):
    """假原生的初判（便宜的檢查；細判在③）。"""

    hidden_text_ratio: float  # 不可見文字（多半是 OCR 文字層）佔全部字元的比例
    unicode_error_ratio: float  # 缺 Unicode 對照的字元比例
    outlined_text_suspected: bool  # 沒有文字但路徑很多：文字可能已轉外框


class PageRef(Model):
    page_index: int  # 從 0 起算
    input_type: Measured[InputType]
    char_count: int = 0  # 檔案中真正的文字字元數（不含 PDFium 自行補上的空白與換行）
    image_count: int = 0
    path_count: int = 0
    largest_image_coverage: float = 0.0  # 最大一張圖佔頁面面積的比例
    pixel_size: Optional[tuple[int, int]] = None  # 影像輸入的寬高（像素）
    dpi_hint: Optional[float] = None  # 檔案內標記的 dpi（只當參考）
    fake_native: Optional[FakeNativeFlags] = None


class SourceDoc(Model):
    schema_version: str = SCHEMA_VERSION
    doc_id: str  # 原始檔內容的 SHA-256（重複檔案偵測與快取用）
    filename: str
    media_type: str  # application/pdf、image/png、image/jpeg
    source: BlobRef
    metadata: dict[str, str] = {}  # PDF 的 Producer、Creator 等
    intent: Intent = Intent()
    pages: list[PageRef]
    warnings: list[str] = []
