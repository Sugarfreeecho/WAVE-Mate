from __future__ import annotations

import hashlib
from pathlib import Path


class BlobStore:
    """Content-addressed store for large text payloads."""

    def __init__(self, session_dir: str | Path):
        self.session_dir = Path(session_dir)

    def put_text(self, text: str, suffix: str = ".txt") -> dict:
        data = str(text or "").encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        suffix = suffix if suffix.startswith(".") else f".{suffix}"
        rel = Path("blobs") / f"{digest}{suffix}"
        path = self.session_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return {
            "blob_ref": rel.as_posix(),
            "sha256": digest,
            "bytes": len(data),
        }

    def read_text(self, blob_ref: str) -> str:
        rel = Path(str(blob_ref or ""))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("invalid blob_ref")
        path = self.session_dir / rel
        return path.read_text(encoding="utf-8")
