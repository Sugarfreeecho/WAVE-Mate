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
            "visible_messages": [],
            "model_messages": [],
            "subagents": {},
            "context": {},
            "todo": None,
            "history_ops": [],
            "visible_range": {},
            "model_window": {},
        }
        for event in events:
            self.apply(snapshot, event)
        self._rebuild_projected_messages(snapshot)
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
        elif event_type in {
            "message_deleted",
            "message_rewritten",
            "history_branch_created",
            "history_compacted",
            "context_summary_committed",
            "visible_range_changed",
            "model_window_changed",
        }:
            self._apply_history_op(snapshot, event)
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

    def _apply_history_op(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        row = {
            "seq": event.seq,
            "timestamp": event.timestamp,
            "type": event.type,
            "payload": payload,
        }
        snapshot["history_ops"].append(row)
        if event.type == "visible_range_changed":
            snapshot["visible_range"] = {
                "from_seq": payload.get("from_seq"),
                "to_seq": payload.get("to_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
        elif event.type == "model_window_changed":
            snapshot["model_window"] = {
                "from_seq": payload.get("from_seq"),
                "to_seq": payload.get("to_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
        elif event.type == "history_compacted":
            snapshot["context"]["history_compaction"] = {
                "summary": payload.get("summary") or "",
                "compacted_before_seq": payload.get("compacted_before_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
        elif event.type == "context_summary_committed":
            snapshot["context"]["summary"] = {
                "summary": payload.get("summary") or "",
                "source_seq": payload.get("source_seq"),
                "changed_at_seq": event.seq,
            }

    def _rebuild_projected_messages(self, snapshot: dict) -> None:
        deleted = set()
        rewrites = {}
        visible_range = snapshot.get("visible_range") or {}
        model_window = snapshot.get("model_window") or {}
        compacted_before_seq = None
        compaction = (snapshot.get("context") or {}).get("history_compaction") or {}
        if compaction.get("compacted_before_seq") is not None:
            try:
                compacted_before_seq = int(compaction.get("compacted_before_seq"))
            except (TypeError, ValueError):
                compacted_before_seq = None

        for op in snapshot.get("history_ops") or []:
            payload = op.get("payload") or {}
            if op.get("type") == "message_deleted":
                target = self._int_or_none(payload.get("target_seq"))
                if target is not None:
                    deleted.add(target)
            elif op.get("type") == "message_rewritten":
                target = self._int_or_none(payload.get("target_seq"))
                if target is not None:
                    rewrite = dict(payload)
                    rewrite["changed_at_seq"] = op.get("seq")
                    rewrites[target] = rewrite

        projected = []
        for message in snapshot.get("messages") or []:
            seq = self._int_or_none(message.get("seq"))
            if seq is None or seq in deleted:
                continue
            if not self._seq_in_range(seq, visible_range):
                continue
            next_message = self._copy_message(message)
            rewrite = rewrites.get(seq)
            if rewrite is not None:
                next_message["payload"] = dict(next_message.get("payload") or {})
                next_message["payload"]["content"] = rewrite.get("content") or ""
                next_message["rewritten_by_seq"] = rewrite.get("changed_at_seq")
                next_message["rewritten"] = True
            projected.append(next_message)

        model_messages = []
        for message in projected:
            seq = self._int_or_none(message.get("seq"))
            if seq is None:
                continue
            if not self._seq_in_range(seq, model_window):
                continue
            if compacted_before_seq is not None and seq < compacted_before_seq:
                continue
            model_messages.append(self._copy_message(message))

        if compacted_before_seq is not None and compaction.get("summary"):
            model_messages.insert(0, {
                "seq": compacted_before_seq,
                "role": "system",
                "payload": {
                    "content": str(compaction.get("summary") or ""),
                    "kind": "history_compaction",
                },
            })

        snapshot["visible_messages"] = projected
        snapshot["model_messages"] = model_messages

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

    @staticmethod
    def _int_or_none(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _seq_in_range(cls, seq: int, range_payload: dict) -> bool:
        if not range_payload:
            return True
        from_seq = cls._int_or_none(range_payload.get("from_seq"))
        to_seq = cls._int_or_none(range_payload.get("to_seq"))
        if from_seq is not None and seq < from_seq:
            return False
        if to_seq is not None and seq > to_seq:
            return False
        return True

    @staticmethod
    def _copy_message(message: dict) -> dict:
        copied = dict(message)
        copied["payload"] = dict(copied.get("payload") or {})
        return copied
