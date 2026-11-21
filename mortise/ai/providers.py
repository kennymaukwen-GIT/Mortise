"""供應商轉接層：每個供應商一個類別，提供同一個 complete() 介面。更換供應商不必改任何階段。

AnthropicProvider 使用官方 anthropic SDK：
- 結構化輸出：output_config.format（json_schema），回答保證是符合綱要的 JSON。
- 拒答時的退路：server-side fallbacks（"default" 模式，依拒答類別自動改由其他模型回答）。
- 影像長邊上限 2576 px；超過就先縮圖（dispatcher 處理）。
- 資料保留：Claude Opus 5.5 可在零資料保留（ZDR）下使用；Fable 與 Mythos 系列需要 30 天資料保留。
  若服務承諾使用者文件不留存，組織需要 ZDR 安排，且不能改用批次處理（批次的資料保留不同）。
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol


@dataclass
class Completion:
    data: Optional[dict]  # 解析後的 JSON；拒答、截斷或格式錯誤時為 None
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str
    latency_s: float
    error: Optional[str] = None


class Provider(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...

    def complete(self, system: str, text: str, image_png: Optional[bytes], schema: dict, max_tokens: int) -> Completion: ...


class NullProvider:
    """沒有設定供應商或沒有憑證：所有呼叫直接回傳 None，流水線沿用規則的結果。"""

    name = "none"
    model = "none"

    def available(self) -> bool:
        return False

    def complete(self, system, text, image_png, schema, max_tokens) -> Completion:  # pragma: no cover
        return Completion(None, 0, 0, self.model, "unavailable", 0.0, "沒有可用的 AI 供應商")


class FakeProvider:
    """測試用：由一個函數根據問題產生回答。"""

    name = "fake"

    def __init__(self, fn: Callable[[str, str, Optional[bytes], dict], Optional[dict]], model: str = "fake-model"):
        self.fn = fn
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def complete(self, system, text, image_png, schema, max_tokens) -> Completion:
        self.calls.append({"system": system, "text": text, "has_image": image_png is not None, "schema": schema})
        data = self.fn(system, text, image_png, schema)
        return Completion(data, 1000 + (2000 if image_png else 0), 200, self.model, "end_turn" if data is not None else "refusal", 0.01)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", timeout_s: float = 120.0, fallbacks: Optional[str] = "default"):
        self.model = model
        self.effort = effort
        self.timeout_s = timeout_s
        self.fallbacks = fallbacks
        self._client = None

    def available(self) -> bool:
        """有憑證才算可用：環境變數，或 `ant auth login` 存下的設定檔。"""
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        return (Path.home() / ".config" / "anthropic").exists()

    def _get(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=self.timeout_s, max_retries=2)
        return self._client

    def complete(self, system, text, image_png, schema, max_tokens) -> Completion:
        import anthropic

        content: list[dict] = []
        if image_png is not None:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(image_png).decode("ascii")}})
        content.append({"type": "text", "text": text})
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if self.fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = self.fallbacks
        t0 = time.time()
        try:
            resp = self._get().beta.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            return Completion(None, 0, 0, self.model, "rate_limited", time.time() - t0, f"rate limited: {e.message}")
        except anthropic.APIStatusError as e:
            return Completion(None, 0, 0, self.model, "api_error", time.time() - t0, f"{e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return Completion(None, 0, 0, self.model, "connection_error", time.time() - t0, str(e))
        latency = time.time() - t0
        usage = resp.usage
        stop = resp.stop_reason or ""
        data = None
        err = None
        if stop == "refusal":
            err = "模型拒答"
        elif stop == "max_tokens":
            err = "回答被截斷"
        else:
            text_out = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                data = json.loads(text_out)
            except json.JSONDecodeError as e:
                err = f"回答不是 JSON：{e}"
        return Completion(data, int(usage.input_tokens or 0), int(usage.output_tokens or 0), getattr(resp, "model", self.model), stop, latency, err)


def make_provider(cfg: dict) -> Provider:
    kind = cfg.get("provider", "anthropic")
    if not cfg.get("enabled", True) or kind == "none":
        return NullProvider()
    if kind == "anthropic":
        p = AnthropicProvider(model=cfg.get("model", "claude-opus-5-5"), effort=cfg.get("effort", "medium"), timeout_s=float(cfg.get("timeout_s", 120)), fallbacks=cfg.get("fallbacks", "default") or None)
        return p if p.available() else NullProvider()
    return NullProvider()
