"""Hot-path callbacks that connect native file tools to the plugin store."""

from __future__ import annotations

import importlib.util
import hashlib
import sys
from pathlib import Path


def _store_module():
    path = Path(__file__).with_name("store.py").resolve()
    try:
        stat = path.stat()
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        signature = "missing"
    digest = hashlib.sha256(f"{path}:{signature}".encode()).hexdigest()[:16]
    name = f"myagent_change_review_store_{digest}"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load change-review store")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def initialize(host_module):
    store_module = _store_module()
    session_manager = host_module.session_manager

    def before_native_file_tool(state, tool_name, tool_args, tool_call_id, worktree_root=""):
        if tool_name not in {"write_file", "edit_file", "apply_patch", "delete_file"}:
            return None
        session_id = str((state or {}).get("session_id") or "")
        if not session_id:
            return None
        if worktree_root:
            work_root = Path(worktree_root)
        else:
            from agent_tools import active_tool_work_dir

            work_root = active_tool_work_dir()
        store = store_module.FileChangeReviewStore(session_manager._get_session_path(session_id))
        return store.begin_capture(
            tool_name,
            tool_args if isinstance(tool_args, dict) else {},
            run_id=str((state or {}).get("_runtime_v2_run_id") or ""),
            tool_call_id=str(tool_call_id or ""),
            work_root=work_root,
        )

    def after_native_file_tool(state, capture, successful=True):
        session_id = str((state or {}).get("session_id") or "")
        if not session_id or capture is None:
            return []
        store = store_module.FileChangeReviewStore(session_manager._get_session_path(session_id))
        return store.finish_capture(capture, successful=bool(successful))

    def referenced_snapshot_ids(session_id):
        ids = set()
        for event in session_manager._load_ui_events_for_active_runtime(session_id):
            if not isinstance(event, dict):
                continue
            if event.get("type") == "tool_call":
                changes = ((event.get("ui") or {}).get("changes") or [])
                ids.update(
                    str(row.get("snapshot_id") or "")
                    for row in changes if isinstance(row, dict) and row.get("snapshot_id")
                )
            elif event.get("type") == "file_changes_reverted":
                ids.update(str(item or "") for item in event.get("snapshot_ids") or [])
        return ids

    def history_truncated(session_id):
        store = store_module.FileChangeReviewStore(session_manager._get_session_path(session_id))
        if store.index_path.is_file():
            store.prune_unreferenced(referenced_snapshot_ids(session_id))

    def session_branched(source_session_id, target_session_id):
        source = store_module.FileChangeReviewStore(session_manager._get_session_path(source_session_id))
        if source.index_path.is_file():
            source.copy_referenced_to(
                session_manager._get_session_path(target_session_id),
                # The branch starts with the source history. Resolve the
                # references from that history before copying; the target has
                # no UI events yet (and therefore no references to discover).
                referenced_snapshot_ids(source_session_id),
            )

    return {
        "before_native_file_tool": before_native_file_tool,
        "after_native_file_tool": after_native_file_tool,
        "history_truncated": history_truncated,
        "session_branched": session_branched,
    }
