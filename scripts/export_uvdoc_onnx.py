"""開發用：以 torch 官方匯出 UVDoc 的 ONNX，與產品裡不需要 torch 的組裝（mortise.recognition.dewarp.build_uvdoc_onnx）交叉比對。

torch 只在這支腳本裡用，不是產品相依；產品在第一次使用時自己下載權重、在本機組 ONNX（見 dewarp.py 開頭說明）。

    python scripts/export_uvdoc_onnx.py UVDoc 原始碼目錄 [輸出.onnx] [測試影像.png]

UVDoc 原始碼目錄：git clone https://github.com/tanguymagne/UVDoc（MIT 授權），並 checkout 到 dewarp.UVDOC_COMMIT。
輸出兩個網格的最大差（正規化座標，網格範圍 [-1, 1]）與推論時間。研究時量到的差是 2.4e-7。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    repo = Path(argv[1])
    out = Path(argv[2]) if len(argv) > 2 else Path("uvdoc_torch_export.onnx")
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import numpy as np
    import onnxruntime as ort
    import torch
    from model import UVDocnet  # UVDoc 倉庫裡的 model.py

    from mortise.recognition import dewarp as D

    pkl = repo / "model" / "best_model.pkl"
    if D._sha256_file(pkl) != D.UVDOC_SHA256:
        print(f"權重的 SHA-256 與 dewarp.UVDOC_SHA256 不同：{pkl}")
        return 1
    net = UVDocnet(num_filter=32, kernel_size=5)
    net.load_state_dict(torch.load(pkl, map_location="cpu", weights_only=False)["model_state"])
    net.eval()
    w, h = D.UVDOC_INPUT
    torch.onnx.export(net, torch.zeros(1, 3, h, w), str(out), input_names=["img"], output_names=["grid2d", "grid3d"], opset_version=17, dynamo=False)
    ours = D.build_uvdoc_onnx(D.load_torch_state_dict(pkl))
    if len(argv) > 3:
        import cv2

        img = cv2.cvtColor(cv2.imread(argv[3]), cv2.COLOR_BGR2RGB)
        x = (cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0).transpose(2, 0, 1)[None].copy()
    else:
        x = np.random.default_rng(0).random((1, 3, h, w), dtype=np.float32)
    a = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    b = ort.InferenceSession(ours, providers=["CPUExecutionProvider"])
    with torch.no_grad():
        g_torch = net(torch.from_numpy(x))[0].numpy()
    t0 = time.time()
    g_a = a.run(None, {"img": x})[0]
    ta = time.time() - t0
    t0 = time.time()
    g_b = b.run(None, {"img": x})[0]
    tb = time.time() - t0
    print(f"torch 匯出 ↔ torch：{np.abs(g_a - g_torch).max():.2e}；本機組裝 ↔ torch：{np.abs(g_b - g_torch).max():.2e}")
    print(f"推論時間：torch 匯出 {ta:.3f} 秒、本機組裝 {tb:.3f} 秒（第一次執行含暖機）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
