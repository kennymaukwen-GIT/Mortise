"""整個測試套件共用的設定。

測試一律不連網下載模型權重：每個測試預設設 MORTISE_OFFLINE=1（UVDoc 有快取就用、沒有就不用）。
dewarp.py 在 pytest 裡本來就不下載，這是第二道保險。要測下載流程的測試，先把下載函數換成假的，
再在測試裡 monkeypatch.delenv("MORTISE_OFFLINE") 並設 MORTISE_ALLOW_DOWNLOAD=1。
不改 MORTISE_CACHE_DIR：那會讓字型快取和已經有的 UVDoc 快取失效。
"""

import pytest


@pytest.fixture(autouse=True)
def _no_network_downloads(monkeypatch):
    monkeypatch.setenv("MORTISE_OFFLINE", "1")
    yield
