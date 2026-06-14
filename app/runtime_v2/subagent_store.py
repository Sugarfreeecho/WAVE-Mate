from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .event_log import SessionEventLog
from .event_schema import RuntimeEvent
from .projector import RuntimeProjector
from .snapshot_store import SnapshotStore


class RuntimeSubagentStore:
    """Parent-local subagent event storage.

    Layout:
      {parent_session}/subagents/{agent_id}/events.jsonl
      {parent_session}/subagents/{agent_id}/snapshots/latest.json
      {parent_session}/subagents/{agent_id}/metadata.json
    """

    def __init__(self, sessions_dir: str | Path):
        self.sessions_dir = Path(sessions_dir)
        self.projector = RuntimeProjector()

    def root_for_parent(self, parent_session_id: str) -> Path:
        return self.sessions_dir / self._safe_id(parent_session_id) / "subagents"

    def agent_dir(self, parent_session_id: str, agent_id: str) -> Path:
        return self.root_for_parent(parent_session_id) / self._safe_id(agent_id)

    def append_event(self, parent_session_id: str, agent_id: str, event_type: str, payload: Optional[dict] = None, run_id: Optional[str] = None) -> RuntimeEvent:
        root = self.root_for_parent(parent_session_id)
        log = SessionEventLog(root)
        event = log.append(agent_id, event_type, payload=payload or {}, run_id=run_id)
        snapshots = SnapshotStore(root)
        snapshot = self.projector.project_incremental(snapshots.read(agent_id), event)
        snapshots.write(agent_id, snapshot)
        return event

    def write_metadata(self, parent_session_id: str, agent_id: str, metadata: dict) -> None:
        path = self.agent_dir(parent_session_id, agent_id) / "metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def read_snapshot(self, parent_session_id: str, agent_id: str) -> dict:
        return SnapshotStore(self.root_for_parent(parent_session_id)).read(agent_id)

    @staticmethod
    def _safe_id(value: str) -> str:
        safe = str(value or "").strip()
        if not safe or any(part in safe for part in ("/", "\\", "..")):
            raise ValueError("invalid id")
        return safe
