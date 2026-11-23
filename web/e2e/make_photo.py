"""端對端測試用的合成照片（不放任何真實照片）：把虛構的 tests/fixtures 文件用 mortise/eval/synth.py 拍成手機照片。

    python3 web/e2e/make_photo.py 輸出.jpg [cut|flat] [種子]

cut：照片下緣切掉紙的一部分（紙超出畫面 → ②會請使用者確認四角，網頁出現黃色提示條）。
flat：整張紙都在畫面裡（通常不必確認）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mortise.eval import synth  # noqa: E402


def make(out: Path, mode: str = "cut", seed: int = 7) -> dict:
    page, _ = synth.render_pdf_page(REPO / "tests" / "fixtures" / "doc_a4.pdf", 0, dpi=110.0)
    photo, matrix, info = synth.degrade(page, "photo", seed, strength=0.6)
    h, w = page.shape[:2]
    corners = synth.apply_h(matrix, np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64))
    if mode == "cut":
        # 切到紙的下緣與左下角之間：下面兩個角都落在畫面外
        bottom = float(min(corners[2][1], corners[3][1]))
        top = float(max(corners[0][1], corners[1][1]))
        keep = int(round(top + 0.86 * (bottom - top)))
        photo = photo[:keep]
    Image.fromarray(np.ascontiguousarray(photo)).save(out, "JPEG", quality=88)
    return {"size": [int(photo.shape[1]), int(photo.shape[0])], "corners": corners.round(1).tolist()}


if __name__ == "__main__":
    target = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "cut"
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    target.parent.mkdir(parents=True, exist_ok=True)
    print(make(target, mode, seed))
