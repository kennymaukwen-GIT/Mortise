"""原生 PDF 自我監督：把原生 PDF 畫成影像，PDF 本身的內容就是標準答案。

    image, truth, spec = render_truth(pdf_path, page_index=0, dpi=150)

image 是 RGB 的 numpy 陣列；truth 是擷取產線讀出的元素清單（頁面相對座標）；
頁面相對座標乘上影像寬度（像素）就是像素座標。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from ..artifacts import ElementList, Intent, PageSpec
from ..config import Config
from ..pipeline.store import Store
from ..stages import s1_input, s2_page, s3_recognize


def render_truth(pdf_path: Path, page_index: int = 0, dpi: float = 150.0, cfg: Config | None = None) -> tuple[np.ndarray, ElementList, PageSpec]:
    cfg = cfg or Config.load()
    cfg.data["page"] = {**cfg.data["page"], "working_dpi": dpi}
    store = Store(Path(tempfile.mkdtemp(prefix="mortise-truth-")))
    doc = s1_input.run(Path(pdf_path), Path(pdf_path).name, Intent(), cfg, store)
    spec = s2_page.run(doc, page_index, cfg, [], store)
    truth = s3_recognize.run(doc, spec, cfg, [], store)
    image = np.asarray(Image.open(store.blob_path(spec.working_image)).convert("RGB"))
    return image, truth, spec
