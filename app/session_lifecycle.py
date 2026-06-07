"""Deleted-session registry and asyncio run-task cancellation on session delete."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Dict, Iterable, List, Set

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_deleted: Set[str] = set()
_run_tasks: Dict[str, Set[asyncio.Task]] = {}


def mark_session_deleted(session_id: str) -> None:
    sid = (session_id or "").strip()
    if sid:
        with _lock:
            _deleted.add(sid)


def mark_sessions_deleted(session_ids: Iterable[str]) -> None:
    for sid in session_ids:
        mark_session_deleted(sid)


def is_session_deleted(session_id: str) -> bool:
    sid = (session_id or "").strip()
    with _lock:
        return bool(sid) and sid in _deleted


def register_run_task(session_id: str, task: asyncio.Task) -> None:
    sid = (session_id or "").strip()
    if not sid or task is None:
        return
    with _lock:
        _run_tasks.setdefault(sid, set()).add(task)

    def _on_done(t: asyncio.Task) -> None:
        with _lock:
            bucket = _run_tasks.get(sid)
            if not bucket:
                return
            bucket.discard(t)
            if not bucket:
                _run_tasks.pop(sid, None)

    task.add_done_callback(_on_done)


def is_run_active(session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid:
        return False
    with _lock:
        tasks = list(_run_tasks.get(sid, ()))
    return any(t and not t.done() for t in tasks)


async def _cancel_tasks(tasks: List[asyncio.Task], timeout: float = 8.0) -> None:
    pending = [t for t in tasks if t and not t.done()]
    if not pending:
        return
    for t in pending:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("session task cancel timeout (%d tasks)", len(pending))


async def cancel_run_tasks(session_ids: Iterable[str]) -> None:
    ids = {(s or "").strip() for s in session_ids if (s or "").strip()}
    if not ids:
        return
    with _lock:
        to_cancel: List[asyncio.Task] = []
        for sid in ids:
            to_cancel.extend(list(_run_tasks.get(sid, ())))
    await _cancel_tasks(to_cancel)


async def stop_session_tree(session_id: str, session_manager, subagent_registry) -> None:
    """Hard-stop parent session, all subagent descendants, and registered asyncio work."""
    sid = session_manager._normalize_session_id(session_id)
    descendants = session_manager.list_subagent_descendants(sid)
    all_ids = [sid, *descendants]
    mark_sessions_deleted(all_ids)

    for x in all_ids:
        try:
            session_manager.request_interrupt(x)
        except Exception:
            pass

    try:
        from tool_approval_gate import reject_pending_approvals_for_sessions

        reject_pending_approvals_for_sessions(all_ids)
    except Exception as e:
        logger.debug("reject tool approvals: %s", e)

    try:
        await subagent_registry.cancel_for_parent(sid, also_ids=set(descendants))
    except Exception as e:
        logger.warning("cancel subagent tasks failed: %s", e)

    await cancel_run_tasks(all_ids)
