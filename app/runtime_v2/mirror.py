from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .blob_store import BlobStore
from .event_log import SessionEventLog
from .event_schema import RuntimeEvent
from .projector import RuntimeProjector
from .snapshot_store import SnapshotStore
from .subagent_store import RuntimeSubagentStore

logger = logging.getLogger(__name__)


class RuntimeMirror:
    """Synchronous compatibility bridge from legacy events to Runtime V2."""

    def __init__(self, sessions_dir: str | Path):
        self.sessions_dir = Path(sessions_dir)
        self.event_log = SessionEventLog(self.sessions_dir)
        self.projector = RuntimeProjector()
        self.snapshots = SnapshotStore(self.sessions_dir)
        self.subagents = RuntimeSubagentStore(self.sessions_dir)

    def mirror_ui_event(self, session_id: str, event: Dict[str, Any]) -> Optional[RuntimeEvent]:
        subagent_event = self._mirror_subagent_event(session_id, event)
        if subagent_event is not None:
            return subagent_event
        mapped = self._map_ui_event(session_id, event)
        if not mapped:
            return None
        return self.append(session_id, mapped["type"], mapped.get("payload") or {}, run_id=mapped.get("run_id"))

    def mirror_run_started(self, session_id: str, run_id: Optional[str] = None, payload: Optional[dict] = None) -> Optional[RuntimeEvent]:
        return self.append(session_id, "run_started", payload or {}, run_id=run_id)

    def mirror_run_finished(self, session_id: str, run_id: Optional[str] = None, payload: Optional[dict] = None) -> Optional[RuntimeEvent]:
        return self.append(session_id, "run_finished", payload or {}, run_id=run_id)

    def mirror_run_failed(self, session_id: str, error: str, run_id: Optional[str] = None, payload: Optional[dict] = None) -> Optional[RuntimeEvent]:
        data = {"error": error}
        if payload:
            data.update(payload)
        return self.append(session_id, "run_failed", data, run_id=run_id)

    def mirror_run_interrupted(self, session_id: str, run_id: Optional[str] = None, payload: Optional[dict] = None) -> Optional[RuntimeEvent]:
        return self.append(session_id, "run_interrupted", payload or {}, run_id=run_id)

    def append(self, session_id: str, event_type: str, payload: Optional[dict] = None, run_id: Optional[str] = None) -> Optional[RuntimeEvent]:
        try:
            event = self.event_log.append(session_id, event_type, payload=payload or {}, run_id=run_id)
            self._apply_snapshot_event(session_id, event)
            return event
        except Exception as exc:
            logger.debug("Runtime V2 mirror append failed for session %s: %s", session_id, exc)
            return None

    def _apply_snapshot_event(self, session_id: str, event: RuntimeEvent) -> None:
        try:
            snapshot = self.snapshots.read(session_id)
            snapshot = self.projector.project_incremental(snapshot, event)
            self.snapshots.write(session_id, snapshot)
        except Exception as exc:
            logger.debug("Runtime V2 mirror incremental snapshot failed for session %s: %s", session_id, exc)
            self._refresh_snapshot(session_id)

    def _refresh_snapshot(self, session_id: str) -> None:
        try:
            snapshot = self.projector.project(self.event_log.read_all(session_id))
            self.snapshots.write(session_id, snapshot)
        except Exception as exc:
            logger.debug("Runtime V2 mirror snapshot failed for session %s: %s", session_id, exc)

    def _map_ui_event(self, session_id: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_type = str((event or {}).get("type") or "")
        if not event_type:
            return None
        if event_type == "user":
            return {"type": "message_user", "payload": {"content": event.get("content") or ""}}
        if event_type == "final":
            return {"type": "message_assistant_final", "payload": {"content": event.get("content") or ""}}
        if event_type == "context_tokens":
            return {"type": "context_tokens", "payload": dict(event)}
        if event_type == "todo_plan":
            return {"type": "todo_updated", "payload": dict(event)}
        if event_type == "context_summary_body":
            return {
                "type": "context_summary_committed",
                "payload": {
                    "summary": event.get("content") or event.get("summary") or event.get("text") or "",
                    "source": "legacy_ui_event",
                },
            }
        if event_type == "context_summary_finished":
            return {
                "type": "legacy_compress_observed",
                "payload": {
                    "reason": event.get("reason") or event.get("mode") or "",
                    "source": "legacy_ui_event",
                },
            }
        if event_type in {"subagent_started", "subagent_progress", "subagent_finished", "subagent_failed", "subagent_result_consumed"}:
            return {"type": event_type, "payload": self._slim_subagent_payload(event)}
        if event_type in {"tool_call", "tool_result"}:
            mapped_type = "tool_finished" if event_type == "tool_result" else "tool_started"
            return {"type": mapped_type, "payload": self._externalize_large_text_payload(str(self.sessions_dir / str(session_id)), dict(event))}
        if event_type in {"status", "process_metrics", "cache_stats", "validate_final"}:
            return None
        return {"type": "legacy_ui_event", "payload": self._externalize_large_text_payload(str(self.sessions_dir / str(session_id)), dict(event))}

    def _mirror_subagent_event(self, session_id: str, event: Dict[str, Any]) -> Optional[RuntimeEvent]:
        event_type = str((event or {}).get("type") or "")
        if event_type not in {"subagent_started", "subagent_progress", "subagent_finished", "subagent_failed", "subagent_result_consumed"}:
            return None
        agent_id = str(event.get("agent_id") or event.get("task_id") or event.get("id") or "").strip()
        if not agent_id:
            return None
        try:
            sub_payload = self._externalize_large_text_payload(
                str(self.sessions_dir / str(session_id) / "subagents" / agent_id),
                dict(event),
            )
            self.subagents.append_event(session_id, agent_id, event_type, sub_payload)
        except Exception as exc:
            logger.debug("Runtime V2 mirror subagent event failed for session %s agent %s: %s", session_id, agent_id, exc)
        return self.append(session_id, event_type, self._slim_subagent_payload(event))

    def _slim_subagent_payload(self, event: Dict[str, Any]) -> Dict[str, Any]:
        keep = {
            "agent_id",
            "task_id",
            "id",
            "session_id",
            "child_session_id",
            "status",
            "has_final",
            "result_consumed",
            "name",
            "title",
            "created_at",
            "started_at",
            "finished_at",
        }
        payload = {k: v for k, v in dict(event).items() if k in keep}
        agent_id = str(event.get("agent_id") or event.get("task_id") or event.get("id") or "").strip()
        if agent_id:
            payload["agent_id"] = agent_id
        return payload

    def _externalize_large_text_payload(self, session_dir: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        base = Path(session_dir) if session_dir else None
        if base is None:
            sid = str(payload.get("session_id") or "").strip()
            if sid:
                base = self.sessions_dir / sid
        if base is None:
            return payload
        out = dict(payload)
        for key in ("content", "result", "output", "text", "message"):
            value = out.get(key)
            if isinstance(value, str) and len(value) > 16000:
                ref = BlobStore(base).put_text(value)
                out.pop(key, None)
                out[f"{key}_ref"] = ref
        return out
