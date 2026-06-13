from __future__ import annotations

from typing import Iterable, Optional

from .event_schema import RuntimeEvent


TERMINAL_RUN_TYPES = {
    "run_finished": "finished",
    "run_failed": "failed",
    "run_interrupted": "interrupted",
}


class RuntimeProjector:
    """Rebuild a session snapshot from Runtime V2 events."""

    def project(self, events: Iterable[RuntimeEvent]) -> dict:
        snapshot = {
            "session_id": None,
            "last_seq": 0,
            "updated_at": None,
            "runs": {},
            "active_runs": [],
            "messages": [],
            "subagents": {},
            "context": {},
            "todo": None,
        }
        for event in events:
            self.apply(snapshot, event)
        snapshot["active_runs"] = [
            run for run in snapshot["runs"].values()
            if run.get("status") not in {"finished", "failed", "interrupted"}
        ]
        return snapshot

    def apply(self, snapshot: dict, event: RuntimeEvent) -> dict:
        snapshot["session_id"] = snapshot.get("session_id") or event.session_id
        snapshot["last_seq"] = max(int(snapshot.get("last_seq") or 0), event.seq)
        snapshot["updated_at"] = event.timestamp
        event_type = event.type

        if event_type == "session_meta":
            snapshot["session"] = dict(event.payload or {})
        elif event_type == "message_user":
            self._append_message(snapshot, event, "user")
        elif event_type in {"message_assistant_delta", "message_assistant_final"}:
            self._append_or_update_assistant(snapshot, event)
        elif event_type == "run_started":
            self._upsert_run(snapshot, event, "running")
        elif event_type == "run_heartbeat":
            self._upsert_run(snapshot, event, "running", heartbeat_only=True)
        elif event_type in TERMINAL_RUN_TYPES:
            self._upsert_run(snapshot, event, TERMINAL_RUN_TYPES[event_type])
        elif event_type.startswith("subagent_"):
            self._apply_subagent(snapshot, event)
        elif event_type == "context_tokens":
            snapshot["context"]["tokens"] = dict(event.payload or {})
        elif event_type == "todo_updated":
            snapshot["todo"] = event.payload.get("todo") if isinstance(event.payload, dict) else event.payload
        return snapshot

    def _append_message(self, snapshot: dict, event: RuntimeEvent, role: str) -> None:
        snapshot["messages"].append({
            "seq": event.seq,
            "timestamp": event.timestamp,
            "role": role,
            "run_id": event.run_id,
            "payload": dict(event.payload or {}),
        })

    def _append_or_update_assistant(self, snapshot: dict, event: RuntimeEvent) -> None:
        if event.type == "message_assistant_final":
            self._append_message(snapshot, event, "assistant")
            return
        delta = str((event.payload or {}).get("delta") or "")
        if not delta:
            return
        last = snapshot["messages"][-1] if snapshot["messages"] else None
        if last and last.get("role") == "assistant" and last.get("run_id") == event.run_id and last.get("streaming"):
            last["payload"]["content"] = str(last["payload"].get("content") or "") + delta
            last["seq"] = event.seq
            last["timestamp"] = event.timestamp
        else:
            snapshot["messages"].append({
                "seq": event.seq,
                "timestamp": event.timestamp,
                "role": "assistant",
                "run_id": event.run_id,
                "streaming": True,
                "payload": {"content": delta},
            })

    def _upsert_run(self, snapshot: dict, event: RuntimeEvent, status: str, heartbeat_only: bool = False) -> None:
        run_id = self._event_run_id(event)
        if not run_id:
            return
        runs = snapshot["runs"]
        run = runs.get(run_id)
        if not run:
            run = {
                "run_id": run_id,
                "session_id": event.session_id,
                "status": "running",
                "started_at": event.timestamp,
                "heartbeat_at": event.timestamp,
                "finished_at": None,
                "error": None,
            }
            runs[run_id] = run
        run["heartbeat_at"] = event.timestamp
        if not heartbeat_only:
            run["status"] = status
        if status in {"finished", "failed", "interrupted"}:
            run["finished_at"] = event.timestamp
        if status == "failed":
            run["error"] = str((event.payload or {}).get("error") or "")

    def _apply_subagent(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = event.payload or {}
        agent_id = str(payload.get("agent_id") or payload.get("id") or "")
        if not agent_id:
            return
        state = snapshot["subagents"].get(agent_id) or {
            "agent_id": agent_id,
            "status": "running",
            "has_final": False,
            "result_consumed": False,
            "started_at": event.timestamp,
            "finished_at": None,
        }
        if event.type == "subagent_finished":
            state["status"] = "finished" if payload.get("has_final", True) else "failed"
            state["has_final"] = bool(payload.get("has_final", True))
            state["finished_at"] = event.timestamp
        elif event.type == "subagent_failed":
            state["status"] = "failed"
            state["finished_at"] = event.timestamp
        elif event.type == "subagent_result_consumed":
            state["result_consumed"] = True
        else:
            state["status"] = state.get("status") or "running"
        state.update({k: v for k, v in payload.items() if k not in {"status"}})
        snapshot["subagents"][agent_id] = state

    @staticmethod
    def _event_run_id(event: RuntimeEvent) -> Optional[str]:
        if event.run_id:
            return event.run_id
        payload = event.payload or {}
        run = payload.get("run")
        if isinstance(run, dict) and run.get("run_id"):
            return str(run.get("run_id"))
        if payload.get("run_id"):
            return str(payload.get("run_id"))
        return None
