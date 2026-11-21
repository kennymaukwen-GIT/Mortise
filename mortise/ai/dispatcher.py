"""AI 調度器：所有大型 AI 呼叫的唯一入口。

負責：
- 編號標記圖（見 marks.py）與問題的組合，送給供應商（見 providers.py）。
- 強制結構化回答（JSON 綱要），再用每個任務自己的 validate() 檢查合法性：編號存在、沒有重複、沒有遺漏、
  標籤在允許的清單中。不合法就把錯誤告訴模型重問一次；仍不合法就放棄（回傳 None），呼叫端沿用規則的結果。
- 快取：同一張圖、同一個問題、同一個模型與提示詞版本，只問一次（重跑流水線不重複花錢）。
- 額度：每頁最多幾次、每份文件最多花多少美元；超過就不再問。
- 紀錄：每次呼叫寫一行到 calls.jsonl（任務、模型、token 數、成本、是否合法、耗時），不記錄影像內容。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .providers import Completion, NullProvider, Provider, make_provider


@dataclass
class AiTask:
    name: str  # 任務名稱，例如 "structure.roles_order"
    prompt_version: str  # 提示詞版本：改提示詞就加一，快取自動失效
    system: str
    text: str
    schema: dict
    validate: Callable[[dict], Optional[str]]  # 回傳 None 表示合法，否則回傳錯誤說明
    image_png: Optional[bytes] = None
    page_index: Optional[int] = None
    max_tokens: int = 4000
    meta: dict = field(default_factory=dict)


class AiDispatcher:
    def __init__(self, cfg: dict, cache_dir: Optional[Path] = None, provider: Optional[Provider] = None):
        self.cfg = cfg
        self.provider = provider if provider is not None else make_provider(cfg)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.page_calls: dict[tuple, int] = {}  # （頁碼、任務）→ 次數：每個 AI 使用點各自有每頁的額度
        self.spent_usd = 0.0
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return not isinstance(self.provider, NullProvider) and self.provider.available()

    def feature(self, name: str) -> bool:
        """某個 AI 使用點是否開啟（設定檔 [ai] 中的開關）。"""
        return self.enabled and bool(self.cfg.get(name, True))

    # ---------- 額度與成本 ----------
    def _cost(self, c: Completion) -> float:
        return c.input_tokens / 1e6 * float(self.cfg.get("price_input_per_mtok", 4.0)) + c.output_tokens / 1e6 * float(self.cfg.get("price_output_per_mtok", 20.0))

    def _budget_ok(self, page: Optional[int], task: str) -> bool:
        if self.page_calls.get((page, task), 0) >= int(self.cfg.get("max_calls_per_page", 3)):
            return False
        return self.spent_usd < float(self.cfg.get("max_cost_per_document_usd", 0.5))

    # ---------- 快取 ----------
    def _key(self, task: AiTask) -> str:
        h = hashlib.sha256()
        for part in (self.provider.name, self.provider.model, task.name, task.prompt_version, task.system, task.text, json.dumps(task.schema, sort_keys=True)):
            h.update(part.encode("utf-8"))
            h.update(b"\0")
        if task.image_png is not None:
            h.update(hashlib.sha256(task.image_png).digest())
        return h.hexdigest()[:32]

    def _cache_get(self, key: str) -> Optional[dict]:
        if not self.cache_dir:
            return None
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def _cache_put(self, key: str, data: dict) -> None:
        if self.cache_dir:
            (self.cache_dir / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _log(self, record: dict) -> None:
        if self.cache_dir:
            with self.lock, (self.cache_dir / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---------- 主流程 ----------
    def ask(self, task: AiTask) -> Optional[dict]:
        """問一個問題。回傳合法的回答，或 None（AI 不可用、超出額度、回答一直不合法）。"""
        if not self.enabled:
            return None
        key = self._key(task)
        cached = self._cache_get(key)
        if cached is not None and task.validate(cached) is None:
            self._log({"t": time.time(), "task": task.name, "cached": True, "page": task.page_index})
            return cached
        text = task.text
        for attempt in range(2):
            with self.lock:
                if not self._budget_ok(task.page_index, task.name):
                    self._log({"t": time.time(), "task": task.name, "skipped": "budget", "page": task.page_index})
                    return None
                key2 = (task.page_index, task.name)
                self.page_calls[key2] = self.page_calls.get(key2, 0) + 1
            c = self.provider.complete(task.system, text, task.image_png, task.schema, task.max_tokens)
            cost = self._cost(c)
            with self.lock:
                self.spent_usd += cost
            problem = c.error or (task.validate(c.data) if c.data is not None else "沒有回答")
            self._log({
                "t": time.time(), "task": task.name, "page": task.page_index, "model": c.model, "attempt": attempt,
                "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "cost_usd": round(cost, 6),
                "latency_s": round(c.latency_s, 3), "stop_reason": c.stop_reason, "valid": problem is None, "problem": problem,
            })
            if problem is None:
                self._cache_put(key, c.data)
                return c.data
            if c.data is None:
                return None  # 拒答、截斷、連線錯誤：不重問
            text = task.text + f"\n\n上一次的回答不合法：{problem}\n請修正後重新回答，只能使用題目中出現的編號。"
        return None

    def summary(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "provider": self.provider.name, "model": self.provider.model, "calls": sum(self.page_calls.values()), "spent_usd": round(self.spent_usd, 4)}
