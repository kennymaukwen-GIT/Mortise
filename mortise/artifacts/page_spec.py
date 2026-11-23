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
    # ---- 照片的頁面確認（②接上找紙與攤平後追加；給「拖曳四角確認」的介面與校準用；PDF 與截圖留預設值）----
    # quad_method 另有："frame_edges"（紙被畫面切掉：三條量到的紙邊＋畫面邊，預選、需確認）、"outline"（找不到四角，由紙邊量出的外框四角，預填、需確認）
    quad_needs_review: bool = False  # 需要請使用者確認頁面四角（status 不是 ok、信心低、某角誤差太大、或攤平的外框需要確認）
    quad_review_reason: str = ""  # 為什麼要確認（白話一句；不需要確認時為空字串）
    quad_alternative_scores: list[float] = []  # quad_alternatives 各組的分數（0 到 1，相對於最佳候選）
    quad_alternative_labels: list[str] = []  # quad_alternatives 各組的白話短標籤（例如「另一張紙」「三條邊＋畫面邊」）
    quad_preselect: Optional[int] = None  # 介面要預先選取的 quad_alternatives 索引（目前的四角就是那一組）；沒有就留空
    dewarp_needs_review: bool = False  # 攤平的外框建議請使用者確認（外框有推估的邊、紙超出四角、或四角是攤平時自己量的）
    dewarp_confidence: Optional[float] = None  # 攤平的信心值（0 到 1）；沒有嘗試攤平就留空
    glare_frac: Optional[float] = None  # 頁面上反光（過曝、看不到內容）的面積比例；沒有量就留空
    source_size: Optional[tuple[int, int]] = None  # 來源影像（照片套用 EXIF 方向後）的寬、高（像素）；PDF 留空

    @property
    def page_height_pt(self) -> float:
        return self.page_width_pt.value * self.aspect_ratio
