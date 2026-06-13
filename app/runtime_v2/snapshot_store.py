from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class SnapshotStore:
    """Rebuildable snapshot cache for faster refresh/debug reads."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, session_id: str) -> Path:
        safe_id = self._safe_id(session_id)
        return self.root / safe_id / "snapshots" / "latest.json"

    def read(self, session_id: str) -> Dict[str, Any]:
        path = self.path(session_id)
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, session_id: str, snapshot: Dict[str, Any]) -> None:
        path = self.path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    @staticmethod
    def _safe_id(session_id: str) -> str:
        safe = str(session_id or "").strip()
        if not safe or any(part in safe for part in ("/", "\\", "..")):
            raise ValueError("invalid session_id")
        return safe
