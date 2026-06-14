from __future__ import annotations

from pathlib import Path
from typing import Optional

from .event_log import SessionEventLog
from .event_schema import RuntimeEvent
from .projector import RuntimeProjector
from .snapshot_store import SnapshotStore


class RuntimeHistoryOps:
    """Append-only history operations for the V2 path.

    These operations do not rewrite events.jsonl. They append semantic events
    and let RuntimeProjector calculate the visible/model history.
    """

    def __init__(self, sessions_dir: str | Path):
        self.event_log = SessionEventLog(sessions_dir)
        self.projector = RuntimeProjector()
        self.snapshots = SnapshotStore(sessions_dir)

    def delete_message(self, session_id: str, target_seq: int, reason: str = "") -> RuntimeEvent:
        return self._append_and_snapshot(session_id, "message_deleted", {
            "target_seq": int(target_seq),
            "reason": reason,
        })

    def rewrite_message(self, session_id: str, target_seq: int, content: str, reason: str = "") -> RuntimeEvent:
        return self._append_and_snapshot(session_id, "message_rewritten", {
            "target_seq": int(target_seq),
            "content": content,
            "reason": reason,
        })

    def create_branch(self, session_id: str, source_session_id: str, branch_from_seq: int, name: str = "") -> RuntimeEvent:
        return self._append_and_snapshot(session_id, "history_branch_created", {
            "source_session_id": source_session_id,
            "branch_from_seq": int(branch_from_seq),
            "name": name,
        })

    def compact_history(
        self,
        session_id: str,
        *,
        summary: str,
        compacted_before_seq: int,
        reason: str = "",
    ) -> RuntimeEvent:
        return self._append_and_snapshot(session_id, "history_compacted", {
            "summary": summary,
            "compacted_before_seq": int(compacted_before_seq),
            "reason": reason,
        })

    def commit_context_summary(self, session_id: str, summary: str, source_seq: Optional[int] = None) -> RuntimeEvent:
        payload = {"summary": summary}
        if source_seq is not None:
            payload["source_seq"] = int(source_seq)
        return self._append_and_snapshot(session_id, "context_summary_committed", payload)

    def change_visible_range(self, session_id: str, *, from_seq: Optional[int] = None, to_seq: Optional[int] = None, reason: str = "") -> RuntimeEvent:
        payload = {"reason": reason}
        if from_seq is not None:
            payload["from_seq"] = int(from_seq)
        if to_seq is not None:
            payload["to_seq"] = int(to_seq)
        return self._append_and_snapshot(session_id, "visible_range_changed", payload)

    def change_model_window(self, session_id: str, *, from_seq: Optional[int] = None, to_seq: Optional[int] = None, reason: str = "") -> RuntimeEvent:
        payload = {"reason": reason}
        if from_seq is not None:
            payload["from_seq"] = int(from_seq)
        if to_seq is not None:
            payload["to_seq"] = int(to_seq)
        return self._append_and_snapshot(session_id, "model_window_changed", payload)

    def _append_and_snapshot(self, session_id: str, event_type: str, payload: dict) -> RuntimeEvent:
        event = self.event_log.append(session_id, event_type, payload=payload)
        snapshot = self.projector.project_incremental(self.snapshots.read(session_id), event)
        self.snapshots.write(session_id, snapshot)
        return event
