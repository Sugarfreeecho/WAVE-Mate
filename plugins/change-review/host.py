"""Trusted HTTP adapter for conflict-safe change-review undo."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


_workspace_locks_guard = threading.Lock()
_workspace_locks = {}


def _store_module():
    name = "myagent_change_review_store"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).with_name("store.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load change-review store")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _root_session_id(manager, session_id: str) -> str:
    current = str(session_id or "").strip()
    seen = set()
    while current and current not in seen:
        seen.add(current)
        parent = manager.get_subagent_parent_id(current)
        if not parent:
            return current
        current = str(parent)
    return str(session_id or "").strip()


def _all_known_session_ids(manager) -> set[str]:
    roots = {
        str(row.get("id") or "")
        for row in manager.list_sessions(include_archived=True)
        if isinstance(row, dict) and row.get("id")
    }
    result = set(roots)
    for root in roots:
        try:
            result.update(manager.list_subagent_descendants(root))
        except Exception:
            pass
    return result


def _workspace_key(manager, session_id: str) -> str:
    meta = manager._load_metadata(session_id) or {}
    root = str(meta.get("subagent_work_dir") or meta.get("git_worktree_path") or "").strip()
    if not root:
        from agent_harness import WORK_DIR

        root = str(WORK_DIR)
    return os.path.normcase(os.path.normpath(str(Path(root).resolve())))


def _workspace_lock(key: str) -> threading.Lock:
    with _workspace_locks_guard:
        lock = _workspace_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _workspace_locks[key] = lock
        return lock


def _active_in_workspace(manager, target_session_id: str) -> list[str]:
    from session_lifecycle import is_run_active

    target_key = _workspace_key(manager, target_session_id)
    active = []
    for session_id in _all_known_session_ids(manager):
        try:
            if _workspace_key(manager, session_id) == target_key and is_run_active(session_id):
                active.append(session_id)
        except Exception:
            continue
    return active


def _append_model_notice(manager, session_id: str, content: str) -> None:
    from runtime_v2.history_ops import RuntimeHistoryOps

    RuntimeHistoryOps(
        manager.repository.sessions_dir,
        path_resolver=getattr(manager.repository, "_path_resolver", None),
    ).append_model_message(session_id, "system", content, source="change-review")


def install(app, context, plugin):
    manager = context["session_manager"]
    store_module = _store_module()
    router = APIRouter()

    @router.post("/sessions/{session_id}/change-reviews/undo")
    async def undo_change_review(session_id: str, request: Request):
        from plugins.host import bundled_host_plugin_enabled
        from session_event_bus import publish_session_event

        if not bundled_host_plugin_enabled(plugin.plugin_id):
            return JSONResponse({"ok": False, "error": "plugin disabled"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
        body = body if isinstance(body, dict) else {}
        snapshot_ids = body.get("snapshot_ids")
        operation_id = str(body.get("operation_id") or "").strip()
        if not isinstance(snapshot_ids, list):
            return JSONResponse(
                {"ok": False, "code": "invalid_snapshot", "error": "snapshot_ids must be an array"},
                status_code=422,
            )
        active = await asyncio.to_thread(_active_in_workspace, manager, session_id)
        if active:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "task_running",
                    "error": "change review undo is disabled while this workspace has an active task",
                    "active_session_ids": active,
                },
                status_code=409,
            )
        store = store_module.FileChangeReviewStore(manager._get_session_path(session_id))
        lock = _workspace_lock(_workspace_key(manager, session_id))
        await asyncio.to_thread(lock.acquire)
        lock_acquired = True
        try:
            # A run may have started while the first activity check waited for
            # the workspace mutex. Re-check while owning the same mutex so an
            # undo never proceeds on a newly active task.
            active = await asyncio.to_thread(_active_in_workspace, manager, session_id)
            if active:
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "task_running",
                        "error": "change review undo is disabled while this workspace has an active task",
                        "active_session_ids": active,
                    },
                    status_code=409,
                )
            result = await asyncio.to_thread(store.undo, snapshot_ids, operation_id)
            if result.get("idempotent_replay"):
                return JSONResponse(result)
            event = {
                "type": "file_changes_reverted",
                "session_id": session_id,
                "origin_session_id": session_id,
                "operation_id": operation_id,
                "snapshot_ids": list(result.get("snapshot_ids") or []),
                "changes": list(result.get("changes") or []),
            }
            paths = [str(row.get("path") or "") for row in result.get("changes") or []]
            notice = (
                "[System notification: The user reverted file changes through Change Review. "
                "Treat the workspace files as restored to their pre-tool contents. Paths: "
                + ", ".join(paths)
                + "]"
            )
            root_id = _root_session_id(manager, session_id)
            try:
                await asyncio.to_thread(manager.append_ui_event, session_id, event)
                await asyncio.to_thread(_append_model_notice, manager, session_id, notice)
                if root_id != session_id:
                    await asyncio.to_thread(_append_model_notice, manager, root_id, notice)
                await asyncio.to_thread(store.commit_undo, operation_id)
            except Exception:
                await asyncio.to_thread(store.rollback_undo, operation_id)
                raise
            await publish_session_event(session_id, dict(event))
            if root_id != session_id:
                forwarded = dict(event)
                forwarded["agent_id"] = session_id
                await publish_session_event(root_id, forwarded)
            result["event"] = event
            return JSONResponse(result)
        except store_module.ChangeReviewError as exc:
            return JSONResponse(
                {"ok": False, "code": exc.code, "error": str(exc), "paths": exc.paths},
                status_code=exc.status_code,
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "code": "undo_failed", "error": str(exc)}, status_code=500)
        finally:
            if lock_acquired:
                lock.release()

    app.include_router(router)
