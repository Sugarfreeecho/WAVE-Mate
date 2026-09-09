from __future__ import annotations

import copy
from typing import Iterable, Optional

from .event_schema import RuntimeEvent
from .legacy_compat import normalize_legacy_optional_event
from .versions import PROJECTOR_VERSION


TERMINAL_RUN_TYPES = {
    "run_finished": "finished",
    "run_failed": "failed",
    "run_interrupted": "interrupted",
}


HOOK_EVENT_TYPES = {
    "hook_started",
    "hook_progress",
    "hook_finished",
    "hook_failed",
    "hook_blocked",
    "hook_timed_out",
    "hook_input_modified",
}

PLUGIN_EVENT_TYPES = {"plugin_state_changed", "plugin_reloaded"}

_FULL_MESSAGE_REPROJECTION_TYPES = {
    "model_history_replaced",
    "model_tail_truncated",
    "model_prefix_compacted",
    "runtime_snapshot_compacted",
    "message_deleted",
    "message_rewritten",
    "history_branch_created",
    "history_compacted",
    "visible_range_changed",
    "model_window_changed",
}

_HOOK_RECENT_LIMIT = 50
_HOOK_SNAPSHOT_FIELDS = {
    "hook_id",
    "hook_event",
    "event",
    "plugin_id",
    "hook_type",
    "source_id",
    "execution_id",
    "invocation_id",
    "hook_run_id",
    "hook_call_id",
    "tool_call_id",
    "tool_name",
    "subagent_id",
    "matcher",
    "status",
    "success",
    "outcome",
    "decision",
    "reason",
    "error",
    "message",
    "user_message",
    "duration_ms",
    "input_modified",
    "progress",
    "exit_code",
    "failure_policy",
    "attempt",
    "stdout_ref",
    "stderr_ref",
    "output_ref",
    "result_ref",
}


class RuntimeProjector:
    """Rebuild a session snapshot from Runtime V2 events."""

    def empty_snapshot(self) -> dict:
        return {
            "projector_version": PROJECTOR_VERSION,
            "session_id": None,
            "last_seq": 0,
            "updated_at": None,
            "runs": {},
            "active_runs": [],
            "messages": [],
            "raw_model_messages": [],
            "model_history_generation": 0,
            "visible_messages": [],
            "model_messages": [],
            "subagents": {},
            "context": {},
            "hooks": {
                "recent": [],
                "stats": {
                    "total": 0,
                    "started": 0,
                    "progress": 0,
                    "finished": 0,
                    "failed": 0,
                    "blocked": 0,
                    "timed_out": 0,
                    "input_modified": 0,
                    "by_type": {},
                },
            },
            "plugins": {},
            "extensions": {},
            "extension_events": [],
            "interactions": {},
            "approvals": {},
            "pending_interactions": [],
            "pending_approvals": [],
            "history_ops": [],
            "legacy_observations": [],
            "visible_range": {},
            "model_window": {},
            "operation_ids": [],
        }

    def project(self, events: Iterable[RuntimeEvent]) -> dict:
        snapshot = self.empty_snapshot()
        for event in events:
            self.apply(snapshot, event)
        self.finalize(snapshot)
        return snapshot

    def project_incremental(self, snapshot: dict, event: RuntimeEvent) -> dict:
        event = normalize_legacy_optional_event(event, snapshot)
        if not snapshot:
            snapshot = self.empty_snapshot()
        else:
            snapshot = self._copy_for_incremental(snapshot, event)
        self._ensure_shape(snapshot)
        message_count = len(snapshot.get("messages") or [])
        raw_model_count = len(snapshot.get("raw_model_messages") or [])
        self.apply(snapshot, event)
        assistant_final_reconciled = (
            event.type == "assistant_final_committed"
            and isinstance((event.payload or {}).get("model_finalize"), dict)
        )
        if event.type in _FULL_MESSAGE_REPROJECTION_TYPES or assistant_final_reconciled:
            self._rebuild_projected_messages(snapshot)
        else:
            self._update_incremental_message_projections(
                snapshot,
                event,
                message_count=message_count,
                raw_model_count=raw_model_count,
            )
        snapshot["active_runs"] = [
            run for run in snapshot["runs"].values()
            if run.get("status") not in {"finished", "failed", "interrupted"}
        ]
        return snapshot

    def _copy_for_incremental(self, snapshot: dict, event: RuntimeEvent) -> dict:
        """Copy only mutable containers touched by an incremental apply.

        This keeps the published in-memory snapshot immutable for concurrent
        readers without deep-copying several duplicated message payload lists
        on every event.
        """
        out = dict(snapshot)
        for key in (
            "messages",
            "raw_model_messages",
            "visible_messages",
            "model_messages",
            "active_runs",
            "history_ops",
            "legacy_observations",
            "operation_ids",
        ):
            value = snapshot.get(key)
            out[key] = list(value) if isinstance(value, list) else []
        out["context"] = dict(snapshot.get("context") or {})
        out["runs"] = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in dict(snapshot.get("runs") or {}).items()
        }
        out["subagents"] = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in dict(snapshot.get("subagents") or {}).items()
        }
        out["plugins"] = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in dict(snapshot.get("plugins") or {}).items()
        }
        if event.type.startswith("extension_"):
            out["extensions"] = copy.deepcopy(snapshot.get("extensions") or {})
            out["extension_events"] = copy.deepcopy(
                snapshot.get("extension_events") or []
            )
        out["interactions"] = {
            key: copy.deepcopy(value) if isinstance(value, dict) else value
            for key, value in dict(snapshot.get("interactions") or {}).items()
        }
        out["approvals"] = {
            key: copy.deepcopy(value) if isinstance(value, dict) else value
            for key, value in dict(snapshot.get("approvals") or {}).items()
        }
        out["pending_interactions"] = [
            copy.deepcopy(value) for value in list(snapshot.get("pending_interactions") or [])
        ]
        out["pending_approvals"] = [
            copy.deepcopy(value) for value in list(snapshot.get("pending_approvals") or [])
        ]
        out["hooks"] = copy.deepcopy(snapshot.get("hooks") or {})
        if event.type == "message_assistant_delta" and out["messages"]:
            latest = dict(out["messages"][-1])
            latest["payload"] = dict(latest.get("payload") or {})
            out["messages"][-1] = latest
        return out

    def _update_incremental_message_projections(
        self,
        snapshot: dict,
        event: RuntimeEvent,
        *,
        message_count: int,
        raw_model_count: int,
    ) -> None:
        """Maintain derived message lists in O(number of appended rows).

        History/window operations still take the full, deterministic rebuild
        path. Normal run, tool, token, extension, hook and subagent events do not
        touch either projection.
        """
        messages = snapshot.get("messages") or []
        visible = snapshot.get("visible_messages")
        if not isinstance(visible, list):
            visible = []
            snapshot["visible_messages"] = visible
        if len(messages) > message_count:
            visible.extend(self._copy_message(row) for row in messages[message_count:])
        elif event.type == "message_assistant_delta" and messages:
            latest = self._copy_message(messages[-1])
            if visible and (
                visible[-1].get("run_id") == latest.get("run_id")
                and visible[-1].get("streaming")
            ):
                visible[-1] = latest
            else:
                visible.append(latest)

        raw_model = snapshot.get("raw_model_messages") or []
        model = snapshot.get("model_messages")
        if not isinstance(model, list):
            model = []
            snapshot["model_messages"] = model
        if len(raw_model) > raw_model_count:
            model.extend(self._copy_message(row) for row in raw_model[raw_model_count:])

        context = snapshot.get("context")
        if not isinstance(context, dict):
            context = {}
            snapshot["context"] = context
        if raw_model:
            context.pop("model_history_missing", None)
        elif visible:
            context["model_history_missing"] = {
                "reason": "raw_model_messages_absent",
                "visible_message_count": len(visible),
            }

    def finalize(self, snapshot: dict) -> dict:
        self._ensure_shape(snapshot)
        self._rebuild_projected_messages(snapshot)
        snapshot["active_runs"] = [
            run for run in snapshot["runs"].values()
            if run.get("status") not in {"finished", "failed", "interrupted"}
        ]
        return snapshot

    def _ensure_shape(self, snapshot: dict) -> None:
        defaults = self.empty_snapshot()
        for key, value in defaults.items():
            if key not in snapshot:
                snapshot[key] = value
        snapshot["projector_version"] = PROJECTOR_VERSION

    def apply(self, snapshot: dict, event: RuntimeEvent) -> dict:
        event = normalize_legacy_optional_event(event, snapshot)
        self._ensure_shape(snapshot)
        operation_id = str((event.payload or {}).get("operation_id") or "").strip()
        if operation_id and operation_id in set(snapshot.get("operation_ids") or []):
            snapshot["last_seq"] = max(int(snapshot.get("last_seq") or 0), event.seq)
            snapshot["updated_at"] = event.timestamp
            return snapshot
        if operation_id:
            ids = [str(x) for x in snapshot.get("operation_ids") or [] if str(x)]
            ids.append(operation_id)
            snapshot["operation_ids"] = ids[-256:]
        snapshot["session_id"] = snapshot.get("session_id") or event.session_id
        snapshot["last_seq"] = max(int(snapshot.get("last_seq") or 0), event.seq)
        snapshot["updated_at"] = event.timestamp
        event_type = event.type
        if self._event_changes_model_history(event):
            snapshot["model_history_generation"] = int(
                snapshot.get("model_history_generation") or 0
            ) + 1
            # A compact checkpoint proves one exact generation/prefix.  Do not
            # carry an opaque provider item after rewrite/delete/branch/local
            # compaction, even though transport validation would also reject it.
            context = snapshot.get("context")
            if isinstance(context, dict):
                context.pop("responses_compaction", None)

        if event_type == "runtime_snapshot_compacted":
            baseline = (event.payload or {}).get("snapshot")
            if isinstance(baseline, dict):
                snapshot.clear()
                snapshot.update(copy.deepcopy(baseline))
                self._ensure_shape(snapshot)
                snapshot["session_id"] = snapshot.get("session_id") or event.session_id
                snapshot["last_seq"] = int(event.seq)
        elif event_type == "session_meta":
            snapshot["session"] = dict(event.payload or {})
        elif event_type == "message_user":
            self._append_message(snapshot, event, "user")
        elif event_type == "user_turn_committed":
            self._append_message(snapshot, event, "user")
            self._append_model_message(snapshot, event)
        elif event_type == "assistant_final_committed":
            self._append_message(snapshot, event, "assistant")
            self._commit_assistant_final_model(snapshot, event)
            self._mark_run_finalizing(snapshot, event)
        elif event_type in {"message_assistant_delta", "message_assistant_final"}:
            self._append_or_update_assistant(snapshot, event)
        elif event_type in {"model_user", "model_assistant", "model_tool", "model_system"}:
            self._append_model_message(snapshot, event)
        elif event_type == "model_messages_appended":
            self._append_model_messages(snapshot, event)
        elif event_type == "model_history_replaced":
            self._replace_model_messages(snapshot, event)
            self._record_history_op(snapshot, event)
        elif event_type == "run_started":
            self._upsert_run(snapshot, event, "running")
        elif event_type == "run_heartbeat":
            self._upsert_run(snapshot, event, "running", heartbeat_only=True)
        elif event_type == "runtime_resumed":
            payload = dict(event.payload or {})
            payload["updated_at"] = event.timestamp
            payload["seq"] = event.seq
            snapshot["context"]["runtime_resume"] = payload
            if event.run_id:
                run = snapshot["runs"].get(event.run_id)
                if isinstance(run, dict):
                    run["heartbeat_at"] = event.timestamp
                    run["last_resume"] = payload
        elif event_type in TERMINAL_RUN_TYPES:
            self._upsert_run(snapshot, event, TERMINAL_RUN_TYPES[event_type])
        elif event_type.startswith("subagent_"):
            self._apply_subagent(snapshot, event)
        elif event_type == "context_tokens":
            payload = dict(event.payload or {})
            payload["updated_at"] = event.timestamp
            payload["seq"] = event.seq
            snapshot["context"]["tokens"] = payload
        elif event_type == "responses_compaction_committed":
            checkpoint = (event.payload or {}).get("checkpoint")
            if isinstance(checkpoint, dict):
                snapshot["context"]["responses_compaction"] = {
                    "checkpoint": copy.deepcopy(checkpoint),
                    "reason": str((event.payload or {}).get("reason") or ""),
                    "changed_at_seq": event.seq,
                }
        elif event_type in HOOK_EVENT_TYPES:
            self._apply_hook_event(snapshot, event)
        elif event_type in PLUGIN_EVENT_TYPES:
            self._apply_plugin_event(snapshot, event)
        elif event_type == "extension_state_changed":
            self._apply_extension_state(snapshot, event)
        elif event_type == "extension_event":
            self._apply_extension_event(snapshot, event)
        elif event_type.startswith("interaction_"):
            self._apply_human_interaction(snapshot, event, kind="question")
        elif event_type.startswith("approval_"):
            self._apply_human_interaction(snapshot, event, kind="approval")
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
        elif event_type in {"model_tail_truncated", "model_prefix_compacted"}:
            self._apply_model_reconcile_op(snapshot, event)
            self._record_history_op(snapshot, event)
        elif event_type.startswith("legacy_") and event_type.endswith("_observed"):
            self._apply_legacy_observation(snapshot, event)
        return snapshot

    @staticmethod
    def _event_changes_model_history(event: RuntimeEvent) -> bool:
        if event.type in {
            "model_history_replaced",
            "model_tail_truncated",
            "model_prefix_compacted",
            "message_deleted",
            "message_rewritten",
            "history_branch_created",
            "history_compacted",
            "model_window_changed",
        }:
            return True
        if event.type != "visible_range_changed":
            return False
        payload = dict(event.payload or {})
        return bool(
            payload.get("apply_model")
            or "restore_model_messages" in payload
            or payload.get("reason") == "runtime_v2_truncate"
        )

    @staticmethod
    def _apply_extension_state(snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        plugin_id = str(payload.get("plugin_id") or "").strip()
        namespace = str(payload.get("namespace") or "").strip()
        try:
            revision = int(payload.get("revision"))
        except (TypeError, ValueError):
            return
        if not plugin_id or not namespace or revision <= 0:
            return
        extensions = snapshot.setdefault("extensions", {})
        plugin_state = extensions.setdefault(plugin_id, {})
        current = plugin_state.get(namespace)
        if isinstance(current, dict) and revision <= int(current.get("revision") or 0):
            return
        value = copy.deepcopy(payload.get("value"))
        patch = payload.get("patch")
        if "value" not in payload and isinstance(patch, list):
            value = copy.deepcopy(current.get("value")) if isinstance(current, dict) else None
            for operation in patch:
                if not isinstance(operation, dict):
                    return
                op = str(operation.get("op") or "").strip().lower()
                path = str(operation.get("path") or "")
                if path == "":
                    if op == "remove":
                        value = None
                    elif op in {"add", "replace"} and "value" in operation:
                        value = copy.deepcopy(operation.get("value"))
                    else:
                        return
                    continue
                if not path.startswith("/") or "/" in path[1:] or not isinstance(value, dict):
                    return
                key = path[1:].replace("~1", "/").replace("~0", "~")
                if op == "remove":
                    value.pop(key, None)
                elif op in {"add", "replace"} and "value" in operation:
                    value[key] = copy.deepcopy(operation.get("value"))
                else:
                    return
        plugin_state[namespace] = {
            "revision": revision,
            "value": value,
            "updated_at": event.timestamp,
            "seq": event.seq,
        }

    @staticmethod
    def _apply_extension_event(snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        plugin_id = str(payload.get("plugin_id") or "").strip()
        event_name = str(payload.get("event_name") or "").strip()
        if not plugin_id or not event_name:
            return
        rows = list(snapshot.get("extension_events") or [])
        rows.append(
            {
                "plugin_id": plugin_id,
                "event_name": event_name,
                "data": copy.deepcopy(payload.get("data")),
                "timestamp": event.timestamp,
                "seq": event.seq,
            }
        )
        snapshot["extension_events"] = rows[-100:]

    @staticmethod
    def _apply_human_interaction(snapshot: dict, event: RuntimeEvent, *, kind: str) -> None:
        payload = dict(event.payload or {})
        is_approval = kind == "approval"
        collection_key = "approvals" if is_approval else "interactions"
        pending_key = "pending_approvals" if is_approval else "pending_interactions"
        id_key = "approval_id" if is_approval else "interaction_id"
        request_id = str(payload.get(id_key) or "").strip()
        if not request_id:
            return
        collection = snapshot.setdefault(collection_key, {})
        current = collection.get(request_id)
        if event.type.endswith("_requested"):
            row = payload
            row["status"] = "pending"
            row.setdefault("created_at", event.timestamp)
        else:
            row = dict(current or {id_key: request_id, "kind": kind})
            row.update(payload)
            if event.type.endswith("_resolved"):
                row["status"] = "resolved"
                row.setdefault("resolved_at", event.timestamp)
            elif event.type.endswith("_cancelled"):
                row["status"] = "cancelled"
                row.setdefault("cancelled_at", event.timestamp)
            elif event.type.endswith("_expired"):
                row["status"] = "expired"
                row.setdefault("expired_at", event.timestamp)
        row["seq"] = event.seq
        row["updated_at"] = event.timestamp
        collection[request_id] = row
        snapshot[pending_key] = [
            copy.deepcopy(item)
            for item in collection.values()
            if isinstance(item, dict) and item.get("status") == "pending"
        ]

    @staticmethod
    def _compact_hook_value(value):
        """Keep hook snapshots useful without copying command output into them."""
        if isinstance(value, str):
            return value if len(value) <= 2048 else value[:2048] + "..."
        if isinstance(value, dict):
            # Blob references are intentionally retained; their contents stay
            # outside snapshot.json and can be hydrated by the UI projection.
            return dict(value)
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return str(value)[:2048]

    def _apply_hook_event(self, snapshot: dict, event: RuntimeEvent) -> None:
        hooks = snapshot.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
            snapshot["hooks"] = hooks
        recent = hooks.get("recent")
        if not isinstance(recent, list):
            recent = []
        stats = hooks.get("stats")
        if not isinstance(stats, dict):
            stats = {}

        payload = dict(event.payload or {})
        row = {
            "seq": event.seq,
            "timestamp": event.timestamp,
            "type": event.type,
            "run_id": event.run_id,
        }
        for key in _HOOK_SNAPSHOT_FIELDS:
            if key in payload:
                row[key] = self._compact_hook_value(payload[key])
        # stdout/stderr/result bodies are deliberately omitted. Executors may
        # place large data in BlobStore and include one of the *_ref fields.
        recent.append(row)
        hooks["recent"] = recent[-_HOOK_RECENT_LIMIT:]

        by_type = stats.get("by_type")
        if not isinstance(by_type, dict):
            by_type = {}
        by_type[event.type] = int(by_type.get(event.type) or 0) + 1
        stats["by_type"] = by_type
        stats["total"] = int(stats.get("total") or 0) + 1
        counter = event.type.removeprefix("hook_")
        stats[counter] = int(stats.get(counter) or 0) + 1
        hooks["stats"] = stats

    def _apply_plugin_event(self, snapshot: dict, event: RuntimeEvent) -> None:
        plugins = snapshot.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}
            snapshot["plugins"] = plugins
        payload = dict(event.payload or {})
        plugin_id = str(
            payload.pop("plugin_id", None)
            or payload.get("id")
            or payload.get("name")
            or ""
        ).strip()
        if not plugin_id:
            # Invalid third-party audit rows remain in events.jsonl, but do not
            # create an unstable/anonymous key in the durable state projection.
            return
        current = plugins.get(plugin_id)
        if not isinstance(current, dict):
            current = {"plugin_id": plugin_id}
        state = payload.pop("state", None)
        if isinstance(state, dict):
            current.update(state)
        elif state is not None:
            current["status"] = state
        current.update(payload)
        current["plugin_id"] = plugin_id
        current["last_event"] = event.type
        current["updated_at"] = event.timestamp
        current["seq"] = event.seq
        if event.type == "plugin_reloaded":
            current["last_reloaded_at"] = event.timestamp
            current["reload_count"] = int(current.get("reload_count") or 0) + 1
        plugins[plugin_id] = current

    def _append_message(self, snapshot: dict, event: RuntimeEvent, role: str) -> None:
        payload = dict(event.payload or {})
        if event.type == "assistant_final_committed" and "ui_content" in payload:
            payload["content"] = payload.get("ui_content") or ""
        snapshot["messages"].append({
            "seq": event.seq,
            "timestamp": event.timestamp,
            "role": role,
            "run_id": event.run_id,
            "payload": payload,
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

    def _append_model_message(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        role = str(payload.get("role") or "").strip()
        if not role:
            role = {
                "model_user": "user",
                "model_assistant": "assistant",
                "model_tool": "tool",
                "model_system": "system",
            }.get(event.type, "")
        if not role:
            return
        row = {
            "seq": event.seq,
            "timestamp": event.timestamp,
            "role": role,
            "run_id": event.run_id,
            "payload": payload,
        }
        row["payload"]["role"] = role
        snapshot["raw_model_messages"].append(row)

    def _append_model_messages(self, snapshot: dict, event: RuntimeEvent) -> None:
        messages = (event.payload or {}).get("messages")
        if not isinstance(messages, list):
            return
        snapshot["raw_model_messages"].extend(
            self._model_message_items_to_rows(messages, event, marker="append_index")
        )

    def _commit_assistant_final_model(self, snapshot: dict, event: RuntimeEvent) -> None:
        finalize = (event.payload or {}).get("model_finalize")
        if not isinstance(finalize, dict) or finalize.get("mode") != "promote":
            self._append_model_message(snapshot, event)
            return
        rows = list(snapshot.get("raw_model_messages") or [])
        try:
            keep_index = int(finalize.get("keep_index"))
        except (TypeError, ValueError):
            self._append_model_message(snapshot, event)
            return
        if keep_index < 0 or keep_index >= len(rows):
            self._append_model_message(snapshot, event)
            return
        expected = str(finalize.get("expected_content") or "").strip()
        target = rows[keep_index] if isinstance(rows[keep_index], dict) else {}
        target_payload = target.get("payload") if isinstance(target.get("payload"), dict) else {}
        if str(target.get("role") or "") != "assistant" or str(target_payload.get("content") or "").strip() != expected:
            self._append_model_message(snapshot, event)
            return
        drop_indexes = set()
        for value in finalize.get("drop_indexes") or []:
            try:
                drop_indexes.add(int(value))
            except (TypeError, ValueError):
                continue
        changed = self._copy_message(target)
        metadata = dict(changed["payload"].get("metadata") or {})
        metadata["is_final"] = True
        metadata["is_assistant_response"] = False
        changed["payload"]["metadata"] = metadata
        changed["finalized_by_seq"] = int(event.seq)
        rows[keep_index] = changed
        snapshot["raw_model_messages"] = [
            row for index, row in enumerate(rows)
            if index == keep_index or index not in drop_indexes
        ]

    @staticmethod
    def _model_message_items_to_rows(messages: list, event: RuntimeEvent, *, marker: str) -> list:
        rows = []
        for index, item in enumerate(messages):
            if not isinstance(item, dict):
                continue
            msg_type = str(item.get("type") or item.get("role") or "").strip()
            role = {
                "human": "user",
                "llm": "assistant",
                "ai": "assistant",
                "agent": "assistant",
            }.get(msg_type, msg_type)
            if role not in {"user", "assistant", "tool", "system"}:
                continue
            msg_payload = dict(item)
            msg_payload["role"] = role
            msg_payload["content"] = str(item.get("content") or "")
            row = {
                "seq": event.seq,
                "timestamp": event.timestamp,
                "role": role,
                "run_id": event.run_id,
                "payload": msg_payload,
                marker: index,
            }
            if marker == "replacement_index":
                row["replaced_by_seq"] = event.seq
            rows.append(row)
        return rows

    def _replace_model_messages(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return
        rows = self._model_message_items_to_rows(messages, event, marker="replacement_index")
        # A replacement is the caller's canonical, already-reconciled model
        # context (compression, branch seeding, or a post-rewrite run).  Do
        # not apply an older visible-range operation a second time: its event
        # sequence cannot describe the individual rows in this new snapshot.
        snapshot["raw_model_messages"] = rows
        if "summary" in payload:
            snapshot["context"]["summary"] = {
                "summary": str(payload.get("summary") or ""),
                "source_seq": payload.get("source_seq"),
                "changed_at_seq": event.seq,
            }
        # A replacement changes the exact API request package.  An older
        # provider checkpoint must never remain authoritative if the process
        # stops before the next model response supplies fresh usage.
        self._mark_context_tokens_stale(snapshot, "model_history_replaced", event.seq)

    def _apply_model_reconcile_op(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        rows = list(snapshot.get("raw_model_messages") or [])
        if event.type == "model_tail_truncated":
            try:
                keep_count = max(0, int(payload.get("keep_count")))
            except (TypeError, ValueError):
                return
            snapshot["raw_model_messages"] = [
                self._strip_row_responses_continuation(row) for row in rows[:keep_count]
            ]
        else:
            try:
                drop_prefix_count = max(0, int(payload.get("drop_prefix_count")))
            except (TypeError, ValueError):
                return
            replacement = payload.get("replacement_prefix")
            if not isinstance(replacement, list) or drop_prefix_count > len(rows):
                return
            replacement_rows = self._model_message_items_to_rows(
                replacement, event, marker="compacted_index"
            )
            retained = [
                self._strip_row_responses_continuation(row)
                for row in rows[drop_prefix_count:]
            ]
            snapshot["raw_model_messages"] = replacement_rows + retained
        if "summary" in payload:
            snapshot["context"]["summary"] = {
                "summary": str(payload.get("summary") or ""),
                "source_seq": payload.get("source_seq"),
                "changed_at_seq": event.seq,
            }
        snapshot["context"].pop("responses_compaction", None)
        self._mark_context_tokens_stale(snapshot, event.type, event.seq)

    @classmethod
    def _strip_row_responses_continuation(cls, row: dict) -> dict:
        from .model_projection import strip_responses_continuation_from_message

        copied = cls._copy_message(row)
        copied["payload"] = strip_responses_continuation_from_message(copied["payload"])
        return copied

    def _apply_history_op(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = dict(event.payload or {})
        self._record_history_op(snapshot, event)
        if event.type == "history_branch_created":
            lineage_id = str(payload.get("lineage_id") or payload.get("source_session_id") or "").strip()
            if lineage_id:
                snapshot["context"]["responses_lineage_id"] = lineage_id
        if event.type in {"message_deleted", "message_rewritten"}:
            self._apply_message_op_to_model(snapshot, event)
            self._mark_context_tokens_stale(snapshot, event.type, event.seq)
        if event.type == "visible_range_changed":
            snapshot["visible_range"] = {
                "from_seq": payload.get("from_seq"),
                "to_seq": payload.get("to_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
            if payload.get("apply_model") or payload.get("reason") == "runtime_v2_truncate":
                self._truncate_snapshot_model_rows(snapshot, payload)
                snapshot["context"]["model_truncate"] = {
                    "changed_at_seq": event.seq,
                    "target_seq": payload.get("target_seq"),
                    "to_seq": payload.get("to_seq"),
                    "reason": payload.get("reason") or "",
                }
            if "restore_model_messages" in payload:
                restored_messages = payload.get("restore_model_messages")
                if not isinstance(restored_messages, list):
                    restored_messages = []
                restored_rows = []
                for index, item in enumerate(restored_messages):
                    if not isinstance(item, dict):
                        continue
                    msg_type = str(item.get("type") or item.get("role") or "").strip()
                    role = {
                        "human": "user",
                        "llm": "assistant",
                        "ai": "assistant",
                        "agent": "assistant",
                    }.get(msg_type, msg_type)
                    if role not in {"user", "assistant", "tool", "system"}:
                        continue
                    msg_payload = dict(item)
                    msg_payload["role"] = role
                    msg_payload["content"] = str(item.get("content") or "")
                    restored_rows.append({
                        "seq": event.seq,
                        "timestamp": event.timestamp,
                        "role": role,
                        "run_id": event.run_id,
                        "payload": msg_payload,
                        "replacement_index": index,
                        "replaced_by_seq": event.seq,
                    })
                snapshot["raw_model_messages"] = restored_rows
            if "restore_context_summary" in payload:
                restored_summary = payload.get("restore_context_summary")
                if isinstance(restored_summary, dict):
                    snapshot["context"]["summary"] = dict(restored_summary)
                else:
                    snapshot["context"].pop("summary", None)
            if "restore_history_compaction" in payload:
                restored_compaction = payload.get("restore_history_compaction")
                if isinstance(restored_compaction, dict):
                    snapshot["context"]["history_compaction"] = dict(restored_compaction)
                else:
                    snapshot["context"].pop("history_compaction", None)
            if "restore_context_tokens" in payload:
                restored_tokens = payload.get("restore_context_tokens")
                if isinstance(restored_tokens, dict):
                    snapshot["context"]["tokens"] = dict(restored_tokens)
                else:
                    snapshot["context"].pop("tokens", None)
            elif payload.get("apply_model") or payload.get("reason") == "runtime_v2_truncate":
                self._mark_context_tokens_stale(snapshot, "visible_range_changed", event.seq)
            if "restore_extensions" in payload:
                restored_extensions = payload.get("restore_extensions")
                snapshot["extensions"] = (
                    copy.deepcopy(restored_extensions)
                    if isinstance(restored_extensions, dict)
                    else {}
                )
        elif event.type == "model_window_changed":
            snapshot["model_window"] = {
                "from_seq": payload.get("from_seq"),
                "to_seq": payload.get("to_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
            self._truncate_snapshot_model_rows(snapshot, payload)
            self._mark_context_tokens_stale(snapshot, "model_window_changed", event.seq)
        elif event.type == "history_compacted":
            snapshot["context"]["history_compaction"] = {
                "summary": payload.get("summary") or "",
                "compacted_before_seq": payload.get("compacted_before_seq"),
                "changed_at_seq": event.seq,
                "reason": payload.get("reason") or "",
            }
            self._mark_context_tokens_stale(snapshot, "history_compacted", event.seq)
        elif event.type == "context_summary_committed":
            snapshot["context"]["summary"] = {
                "summary": payload.get("summary") or "",
                "source_seq": payload.get("source_seq"),
                "changed_at_seq": event.seq,
            }
            self._mark_context_tokens_stale(snapshot, "context_summary_committed", event.seq)

    @staticmethod
    def _record_history_op(snapshot: dict, event: RuntimeEvent) -> None:
        row = {
            "seq": event.seq,
            "timestamp": event.timestamp,
            "type": event.type,
            "payload": dict(event.payload or {}),
        }
        snapshot["history_ops"].append(row)

    @staticmethod
    def _mark_context_tokens_stale(snapshot: dict, reason: str, changed_at_seq: int) -> None:
        context = snapshot.get("context")
        if not isinstance(context, dict):
            return
        tokens = context.get("tokens")
        if not isinstance(tokens, dict):
            return
        stale = dict(tokens)
        stale["stale"] = True
        stale["stale_reason"] = str(reason or "history_changed")
        stale["stale_at_seq"] = int(changed_at_seq)
        context["tokens"] = stale

    def _apply_legacy_observation(self, snapshot: dict, event: RuntimeEvent) -> None:
        snapshot["legacy_observations"].append({
            "seq": event.seq,
            "timestamp": event.timestamp,
            "type": event.type,
            "payload": dict(event.payload or {}),
        })

    def _rebuild_projected_messages(self, snapshot: dict) -> None:
        deleted = set()
        rewrites = {}
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
            next_message = self._copy_message(message)
            rewrite = rewrites.get(seq)
            if rewrite is not None:
                next_message["payload"] = dict(next_message.get("payload") or {})
                next_message["payload"]["content"] = rewrite.get("content") or ""
                next_message["rewritten_by_seq"] = rewrite.get("changed_at_seq")
                next_message["rewritten"] = True
            projected.append(next_message)

        for op in snapshot.get("history_ops") or []:
            if op.get("type") != "visible_range_changed":
                continue
            payload = op.get("payload") or {}
            projected = self._truncate_rows(
                projected,
                payload,
                effective_before_seq=self._int_or_none(op.get("seq")),
            )

        raw_model_messages = snapshot.get("raw_model_messages") or []
        model_source = raw_model_messages if isinstance(raw_model_messages, list) else []
        context = snapshot.get("context")
        if not isinstance(context, dict):
            context = {}
            snapshot["context"] = context
        if model_source:
            context.pop("model_history_missing", None)
        elif projected:
            context["model_history_missing"] = {
                "reason": "raw_model_messages_absent",
                "visible_message_count": len(projected),
            }
        model_messages = []
        for message in model_source:
            seq = self._int_or_none(message.get("seq"))
            if seq is None:
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
        terminal_statuses = {"finished", "failed", "interrupted"}
        if status == "running" and not heartbeat_only:
            for existing_id, existing in list(runs.items()):
                if existing_id == run_id or not isinstance(existing, dict):
                    continue
                if existing.get("session_id") != event.session_id:
                    continue
                if existing.get("status") in terminal_statuses:
                    continue
                existing["status"] = "interrupted"
                existing["finished_at"] = event.timestamp
                existing["heartbeat_at"] = event.timestamp
                existing["error"] = existing.get("error") or "superseded by a newer run"
        if not run:
            run = {
                "run_id": run_id,
                "session_id": event.session_id,
                "status": "running",
                "started_at": event.timestamp,
                "heartbeat_at": event.timestamp,
                "finished_at": None,
                "error": None,
                "started_seq": event.seq,
                "phase": "running",
            }
            runs[run_id] = run
        if run.get("status") in terminal_statuses and status not in terminal_statuses:
            return
        run["heartbeat_at"] = event.timestamp
        run["heartbeat_seq"] = event.seq
        if not heartbeat_only:
            run["status"] = status
        if status in terminal_statuses:
            run["phase"] = "terminal"
            run["finished_at"] = event.timestamp
            run["finished_seq"] = event.seq
            reason = str((event.payload or {}).get("reason") or "").strip()
            if reason:
                run["reason"] = reason
        if status == "failed":
            run["error"] = str((event.payload or {}).get("error") or "")

    def _mark_run_finalizing(self, snapshot: dict, event: RuntimeEvent) -> None:
        run_id = self._event_run_id(event)
        run = snapshot.get("runs", {}).get(run_id) if run_id else None
        if not isinstance(run, dict):
            return
        if run.get("status") in {"finished", "failed", "interrupted"}:
            return
        run["phase"] = "finalizing"
        run["response_committed_at"] = event.timestamp
        run["response_committed_seq"] = event.seq

    def _apply_subagent(self, snapshot: dict, event: RuntimeEvent) -> None:
        payload = event.payload or {}
        agent_id = str(payload.get("agent_id") or payload.get("id") or "")
        if not agent_id:
            return
        if event.type == "subagent_deleted":
            snapshot["subagents"].pop(agent_id, None)
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
        """Return True if a row with ``seq`` should be kept after applying ``range_payload``.

        The retain region is defined by optional boundaries:

        - ``from_seq``: lower bound (inclusive). Rows with ``seq < from_seq``
          are dropped.
        - ``to_seq``: upper bound (inclusive). Rows with ``seq > to_seq`` are
          dropped.
        - ``target_seq``: an audit anchor identifying the clicked UI event.
          It does not change the retained range.  ``to_seq`` is the sole
          inclusive upper boundary, so delete/rewrite/truncate all have the
          same prefix semantics in the UI and model projections.
        """
        if not range_payload:
            return True
        from_seq = cls._int_or_none(range_payload.get("from_seq"))
        to_seq = cls._int_or_none(range_payload.get("to_seq"))
        if from_seq is not None and seq < from_seq:
            return False
        if to_seq is not None and seq > to_seq:
            return False
        return True

    @classmethod
    def _snapshot_should_keep(cls, row: dict, payload: dict) -> bool:
        """Decide whether a ``model_history_replaced`` snapshot row is kept.

        Snapshot rows share one ``seq`` (the event seq) but represent history
        *up to* that event, marked by ``replaced_by_seq``. A rewrite/delete
        truncate with ``target_seq`` drops visible history at and after
        ``target_seq`` while keeping everything before it. A snapshot whose
        ``replaced_by_seq < target_seq`` contains only pre-target history and
        must be kept entirely — even though its event seq may numerically fall
        between ``to_seq`` and ``target_seq``, because the snapshot captures
        the full history at the moment *before* the truncated run started.
        """
        target_seq = cls._int_or_none(payload.get("target_seq"))
        replaced_by = cls._int_or_none(row.get("replaced_by_seq"))
        if target_seq is not None and replaced_by is not None and replaced_by < target_seq:
            return True
        judge_seq = replaced_by if replaced_by is not None else cls._int_or_none(row.get("seq"))
        return cls._seq_in_range(judge_seq, payload)

    def _truncate_snapshot_rows(self, snapshot: dict, payload: dict) -> None:
        snapshot["messages"] = self._truncate_rows(snapshot.get("messages") or [], payload)

    def _truncate_snapshot_model_rows(self, snapshot: dict, payload: dict) -> None:
        snapshot["raw_model_messages"] = self._truncate_rows(
            snapshot.get("raw_model_messages") or [],
            payload,
        )

    @classmethod
    def _truncate_rows(cls, rows: list, payload: dict, effective_before_seq: Optional[int] = None) -> list:
        if payload.get("to_ui_index") is not None:
            try:
                return list(rows or [])[:max(0, int(payload.get("to_ui_index")))]
            except (TypeError, ValueError):
                return list(rows or [])
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            seq = cls._int_or_none(row.get("seq"))
            if seq is None:
                continue
            # A history operation is chronological, not a permanent global
            # filter. Rows appended after the operation must remain visible.
            if effective_before_seq is not None and seq > effective_before_seq:
                out.append(row)
                continue
            # model_history_replaced snapshot rows are judged as a whole by
            # their ``replaced_by_seq`` so a rewrite/delete truncate does not
            # erase pre-existing history (see ``_snapshot_should_keep``).
            if row.get("replaced_by_seq") is not None:
                if cls._snapshot_should_keep(row, payload):
                    out.append(row)
            elif cls._seq_in_range(seq, payload):
                out.append(row)
        return out

    def _apply_message_op_to_model(self, snapshot: dict, event: RuntimeEvent) -> None:
        """Keep standalone delete/rewrite operations aligned with model context.

        UI and model events have independent seq values, so correlate the
        targeted visible message by role/content and change the latest matching
        model row. Canonical truncate/replace callers still take precedence.
        """
        payload = dict(event.payload or {})
        target_seq = self._int_or_none(payload.get("target_seq"))
        if target_seq is None:
            return
        target = None
        for message in snapshot.get("messages") or []:
            if self._int_or_none((message or {}).get("seq")) == target_seq:
                target = message
                break
        if not isinstance(target, dict):
            return
        role = str(target.get("role") or "")
        content = str((target.get("payload") or {}).get("content") or "")
        rows = list(snapshot.get("raw_model_messages") or [])
        match_index = None
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if not isinstance(row, dict) or str(row.get("role") or "") != role:
                continue
            if str((row.get("payload") or {}).get("content") or "") == content:
                match_index = index
                break
        if match_index is None:
            return
        if event.type == "message_deleted":
            rows.pop(match_index)
        else:
            changed = self._copy_message(rows[match_index])
            changed["payload"]["content"] = str(payload.get("content") or "")
            changed["rewritten_by_seq"] = event.seq
            rows[match_index] = changed
        snapshot["raw_model_messages"] = rows

    @staticmethod
    def _copy_message(message: dict) -> dict:
        copied = dict(message)
        copied["payload"] = dict(copied.get("payload") or {})
        return copied
