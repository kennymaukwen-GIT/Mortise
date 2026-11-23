"""頁面規格 PageSpec（②頁面 交出）。"""

from __future__ import annotations

from typing import Optional

from .common import SCHEMA_VERSION, BlobRef, Measured, Model, Point


class PageSpec(Model):
    schema_version: str = SCHEMA_VERSION
    page_index: int

    # ---- 幾何（③只依賴這一段；改尺寸不會動到它）----
    source_corners: list[Point]  # 頁面四角在來源座標中的位置（PDF 為 pt，影像為像素）：左上、右上、右下、左下
    aspect_ratio: float  # 高 ÷ 寬
    source_to_page: list[float]  # 3×3 矩陣（列優先 9 個數）：來源座標 → 頁面相對座標
    rotation_applied: int = 0  # 已套用的頁面旋轉（度）
    box_used: Optional[str] = None  # PDF 採用的頁框：crop、media、trim
    working_image: BlobRef  # 攤平後的工作影像
    working_image_size: tuple[int, int]  # 寬、高（像素）

    # ---- 尺度（只有⑤之後才用）----
    page_width_pt: Measured[float]  # 比例尺：頁寬 1 單位 = 幾 pt
    effective_resolution: float  # 工作影像中每 pt 有幾個像素

    notes: list[str] = []
    color_image: Optional[BlobRef] = None  # 攤平後、未增強的影像（量顏色用）；沒有就用 working_image
    size_candidates: list[dict] = []  # 尺寸候選（名稱、寬 pt、高 pt、信心值、依據），前兩名接近時請使用者選
    quad_method: str = ""  # 頁面四角怎麼來的：pdf_box、edge_fit、contour、full_image、user
    quad_confidence: float = 1.0
    enhanced: bool = False  # 工作影像做過白平衡與去光照（白紙類頁面）
    # ---- 照片的頁面偵測細節（給前端「拖曳四角修正」與「選哪一張紙」用；PDF 與截圖留預設值）----
    quad_status: str = "ok"  # ok、low_contrast（邊界對比低）、curved_edge（紙邊彎曲）、multiple_pages（有好幾張紙）、cut_off（紙被畫面切掉）、not_found（找不到，用整張圖）
    quad_corner_error_px: list[float] = []  # 四角各自的位置誤差（來源影像像素，約一個標準差）：左上、右上、右下、左下
    quad_alternatives: list[list[Point]] = []  # 其他候選的頁面四角（來源座標，每組 4 點，依分數由高到低）：例如照片裡的第二張紙
    source_outline: list[Point] = []  # 紙邊彎曲時的頁面外框（來源座標，沿外框依序取點）；平的紙留空，用 source_corners
    dewarp_map: Optional[BlobRef] = None  # 彎曲的紙攤平時：工作影像每個像素對應到來源影像哪裡（npz：map_x、map_y，float32）；沒有攤平就留空
    dewarp_source: str = ""  # 攤平的依據：measured（由紙邊、格線、文字行量出）、uvdoc_prior（學習式模型給的初值再量測修正）；沒有攤平就留空
    dewarp_residual_px: Optional[float] = None  # 攤平後格線與文字行離直線的殘差（工作影像像素，第 95 百分位）

    @property
    def page_height_pt(self) -> float:
        return self.page_width_pt.value * self.aspect_ratio
