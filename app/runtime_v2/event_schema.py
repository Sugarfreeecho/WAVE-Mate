from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


CORE_EVENT_TYPES = {
    "session_meta",
    "message_user",
    "message_assistant_delta",
    "message_assistant_final",
    "run_started",
    "run_heartbeat",
    "run_finished",
    "run_failed",
    "run_interrupted",
    "tool_started",
    "tool_delta",
    "tool_finished",
    "tool_failed",
    "subagent_started",
    "subagent_progress",
    "subagent_finished",
    "subagent_failed",
    "subagent_result_consumed",
    "context_tokens",
    "context_summary_started",
    "context_summary_finished",
    "todo_updated",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RuntimeEvent:
    seq: int
    type: str
    session_id: str
    timestamp: str = field(default_factory=now_iso)
    run_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "seq": int(self.seq),
            "timestamp": self.timestamp,
            "type": self.type,
            "session_id": self.session_id,
            "payload": dict(self.payload or {}),
        }
        if self.run_id:
            data["run_id"] = self.run_id
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeEvent":
        if not isinstance(data, dict):
            raise ValueError("runtime event must be an object")
        seq = data.get("seq")
        if not isinstance(seq, int):
            raise ValueError("runtime event seq must be an integer")
        event_type = str(data.get("type") or "").strip()
        if not event_type:
            raise ValueError("runtime event type is required")
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("runtime event session_id is required")
        timestamp = str(data.get("timestamp") or now_iso())
        run_id_raw = data.get("run_id")
        run_id = str(run_id_raw).strip() if run_id_raw else None
        payload = data.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("runtime event payload must be an object")
        return cls(
            seq=seq,
            type=event_type,
            session_id=session_id,
            timestamp=timestamp,
            run_id=run_id,
            payload=payload,
        )
