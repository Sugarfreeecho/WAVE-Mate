"""Deleted-session registry and asyncio run-task cancellation on session delete."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_deleted: Set[str] = set()
_run_tasks: Dict[str, Set[asyncio.Task]] = {}
_run_started_at: Dict[str, str] = {}
_run_info_by_task: Dict[asyncio.Task, Dict[str, Any]] = {}


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


def register_run_task(
    session_id: str,
    task: asyncio.Task,
    *,
    run_id: str = "",
    mode: str = "",
) -> None:
    sid = (session_id or "").strip()
    if not sid or task is None:
        return
    started_at = datetime.now(timezone.utc).isoformat()
    with _lock:
        _run_tasks.setdefault(sid, set()).add(task)
        _run_started_at.setdefault(sid, started_at)
        _run_info_by_task[task] = {
            "session_id": sid,
            "run_id": str(run_id or "").strip(),
            "mode": str(mode or "").strip(),
            "started_at": started_at,
            "run_active": True,
            "phase": "running",
            "runtime_v2": True,
        }

    def _on_done(t: asyncio.Task) -> None:
        with _lock:
            _run_info_by_task.pop(t, None)
            bucket = _run_tasks.get(sid)
            if not bucket:
                return
            bucket.discard(t)
            if not bucket:
                _run_tasks.pop(sid, None)
                _run_started_at.pop(sid, None)

    task.add_done_callback(_on_done)


def is_run_active(session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid:
        return False
    with _lock:
        tasks = list(_run_tasks.get(sid, ()))
    return any(t and not t.done() for t in tasks)


def get_run_started_at(session_id: str) -> Optional[str]:
    sid = (session_id or "").strip()
    if not sid:
        return None
    with _lock:
        return _run_started_at.get(sid)


def get_active_run_info(session_id: str) -> Optional[Dict[str, Any]]:
    """Return the newest live task's run identity and lifecycle phase.

    Connection counters are deliberately excluded: an attached or half-closed
    SSE transport is not a lifecycle fact.  Status snapshots use this identity
    so a stale local-task observation cannot reopen the same run after a
    durable terminal event has reached the browser.
    """

    sid = (session_id or "").strip()
    if not sid:
        return None
    with _lock:
        tasks = list(_run_tasks.get(sid, ()))
        rows = [
            dict(_run_info_by_task.get(task) or {})
            for task in tasks
            if task is not None and not task.done() and _run_info_by_task.get(task)
        ]
    if not rows:
        return None
    active_rows = [row for row in rows if bool(row.get("run_active", True))]
    candidates = active_rows or rows
    return max(candidates, key=lambda row: str(row.get("started_at") or ""))


def mark_run_terminal(session_id: str, run_id: str) -> None:
    """Stop exposing a task as active once its durable terminal has committed."""

    sid = (session_id or "").strip()
    rid = str(run_id or "").strip()
    if not sid or not rid:
        return
    with _lock:
        for task in list(_run_tasks.get(sid, ())):
            info = _run_info_by_task.get(task)
            if not isinstance(info, dict):
                continue
            if str(info.get("run_id") or "").strip() != rid:
                continue
            info["run_active"] = False
            info["phase"] = "terminal"


def mark_run_finalizing(session_id: str, run_id: str) -> None:
    """Expose that output is committed while the run completes critical work."""

    sid = (session_id or "").strip()
    rid = str(run_id or "").strip()
    if not sid or not rid:
        return
    with _lock:
        for task in list(_run_tasks.get(sid, ())):
            info = _run_info_by_task.get(task)
            if not isinstance(info, dict):
                continue
            if str(info.get("run_id") or "").strip() == rid and info.get("run_active", True):
                info["phase"] = "finalizing"


async def _cancel_tasks(tasks: List[asyncio.Task], timeout: float = 8.0) -> None:
    pending = [t for t in tasks if t and not t.done()]
    if not pending:
        return
    current_loop = asyncio.get_running_loop()
    for t in pending:
        try:
            task_loop = t.get_loop()
            if task_loop is current_loop:
                t.cancel()
            elif task_loop.is_running():
                task_loop.call_soon_threadsafe(t.cancel)
            else:
                t.cancel()
        except Exception:
            logger.debug("failed to request session task cancellation", exc_info=True)
    deadline = current_loop.time() + max(0.0, float(timeout))
    while any(not task.done() for task in pending) and current_loop.time() < deadline:
        # Registered chat runs may live on the worker thread's event loop.  A
        # foreign-loop Task cannot be passed to gather() on this loop, so wait
        # for its thread-safe cancellation to settle without cross-loop awaits.
        await asyncio.sleep(0.01)
    if any(not task.done() for task in pending):
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
