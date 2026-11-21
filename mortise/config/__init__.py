"""設定檔：載入、覆寫、取版本雜湊。"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

_DEFAULT = Path(__file__).with_name("default.toml")


class Config:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, override: Path | None = None) -> "Config":
        data = tomllib.loads(_DEFAULT.read_text(encoding="utf-8"))
        if override:
            extra = tomllib.loads(Path(override).read_text(encoding="utf-8"))
            for section, values in extra.items():
                if isinstance(values, dict) and isinstance(data.get(section), dict):
                    data[section].update(values)
                else:
                    data[section] = values
        return cls(data)

    def section(self, name: str) -> dict[str, Any]:
        return self.data.get(name, {})

    def key(self, *sections: str) -> str:
        """指定幾節設定的內容雜湊：設定沒變，快取就不失效。"""
        payload = {s: self.data.get(s, {}) for s in sections}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
