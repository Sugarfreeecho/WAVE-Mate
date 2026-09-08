from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from agent_harness import session_manager
from runtime_v2.event_schema import now_iso
from runtime_v2.mirror import RuntimeMirror


class HumanInteractionValidationError(ValueError):
    pass


class HumanInteractionNotFound(LookupError):
    pass


class HumanInteractionConflict(RuntimeError):
    pass


ASK_USER_ENV_VAR = "ASK_USER_ENABLED"
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def ask_user_enabled() -> bool:
    """Return whether the model may create new ask_user interactions."""

    return os.getenv(ASK_USER_ENV_VAR, "1").strip().lower() in _TRUE_ENV_VALUES


_TERMINAL = {"resolved", "cancelled", "expired"}
_WAITERS: Dict[tuple[str, str, str], asyncio.Future] = {}
_WAITERS_LOCK = threading.RLock()
_PENDING_COUNTS_INDEX_VERSION = 1


def _clean_text(value: Any, field: str, *, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HumanInteractionValidationError(f"{field} is required")
    if len(text) > maximum:
        raise HumanInteractionValidationError(f"{field} exceeds {maximum} characters")
    return text


def _digest(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalize_questions(arguments: Any) -> list[dict]:
    raw = arguments if isinstance(arguments, dict) else {}
    questions = raw.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        raise HumanInteractionValidationError("questions must contain between 1 and 4 items")
    normalized: list[dict] = []
    question_texts: set[str] = set()
    for q_index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise HumanInteractionValidationError(f"questions[{q_index - 1}] must be an object")
        header = _clean_text(question.get("header"), f"questions[{q_index - 1}].header", maximum=50)
        prompt = _clean_text(question.get("question"), f"questions[{q_index - 1}].question", maximum=1000)
        prompt_key = prompt.casefold()
        if prompt_key in question_texts:
            raise HumanInteractionValidationError("question text must be unique within one ask_user call")
        question_texts.add(prompt_key)
        options = question.get("options")
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise HumanInteractionValidationError(
                f"questions[{q_index - 1}].options must contain between 2 and 4 items"
            )
        normalized_options: list[dict] = []
        labels: set[str] = set()
        for o_index, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                raise HumanInteractionValidationError(
                    f"questions[{q_index - 1}].options[{o_index - 1}] must be an object"
                )
            label = _clean_text(
                option.get("label"),
                f"questions[{q_index - 1}].options[{o_index - 1}].label",
                maximum=80,
            )
            label_key = label.casefold()
            if label_key in labels:
                raise HumanInteractionValidationError(f"option labels must be unique for question q{q_index}")
            if label_key in {"other", "其他", "其它"}:
                raise HumanInteractionValidationError("Other is supplied by the UI and must not be an option")
            labels.add(label_key)
            normalized_option = {
                "option_id": f"q{q_index}o{o_index}",
                "label": label,
                "description": _clean_text(
                    option.get("description"),
                    f"questions[{q_index - 1}].options[{o_index - 1}].description",
                    maximum=1000,
                ),
            }
            preview = _clean_text(
                option.get("preview"),
                f"questions[{q_index - 1}].options[{o_index - 1}].preview",
                maximum=6000,
                required=False,
            )
            if preview:
                normalized_option["preview"] = preview
            normalized_options.append(normalized_option)
        normalized.append(
            {
                "question_id": f"q{q_index}",
                "header": header,
                "question": prompt,
                "options": normalized_options,
                "multi_select": bool(question.get("multi_select", question.get("multiSelect", False))),
            }
        )
    return normalized


def _normalize_metadata(arguments: Any) -> dict:
    raw = arguments if isinstance(arguments, dict) else {}
    metadata = raw.get("metadata")
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise HumanInteractionValidationError("metadata must be an object")
    try:
        encoded = json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HumanInteractionValidationError("metadata must be JSON serializable") from exc
    if len(encoded) > 4000:
        raise HumanInteractionValidationError("metadata exceeds 4000 characters")
    return dict(metadata)


class HumanInteractionService:
    def __init__(self, root=None, path_resolver=None):
        root = root or session_manager.repository.sessions_dir
        path_resolver = path_resolver or getattr(session_manager, "_resolve_session_path", None)
        self.mirror = RuntimeMirror(root, path_resolver=path_resolver)
        self._pending_counts_cache: Dict[str, tuple[tuple[bool, int, int], dict]] = {}
        self._pending_counts_lock = threading.RLock()

    def _snapshot(self, session_id: str) -> dict:
        return self.mirror.snapshots.read_consistent(
            session_id, self.mirror.event_log, self.mirror.projector
        )

    def _append_locked(self, session_id: str, event_type: str, payload: dict, run_id: str = ""):
        event = self.mirror.event_log._append_unlocked(
            session_id, event_type, payload=payload, run_id=run_id or None
        )
        snapshot = self.mirror.snapshots.read_for_update(session_id)
        if int(snapshot.get("last_seq") or 0) != int(event.seq) - 1:
            snapshot = self.mirror.projector.project(self.mirror.event_log.read_all(session_id))
        else:
            snapshot = self.mirror.projector.project_incremental(snapshot, event)
        self.mirror.snapshots.stamp_event_log(
            session_id, snapshot, self.mirror.event_log.event_path(session_id)
        )
        self.mirror.snapshots.write_checkpointed(session_id, snapshot)
        self._publish_pending_counts(session_id, self._counts_from_snapshot(snapshot))
        return event, snapshot

    @staticmethod
    def _counts_from_snapshot(snapshot: dict) -> dict:
        questions = len(list((snapshot or {}).get("pending_interactions") or []))
        approvals = len(list((snapshot or {}).get("pending_approvals") or []))
        return {
            "questions": questions,
            "approvals": approvals,
            "total": questions + approvals,
        }

    def _pending_counts_path(self, session_id: str):
        return self.mirror.snapshots.path(session_id).with_name("pending_counts.json")

    @staticmethod
    def _pending_counts_signature(path) -> tuple[bool, int, int]:
        try:
            stat = path.stat()
            return True, int(stat.st_size), int(stat.st_mtime_ns)
        except OSError:
            return False, 0, 0

    def _publish_pending_counts(self, session_id: str, counts: dict) -> None:
        sid = str(session_id or "").strip()
        normalized = {
            "questions": max(0, int(counts.get("questions") or 0)),
            "approvals": max(0, int(counts.get("approvals") or 0)),
        }
        normalized["total"] = normalized["questions"] + normalized["approvals"]
        path = self._pending_counts_path(sid)
        tmp = path.with_name(f".pending-{uuid.uuid4().hex[:8]}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(
                    {
                        "version": _PENDING_COUNTS_INDEX_VERSION,
                        **normalized,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError:
            # The index is an optimization only. The Runtime event and
            # in-memory count remain authoritative for this process.
            pass
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        with self._pending_counts_lock:
            self._pending_counts_cache[sid] = (
                self._pending_counts_signature(path),
                dict(normalized),
            )

    def _read_pending_counts_index(self, session_id: str) -> Optional[dict]:
        try:
            payload = json.loads(
                self._pending_counts_path(session_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            if int(payload.get("version") or 0) != _PENDING_COUNTS_INDEX_VERSION:
                return None
            questions = max(0, int(payload.get("questions") or 0))
            approvals = max(0, int(payload.get("approvals") or 0))
        except (TypeError, ValueError):
            return None
        return {
            "questions": questions,
            "approvals": approvals,
            "total": questions + approvals,
        }

    def create_question(
        self,
        session_id: str,
        arguments: Any,
        *,
        run_id: str = "",
        tool_call_id: str = "",
        interaction_id: str = "",
    ) -> dict:
        if not ask_user_enabled():
            raise HumanInteractionValidationError(
                f"ask_user is disabled by {ASK_USER_ENV_VAR}"
            )
        sid = _clean_text(session_id, "session_id", maximum=240)
        iid = interaction_id or uuid.uuid4().hex
        questions = _normalize_questions(arguments)
        metadata = _normalize_metadata(arguments)
        created_at = now_iso()
        request_core = {
            "interaction_id": iid,
            "kind": "question",
            "session_id": sid,
            "run_id": str(run_id or ""),
            "tool_call_id": str(tool_call_id or ""),
            "request_version": 1,
            "questions": questions,
            "metadata": metadata,
        }
        record = {
            **request_core,
            "status": "pending",
            "created_at": created_at,
            "expires_at": None,
            "request_digest": _digest(request_core),
        }
        with self.mirror.event_log.session_transaction(sid):
            snapshot = self._snapshot(sid)
            if iid in dict(snapshot.get("interactions") or {}):
                raise HumanInteractionConflict("interaction_id already exists")
            self._append_locked(sid, "interaction_requested", record, str(run_id or ""))
        return record

    def create_approval(
        self,
        session_id: str,
        *,
        approval_id: str,
        metadata: Optional[dict] = None,
        run_id: str = "",
        tool_call_id: str = "",
    ) -> dict:
        sid = _clean_text(session_id, "session_id", maximum=240)
        aid = _clean_text(approval_id, "approval_id", maximum=240)
        meta = dict(metadata or {})
        request_core = {
            "approval_id": aid,
            "kind": "approval",
            "session_id": sid,
            "run_id": str(run_id or ""),
            "tool_call_id": str(tool_call_id or ""),
            "request_version": 1,
            **meta,
        }
        from tool_approval_gate import approval_wait_seconds

        wait_seconds = approval_wait_seconds()
        expires_at = (
            datetime.fromtimestamp(
                time.time() + wait_seconds,
                tz=timezone.utc,
            ).isoformat()
            if wait_seconds is not None
            else None
        )
        record = {
            **request_core,
            "status": "pending",
            "created_at": now_iso(),
            "expires_at": expires_at,
            "request_digest": _digest(request_core),
        }
        with self.mirror.event_log.session_transaction(sid):
            snapshot = self._snapshot(sid)
            existing = dict(snapshot.get("approvals") or {}).get(aid)
            if isinstance(existing, dict):
                if existing.get("status") == "pending":
                    return existing
                raise HumanInteractionConflict("approval_id already exists")
            self._append_locked(sid, "approval_requested", record, str(run_id or ""))
        return record

    def list(self, session_id: str, *, kind: str = "question", status: str = "") -> list[dict]:
        snapshot = self._snapshot(session_id)
        key = "approvals" if kind == "approval" else "interactions"
        rows = [dict(row) for row in dict(snapshot.get(key) or {}).values() if isinstance(row, dict)]
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        return sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("interaction_id") or row.get("approval_id") or "")))

    def get(self, session_id: str, request_id: str, *, kind: str = "question") -> dict:
        key = "approvals" if kind == "approval" else "interactions"
        row = dict(self._snapshot(session_id).get(key) or {}).get(request_id)
        if not isinstance(row, dict):
            raise HumanInteractionNotFound(f"{kind} request not found")
        return dict(row)

    def pending_counts(self, session_id: str) -> dict:
        sid = str(session_id or "").strip()
        path = self._pending_counts_path(sid)
        signature = self._pending_counts_signature(path)
        with self._pending_counts_lock:
            cached = self._pending_counts_cache.get(sid)
            if cached is not None and cached[0] == signature:
                return dict(cached[1])
        counts = self._read_pending_counts_index(sid)
        if counts is None:
            # One-time upgrade path for sessions created before the lightweight
            # index existed. All subsequent reads are O(1) and do not touch the
            # multi-megabyte Runtime snapshot.
            counts = self._counts_from_snapshot(self._snapshot(sid))
            self._publish_pending_counts(sid, counts)
            return dict(counts)
        with self._pending_counts_lock:
            self._pending_counts_cache[sid] = (signature, dict(counts))
        return dict(counts)

    def pending_counts_many(self, session_ids: Iterable[str]) -> Dict[str, dict]:
        """Return cached counts and load only sessions not seen by this process.

        Every interaction mutation publishes the new count into this cache, so
        re-stat'ing every ``pending_counts.json`` during each sidebar poll only
        adds disk contention. Direct ``pending_counts`` calls retain signature
        validation for explicit reads and startup recovery.
        """
        ids = list(dict.fromkeys(str(item or "").strip() for item in session_ids if str(item or "").strip()))
        ready: Dict[str, dict] = {}
        with self._pending_counts_lock:
            for sid in ids:
                cached = self._pending_counts_cache.get(sid)
                if cached is not None:
                    ready[sid] = dict(cached[1])
        missing = [sid for sid in ids if sid not in ready]
        if not missing:
            return ready
        workers = min(8, len(missing))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="pending-counts",
        ) as pool:
            futures = {
                pool.submit(self.pending_counts, sid): sid for sid in missing
            }
            for future, sid in ((future, futures[future]) for future in futures):
                ready[sid] = future.result()
        return ready

    def resolve_question(self, session_id: str, interaction_id: str, answers: Any, *, resolver: Optional[dict] = None) -> dict:
        sid = str(session_id or "").strip()
        iid = str(interaction_id or "").strip()
        with self.mirror.event_log.session_transaction(sid):
            record = dict(self._snapshot(sid).get("interactions") or {}).get(iid)
            if not isinstance(record, dict):
                raise HumanInteractionNotFound("interaction not found")
            if record.get("status") in _TERMINAL:
                return dict(record)
            normalized_answers = self._validate_answers(record, answers)
            payload = {
                "interaction_id": iid,
                "status": "resolved",
                "answers": normalized_answers,
                "resolved_at": now_iso(),
                "resolver": dict(resolver or {}),
                "request_digest": record.get("request_digest"),
            }
            _event, snapshot = self._append_locked(sid, "interaction_resolved", payload, str(record.get("run_id") or ""))
            resolved = dict(snapshot.get("interactions") or {}).get(iid) or payload
        _wake_waiter(sid, "question", iid, dict(resolved))
        return dict(resolved)

    def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        decision: str,
        *,
        resolver: Optional[dict] = None,
        rejection_reason: str = "",
    ) -> dict:
        sid = str(session_id or "").strip()
        aid = str(approval_id or "").strip()
        normalized_decision = str(decision or "").strip().lower().replace("-", "_")
        if normalized_decision not in {
            "allow_once",
            "allow_session",
            "allow_always",
            "allow_external_workspace",
            "allow_external_workspace_once",
            "deny",
        }:
            raise HumanInteractionValidationError(
                "decision must be allow_once, allow_session, allow_always, "
                "allow_external_workspace, allow_external_workspace_once, or deny"
            )
        normalized_rejection_reason = _clean_text(
            rejection_reason,
            "rejection_reason",
            maximum=2000,
            required=False,
        )
        if normalized_decision != "deny" and normalized_rejection_reason:
            raise HumanInteractionValidationError(
                "rejection_reason is only valid when decision is deny"
            )
        with self.mirror.event_log.session_transaction(sid):
            record = dict(self._snapshot(sid).get("approvals") or {}).get(aid)
            if not isinstance(record, dict):
                raise HumanInteractionNotFound("approval not found")
            if record.get("status") in _TERMINAL:
                return dict(record)
            if (
                bool(record.get("force_approval"))
                and normalized_decision
                in {
                    "allow_session",
                    "allow_always",
                    "allow_external_workspace",
                    "allow_external_workspace_once",
                }
            ):
                raise HumanInteractionValidationError(
                    "Dangerous approvals only support allow_once or deny"
                )
            if normalized_decision == "allow_always" and not bool(
                record.get("allow_always_available")
            ):
                raise HumanInteractionValidationError(
                    "This approval does not support a durable allow rule"
                )
            if normalized_decision == "allow_session" and record.get(
                "allow_session_available", True
            ) is False:
                raise HumanInteractionValidationError(
                    "This approval only supports allow_once or deny"
                )
            payload = {
                "approval_id": aid,
                "status": "resolved",
                "decision": normalized_decision,
                "resolved_at": now_iso(),
                "resolver": dict(resolver or {}),
                "request_digest": record.get("request_digest"),
            }
            if normalized_decision == "deny" and normalized_rejection_reason:
                payload["rejection_reason"] = normalized_rejection_reason
            _event, snapshot = self._append_locked(sid, "approval_resolved", payload, str(record.get("run_id") or ""))
            resolved = dict(snapshot.get("approvals") or {}).get(aid) or payload
        _wake_waiter(sid, "approval", aid, dict(resolved))
        return dict(resolved)

    def cancel(self, session_id: str, request_id: str, *, kind: str = "question", reason: str = "user_cancelled") -> dict:
        sid = str(session_id or "").strip()
        rid = str(request_id or "").strip()
        collection = "approvals" if kind == "approval" else "interactions"
        id_key = "approval_id" if kind == "approval" else "interaction_id"
        event_type = "approval_cancelled" if kind == "approval" else "interaction_cancelled"
        with self.mirror.event_log.session_transaction(sid):
            record = dict(self._snapshot(sid).get(collection) or {}).get(rid)
            if not isinstance(record, dict):
                raise HumanInteractionNotFound(f"{kind} request not found")
            if record.get("status") in _TERMINAL:
                return dict(record)
            payload = {
                id_key: rid,
                "status": "cancelled",
                "reason": _clean_text(reason, "reason", maximum=500),
                "cancelled_at": now_iso(),
                "request_digest": record.get("request_digest"),
            }
            _event, snapshot = self._append_locked(sid, event_type, payload, str(record.get("run_id") or ""))
            resolved = dict(snapshot.get(collection) or {}).get(rid) or payload
        _wake_waiter(sid, kind, rid, dict(resolved))
        return dict(resolved)

    def expire(self, session_id: str, request_id: str, *, kind: str = "question", reason: str = "timeout") -> dict:
        sid = str(session_id or "").strip()
        rid = str(request_id or "").strip()
        collection = "approvals" if kind == "approval" else "interactions"
        id_key = "approval_id" if kind == "approval" else "interaction_id"
        event_type = "approval_expired" if kind == "approval" else "interaction_expired"
        with self.mirror.event_log.session_transaction(sid):
            record = dict(self._snapshot(sid).get(collection) or {}).get(rid)
            if not isinstance(record, dict):
                raise HumanInteractionNotFound(f"{kind} request not found")
            if record.get("status") in _TERMINAL:
                return dict(record)
            payload = {
                id_key: rid,
                "status": "expired",
                "reason": _clean_text(reason, "reason", maximum=500),
                "expired_at": now_iso(),
                "request_digest": record.get("request_digest"),
            }
            _event, snapshot = self._append_locked(sid, event_type, payload, str(record.get("run_id") or ""))
            resolved = dict(snapshot.get(collection) or {}).get(rid) or payload
        _wake_waiter(sid, kind, rid, dict(resolved))
        return dict(resolved)

    @staticmethod
    def _validate_answers(record: dict, answers: Any) -> list[dict]:
        raw_answers = answers.get("answers") if isinstance(answers, dict) else answers
        if not isinstance(raw_answers, list):
            raise HumanInteractionValidationError("answers must be an array")
        by_id = {str(item.get("question_id") or ""): item for item in raw_answers if isinstance(item, dict)}
        questions = list(record.get("questions") or [])
        if set(by_id) != {str(q.get("question_id") or "") for q in questions}:
            raise HumanInteractionValidationError("answers must contain every question exactly once")
        normalized: list[dict] = []
        for question in questions:
            qid = str(question.get("question_id") or "")
            raw = by_id[qid]
            selected = raw.get("selected_option_ids") or []
            if not isinstance(selected, list):
                raise HumanInteractionValidationError(f"{qid}.selected_option_ids must be an array")
            selected_ids = [str(value or "").strip() for value in selected if str(value or "").strip()]
            if len(selected_ids) != len(set(selected_ids)):
                raise HumanInteractionValidationError(f"{qid} contains duplicate option ids")
            option_map = {str(option.get("option_id") or ""): option for option in question.get("options") or []}
            if any(option_id not in option_map for option_id in selected_ids):
                raise HumanInteractionValidationError(f"{qid} contains an unknown option id")
            other_text = _clean_text(raw.get("other_text"), f"{qid}.other_text", maximum=2000, required=False) or None
            skipped = raw.get("skipped", False)
            if not isinstance(skipped, bool):
                raise HumanInteractionValidationError(f"{qid}.skipped must be a boolean")
            notes = _clean_text(raw.get("notes"), f"{qid}.notes", maximum=2000, required=False) or None
            if skipped and (selected_ids or other_text or notes):
                raise HumanInteractionValidationError(f"{qid} cannot include an answer when skipped")
            if not skipped and not selected_ids and not other_text:
                raise HumanInteractionValidationError(f"{qid} requires an option or Other text")
            if not question.get("multi_select") and len(selected_ids) + (1 if other_text else 0) > 1:
                raise HumanInteractionValidationError(f"{qid} is single-select")
            normalized.append(
                {
                    "question_id": qid,
                    "selected_option_ids": selected_ids,
                    "selected_labels": [str(option_map[option_id].get("label") or "") for option_id in selected_ids],
                    "other_text": other_text,
                    "notes": notes,
                    "skipped": skipped,
                }
            )
        return normalized


def get_human_interaction_service() -> HumanInteractionService:
    return HumanInteractionService()


def _register_waiter(session_id: str, kind: str, request_id: str) -> asyncio.Future:
    future = asyncio.get_running_loop().create_future()
    with _WAITERS_LOCK:
        _WAITERS[(session_id, kind, request_id)] = future
    return future


def has_registered_waiter(session_id: str, kind: str, request_id: str) -> bool:
    with _WAITERS_LOCK:
        future = _WAITERS.get((str(session_id or ""), str(kind or ""), str(request_id or "")))
    return bool(future is not None and not future.done())


def _wake_waiter(session_id: str, kind: str, request_id: str, value: dict) -> bool:
    with _WAITERS_LOCK:
        future = _WAITERS.get((session_id, kind, request_id))
    if future is None or future.done():
        return False
    try:
        future.get_loop().call_soon_threadsafe(future.set_result, value)
    except RuntimeError:
        return False
    return True


def _remove_waiter(session_id: str, kind: str, request_id: str, future: asyncio.Future) -> None:
    with _WAITERS_LOCK:
        if _WAITERS.get((session_id, kind, request_id)) is future:
            _WAITERS.pop((session_id, kind, request_id), None)


async def wait_for_user_answers(
    session_id: str,
    arguments: Any,
    *,
    run_id: str = "",
    tool_call_id: str = "",
    emit: Optional[Callable[[dict], Awaitable[None]]] = None,
    interrupt_check: Optional[Callable[[], bool]] = None,
) -> dict:
    service = get_human_interaction_service()
    record = service.create_question(
        session_id, arguments, run_id=run_id, tool_call_id=tool_call_id
    )
    iid = str(record["interaction_id"])
    future = _register_waiter(session_id, "question", iid)
    try:
        latest = service.get(session_id, iid)
        if latest.get("status") != "pending" and not future.done():
            future.set_result(latest)
        if emit is not None:
            await emit({"type": "interaction_requested", **record, "_runtime_v2_committed": True})
        while not future.done():
            if interrupt_check is not None and interrupt_check():
                return service.cancel(session_id, iid, reason="run_interrupted")
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=0.25)
            except asyncio.TimeoutError:
                continue
        return future.result()
    except asyncio.CancelledError:
        try:
            service.cancel(session_id, iid, reason="run_cancelled")
        except Exception:
            pass
        raise
    finally:
        _remove_waiter(session_id, "question", iid, future)
