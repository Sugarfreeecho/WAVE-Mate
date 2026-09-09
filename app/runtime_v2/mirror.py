from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .blob_store import BlobStore
from .event_log import RuntimeEventLogBusyError, SessionEventLog
from .event_schema import RuntimeEvent
from .projector import RuntimeProjector
from .snapshot_store import SnapshotStore
from .subagent_store import RuntimeSubagentStore

logger = logging.getLogger(__name__)


class RuntimeMirror:
    """Synchronous mapper from UI-shaped events to native Runtime V2 facts."""

    def __init__(
        self,
        sessions_dir: str | Path,
        path_resolver: Optional[Callable[[str], str | Path]] = None,
        transaction_timeout_seconds: Optional[float] = None,
    ):
        self.sessions_dir = Path(sessions_dir)
        self._path_resolver = path_resolver
        self._transaction_timeout_seconds = transaction_timeout_seconds
        self.event_log = SessionEventLog(self.sessions_dir, path_resolver=path_resolver)
        self.projector = RuntimeProjector()
        self.snapshots = SnapshotStore(self.sessions_dir, path_resolver=path_resolver)
        self.subagents = RuntimeSubagentStore(
            self.sessions_dir,
            path_resolver=path_resolver,
        )

    def mirror_ui_event(self, session_id: str, event: Dict[str, Any]) -> Optional[RuntimeEvent]:
        subagent_event = self._mirror_subagent_event(session_id, event)
        if subagent_event is not None:
            return subagent_event
        mapped = self._map_ui_event(session_id, event)
        if not mapped:
            return None
        if mapped.pop("_set_extension_latest", False):
            from .extension_state import SessionExtensionStateStore

            self.event_log.session_dir(session_id).mkdir(parents=True, exist_ok=True)
            payload = dict(mapped.get("payload") or {})
            row = SessionExtensionStateStore(
                self.sessions_dir,
                path_resolver=self._path_resolver,
            ).set_latest(
                session_id,
                str(payload.get("plugin_id") or ""),
                str(payload.get("namespace") or ""),
                payload.get("value"),
                run_id=str((event or {}).get("run_id") or ""),
            )
            latest = self.event_log.read_latest(session_id, 1)
            return latest[-1] if latest and latest[-1].seq == row.get("seq") else None
        if not mapped.get("run_id"):
            mapped["run_id"] = str((event or {}).get("run_id") or "").strip() or None
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

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: Optional[dict] = None,
        run_id: Optional[str] = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional[RuntimeEvent]:
        try:
            with self.event_log.session_transaction(
                session_id,
                timeout_seconds=self._transaction_timeout_seconds,
            ):
                event = self.event_log._append_unlocked(session_id, event_type, payload=payload or {}, run_id=run_id)
                self._apply_snapshot_event(session_id, event)
            return event
        except Exception as exc:
            try:
                path = str(self.event_log.event_path(session_id))
            except Exception:
                path = "<unresolved>"
            logger.warning(
                "Runtime V2 mirror append failed for session %s path=%s type=%s: %s",
                session_id,
                path,
                event_type,
                exc,
            )
            if raise_on_error or isinstance(exc, RuntimeEventLogBusyError):
                raise
            return None

    def append_batch(self, session_id: str, rows: Iterable[dict]) -> List[RuntimeEvent]:
        """Append many native facts and materialize one snapshot.

        This is used by explicit migration/restore paths. Unlike the live UI
        convenience method, failures propagate so the migration transaction can
        verify or roll back the whole operation.
        """
        clean = [dict(row) for row in rows if isinstance(row, dict) and row.get("type")]
        if not clean:
            return []
        with self.event_log.session_transaction(
            session_id,
            timeout_seconds=self._transaction_timeout_seconds,
        ):
            events = self.event_log._append_many_unlocked(session_id, clean)
            snapshot = self.snapshots.read(session_id)
            if events and int(snapshot.get("last_seq") or 0) == int(events[0].seq) - 1:
                for event in events:
                    snapshot = self.projector.project_incremental(snapshot, event)
            else:
                snapshot = self.projector.project(self.event_log.read_all(session_id))
            self.snapshots.stamp_event_log(
                session_id, snapshot, self.event_log.event_path(session_id)
            )
            self.snapshots.write(session_id, snapshot)
            return events

    def mirror_ui_events_batch(self, session_id: str, ui_events: Iterable[dict]) -> List[RuntimeEvent]:
        rows: List[dict] = []
        snapshot = self.snapshots.read(session_id)
        extensions = snapshot.get("extensions") if isinstance(snapshot, dict) else {}
        revisions: Dict[tuple[str, str], int] = {}
        if isinstance(extensions, dict):
            for plugin_id, namespaces in extensions.items():
                if not isinstance(namespaces, dict):
                    continue
                for namespace, state_row in namespaces.items():
                    if isinstance(state_row, dict):
                        revisions[(str(plugin_id), str(namespace))] = int(
                            state_row.get("revision") or 0
                        )
        for event in ui_events or []:
            if not isinstance(event, dict):
                continue
            mapped = self._map_ui_event(session_id, dict(event))
            if mapped:
                if mapped.pop("_set_extension_latest", False):
                    payload = dict(mapped.get("payload") or {})
                    key = (
                        str(payload.get("plugin_id") or ""),
                        str(payload.get("namespace") or ""),
                    )
                    revisions[key] = revisions.get(key, 0) + 1
                    payload["revision"] = revisions[key]
                    mapped["payload"] = payload
                rows.append({
                    "type": mapped["type"],
                    "payload": mapped.get("payload") or {},
                    "run_id": mapped.get("run_id"),
                })
            else:
                rows.append({"type": "legacy_ui_event", "payload": dict(event)})
        return self.append_batch(session_id, rows)

    def _apply_snapshot_event(self, session_id: str, event: RuntimeEvent) -> None:
        try:
            snapshot = self.snapshots.read_for_update(session_id)
            if int(snapshot.get("last_seq") or 0) != int(event.seq) - 1:
                snapshot = self.projector.project(self.event_log.read_all(session_id))
            else:
                snapshot = self.projector.project_incremental(snapshot, event)
            self.snapshots.stamp_event_log(session_id, snapshot, self.event_log.event_path(session_id))
            self.snapshots.write_checkpointed(session_id, snapshot)
        except Exception as exc:
            logger.debug("Runtime V2 mirror incremental snapshot failed for session %s: %s", session_id, exc)
            self._refresh_snapshot(session_id)

    def _refresh_snapshot(self, session_id: str) -> None:
        try:
            snapshot = self.projector.project(self.event_log.read_all(session_id))
            self.snapshots.stamp_event_log(session_id, snapshot, self.event_log.event_path(session_id))
            self.snapshots.write(session_id, snapshot)
        except Exception as exc:
            logger.debug("Runtime V2 mirror snapshot failed for session %s: %s", session_id, exc)

    def _map_ui_event(self, session_id: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_type = str((event or {}).get("type") or "")
        if not event_type:
            return None
        from .legacy_compat import map_legacy_ui_optional_event

        legacy_optional = map_legacy_ui_optional_event(event)
        if legacy_optional is not None:
            return legacy_optional
        if event_type == "user":
            return {"type": "message_user", "payload": {"content": event.get("content") or ""}}
        if event_type == "user_steer":
            return {
                "type": "message_user",
                "payload": {
                    "content": event.get("content") or "",
                    "ui_type": "user_steer",
                    "steer": bool(event.get("steer")),
                    "steer_id": str(event.get("steer_id") or ""),
                    "client_id": str(event.get("client_id") or ""),
                    "steer_mode": "append" if str(event.get("steer_mode") or "").strip().lower() == "append" else "interrupt",
                },
            }
        if event_type == "final":
            return {"type": "message_assistant_final", "payload": {"content": event.get("content") or ""}}
        if event_type == "context_tokens":
            return {"type": "context_tokens", "payload": dict(event)}
        if event_type == "context_summary_body":
            return {
                "type": "context_summary_committed",
                "payload": {
                    "summary": event.get("content") or event.get("summary") or event.get("text") or "",
                    "source": "ui_event",
                },
            }
        if event_type == "context_summary_finished":
            return {"type": "ui_event", "payload": dict(event)}
        if event_type in {"subagent_started", "subagent_progress", "subagent_finished", "subagent_failed", "subagent_result_consumed"}:
            return {"type": event_type, "payload": self._slim_subagent_payload(event)}
        if event_type in {"tool_call", "tool_result"}:
            # The legacy UI calls a completed row ``tool_call`` and includes
            # its result. Preserve that historical shape in the UI projection,
            # while recording the semantically correct Runtime V2 lifecycle.
            mapped_type = (
                "tool_finished"
                if event_type == "tool_result" or "result" in event
                else "tool_started"
            )
            return {
                "type": mapped_type,
                "payload": self._externalize_large_text_payload(
                    str(self.event_log.session_dir(session_id)),
                    dict(event),
                ),
            }
        if event_type == "cache_stats":
            payload = dict(event)
            try:
                provider_tokens = int(payload.get("input_tokens") or 0)
            except (TypeError, ValueError):
                provider_tokens = 0
            if provider_tokens > 0:
                payload["estimated"] = provider_tokens
                payload["token_source"] = "provider_exact"
                payload["source"] = "provider_usage"
                payload["token_mode"] = str(
                    payload.get("token_mode")
                    or payload.get("context_token_mode")
                    or "hybrid"
                )
                return {"type": "context_tokens", "payload": payload}
            return {"type": "ui_event", "payload": payload}
        if event_type in {"status", "process_metrics", "validate_final"}:
            return {"type": "ui_event", "payload": dict(event)}
        return {
            "type": "ui_event",
            "payload": self._externalize_large_text_payload(
                str(self.event_log.session_dir(session_id)),
                dict(event),
            ),
        }

    def _mirror_subagent_event(self, session_id: str, event: Dict[str, Any]) -> Optional[RuntimeEvent]:
        event_type = str((event or {}).get("type") or "")
        if event_type not in {"subagent_started", "subagent_progress", "subagent_finished", "subagent_failed", "subagent_result_consumed"}:
            return None
        agent_id = str(event.get("agent_id") or event.get("task_id") or event.get("id") or "").strip()
        if not agent_id:
            return None
        try:
            sub_payload = self._externalize_large_text_payload(
                str(self.subagents.agent_dir(session_id, agent_id)),
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
                base = self.event_log.session_dir(sid)
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
