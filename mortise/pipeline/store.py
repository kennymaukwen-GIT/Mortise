"""產物儲存區：每份工作一個目錄。產物存成 JSON，影像與檔案以內容雜湊為名。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from ..artifacts import BlobRef

M = TypeVar("M", bound=BaseModel)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
            path.write_bytes(data)
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
        self._artifact_path(name).write_text(text, encoding="utf-8")
        digest = sha256_bytes(text.encode("utf-8"))[:16]
        self.manifest[name] = {"key": cache_key, "hash": digest}
        self._manifest_path.write_text(json.dumps(self.manifest, indent=1), encoding="utf-8")
        return digest

    def load(self, name: str, cls: Type[M]) -> M:
        return cls.model_validate_json(self._artifact_path(name).read_text(encoding="utf-8"))

    def has(self, name: str) -> bool:
        return name in self.manifest and self._artifact_path(name).exists()

    def hash_of(self, name: str) -> str:
        return self.manifest[name]["hash"]

    def key_of(self, name: str) -> str:
        return self.manifest.get(name, {}).get("key", "")
