"""產物儲存區：每份工作一個目錄。產物存成 JSON，影像與檔案以內容雜湊為名。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from ..artifacts import BlobRef

M = TypeVar("M", bound=BaseModel)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """先寫到暫存檔再改名：其他執行緒（例如 API 查詢狀態）永遠不會讀到寫到一半的檔案。"""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.root / "manifest.json"
        self.manifest: dict[str, dict[str, str]] = {}
        if self._manifest_path.exists():
            self.manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))

    # ---- 二進位檔 ----
    def put_blob(self, data: bytes, media_type: str, ext: str) -> BlobRef:
        digest = sha256_bytes(data)
        path = self.root / "blobs" / f"{digest}.{ext}"
        if not path.exists():
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return BlobRef(sha256=digest, media_type=media_type, size=len(data), ext=ext)

    def blob_path(self, ref: BlobRef) -> Path:
        return self.root / "blobs" / f"{ref.sha256}.{ref.ext}"

    def read_blob(self, ref: BlobRef) -> bytes:
        return self.blob_path(ref).read_bytes()

    # ---- 產物 ----
    def _artifact_path(self, name: str) -> Path:
        path = self.root / "artifacts" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, name: str, artifact: BaseModel, cache_key: str = "") -> str:
        text = artifact.model_dump_json(indent=1)
        atomic_write_text(self._artifact_path(name), text)
        digest = sha256_bytes(text.encode("utf-8"))[:16]
        self.manifest[name] = {"key": cache_key, "hash": digest}
        atomic_write_text(self._manifest_path, json.dumps(self.manifest, indent=1))
        return digest

    def load(self, name: str, cls: Type[M]) -> M:
        return cls.model_validate_json(self._artifact_path(name).read_text(encoding="utf-8"))

    def has(self, name: str) -> bool:
        return name in self.manifest and self._artifact_path(name).exists()

    def hash_of(self, name: str) -> str:
        return self.manifest[name]["hash"]

    def key_of(self, name: str) -> str:
        return self.manifest.get(name, {}).get("key", "")
