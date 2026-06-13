from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class SessionRepository:
    """Small metadata repository for Runtime V2 projections.

    This is a cache/projection layer. The event log remains the fact source.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def metadata_path(self, session_id: str) -> Path:
        return self.root / self._safe_id(session_id) / "metadata.json"

    def read_metadata(self, session_id: str) -> Dict[str, Any]:
        path = self.metadata_path(session_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}

    def write_metadata(self, session_id: str, metadata: Dict[str, Any]) -> None:
        path = self.metadata_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    @staticmethod
    def _safe_id(session_id: str) -> str:
        safe = str(session_id or "").strip()
        if not safe or any(part in safe for part in ("/", "\\", "..")):
            raise ValueError("invalid session_id")
        return safe
