"""
基于 FastAPI + SSE 的 Web 壳：聊天流、会话 CRUD、历史加载。

大事件在 agent_loop.astream_events 中产生；本模块负责 JSON 行协议与分块刷出（sleep(0)）。
"""

import asyncio
import functools
import json
import logging
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from agent import (
    astream_events,
    astream_events_continuation,
    is_session_title_generation_pending,
    session_manager,
)
from agent_harness import (
    PROJECT_ROOT,
    WORK_DIR,
    _invalidate_executor_config_cache,
    dotenv_file_path,
    key_context_body_for_system_prompt,
    normalize_prompt_language,
    refresh_executor_client_from_env,
)
from human_interaction import ASK_USER_ENV_VAR, ask_user_enabled
from agent_loop import (
    abort_session_steer_run,
    build_combined_tool_definitions_for_session,
    compute_context_tokens_for_session,
    enqueue_session_steer,
    get_context_token_mode,
    get_session_steer,
    list_session_steers,
    remove_session_steer,
    transition_session_steer,
)
from session_lifecycle import get_run_started_at, is_run_active
from session_event_bus import (
    add_event_listener,
    publish_session_event,
    subscribe_session_events,
)
import agent_mcp
from agent_tools import discover_skills, set_skill_enabled
import model_profiles
import execution_metrics
from path_picker_util import pick_native_path

from notification_providers import notify_user

_PATH_PICKER_JS_PATH = Path(__file__).resolve().parent / "templates" / "static" / "myagent_path_picker.js"
_SETUP_I18N_JS_PATH = Path(__file__).resolve().parent / "templates" / "static" / "setup_i18n.js"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_DIST_INDEX = _TEMPLATES_DIR / "dist" / "index.html"
_DIST_ASSETS = _TEMPLATES_DIR / "dist" / "assets"
_EXECUTION_METRICS_PAYLOAD_TTL_SEC = max(
    1.0,
    float(os.getenv("EXECUTION_METRICS_PAYLOAD_TTL_SEC", "3")),
)
_execution_metrics_payload_lock = threading.Lock()
_execution_metrics_payload_cached_at = 0.0
_execution_metrics_payload_cache = b""
_execution_metrics_sessions_cached_at = 0.0
_execution_metrics_sessions_cache = b""
_VIEWABLE_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".tif",
    ".tiff",
    ".avif",
    ".jfif",
}
_PLAYABLE_AUDIO_SUFFIXES = {
    ".mp3",
    ".wav",
    ".ogg",
    ".oga",
    ".opus",
    ".m4a",
    ".aac",
    ".flac",
}
_PLAYABLE_VIDEO_SUFFIXES = {
    ".mp4",
    ".webm",
    ".ogv",
    ".mov",
}
_VIEWABLE_MEDIA_SUFFIXES = (
    _VIEWABLE_IMAGE_SUFFIXES
    | _PLAYABLE_AUDIO_SUFFIXES
    | _PLAYABLE_VIDEO_SUFFIXES
)
_WORKSPACE_MEDIA_MIME_OVERRIDES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
}

# SSE 响应头：降低反向代理/浏览器对小块的缓冲
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_CHAT_SSE_KEEPALIVE_SEC = max(5.0, float(os.getenv("CHAT_SSE_KEEPALIVE_SEC", "15")))
CHAT_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024
CHAT_UPLOAD_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_CHAT_UPLOAD_MULTIPART_OVERHEAD_BYTES = 4 * 1024 * 1024

@asynccontextmanager
async def _webui_lifespan(_app: FastAPI):
    """Default lifecycle for tests and alternate ASGI launchers."""
    await start_webui_lifecycle()
    try:
        yield
    finally:
        await stop_webui_lifecycle()


fastapi_app = FastAPI(lifespan=_webui_lifespan)
try:
    from agent_extensions import load_plugins
    from plugins.host import install_bundled_host_extensions

    install_bundled_host_extensions(
        fastapi_app,
        load_plugins(force=True).plugins,
        {"session_manager": session_manager, "project_root": PROJECT_ROOT},
    )
    from workflow_extensions import activate_bundled_workflow_callbacks

    activate_bundled_workflow_callbacks(sys.modules["agent_loop"])
except Exception:
    logging.getLogger(__name__).exception("Bundled host plugin installation failed")

if _DIST_ASSETS.is_dir():
    fastapi_app.mount(
        "/assets",
        StaticFiles(directory=str(_DIST_ASSETS)),
        name="dist_assets",
    )
UI_LOG_TRUNCATE_KEEP_LINES = max(10, int(os.getenv("UI_LOG_TRUNCATE_KEEP_LINES", "100")))

# 正在向前端推流的 /chat 连接数（按 session）。刷新页面后仍可根据此项显示「生成中」黄点。
_active_chat_by_session: dict[str, int] = {}
# 上次活跃时间戳（按 session），用于清理僵尸计数器（浏览器非正常关闭导致未递减）
_active_chat_last_seen: dict[str, float] = {}
# Read-only reconnect/observer streams are deliberately separate from run
# producers. An observer must never keep an orphaned run "active" or cause 409.
_observer_streams_by_session: dict[str, int] = {}

_CHAT_ACTIVE_TIMEOUT_SEC = int(os.getenv("CHAT_ACTIVE_TIMEOUT_SEC", "300"))
_RUNTIME_V2_ORPHAN_GRACE_SEC = max(
    0,
    int(os.getenv("RUNTIME_V2_ORPHAN_GRACE_SEC", "30")),
)
_chat_start_lock = threading.RLock()
_chat_starting_by_session: dict[str, tuple[float, str]] = {}
logger = logging.getLogger(__name__)
_STATIC_TEXT_CACHE_LOCK = threading.Lock()
_STATIC_TEXT_CACHE: dict[str, tuple[tuple[bool, int, int], str]] = {}

_RUNTIME_SYNC_LOCK = threading.Lock()
_RUNTIME_SYNC_QUEUE: deque[str] = deque()
_RUNTIME_SYNC_STATUS: dict[str, dict] = {}
_RUNTIME_SYNC_WORKER: Optional[threading.Thread] = None
_RUNTIME_SYNC_EXTRA_WORKERS: set[threading.Thread] = set()
_history_op_locks: dict[str, threading.Lock] = {}
_history_op_locks_guard = threading.Lock()
_react_recovery_scan_task: Optional[asyncio.Task] = None
_react_recovery_workers: dict[str, asyncio.Task] = {}
_react_recovery_attempt_at: dict[str, float] = {}
_human_interaction_recovery_workers: dict[str, asyncio.Task] = {}
_REACT_RECOVERY_RETRY_SECONDS = max(
    5.0,
    float(os.getenv("REACT_RECOVERY_RETRY_SECONDS", "30")),
)
_RUNTIME_SYNC_CANCEL = threading.Event()
_RUNTIME_SYNC_SLEEP_SEC = float(os.getenv("RUNTIME_SYNC_QUEUE_SLEEP_SEC", "0.2"))
_RUNTIME_AUTO_MIGRATION_SCAN_STARTED = False
_RUNTIME_AUTO_MIGRATION_STATUS: dict[str, Any] = {"state": "idle"}
_RUNTIME_AUTO_MIGRATION_DELAY_SEC = max(
    0.0,
    float(os.getenv("RUNTIME_V2_AUTO_MIGRATE_DELAY_SEC", "3")),
)
_UI_CLOSED_NOTIFY_ENABLED = (
    os.getenv("MYAGENT_UI_CLOSED_NOTIFY", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
_UI_CLOSED_NOTIFY_GRACE_SEC = max(
    1.0,
    float(os.getenv("MYAGENT_UI_CLOSED_NOTIFY_DELAY_SEC", "3")),
)
_UI_PRESENCE_TOKEN_TTL_SEC = max(90.0, float(os.getenv("MYAGENT_UI_PRESENCE_TTL_SEC", "300")))
_ui_presence_lock = threading.Lock()
_hook_pipeline_warmup_task: Optional[asyncio.Task] = None
_ui_presence_tokens: dict[str, dict[str, Any]] = {}
_ui_activation_seq = 0
_ui_activation_path = "/"
_ui_activation_session = ""
_last_ui_session_id = ""
_ui_closed_notify_task: Optional[asyncio.Task] = None
_ui_attention_notify_lock = threading.Lock()
_ui_attention_notify_reasons: dict[str, set[str]] = {}
_ui_attention_notify_task: Optional[asyncio.Task] = None
_UI_ATTENTION_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
_last_ui_attention_notify_at = 0.0


def _history_op_lock(session_id: str) -> threading.Lock:
    sid = str(session_id or "").strip() or "__empty__"
    with _history_op_locks_guard:
        lock = _history_op_locks.get(sid)
        if lock is None:
            lock = threading.Lock()
            _history_op_locks[sid] = lock
        return lock


def _run_history_op_locked(session_id: str, fn, *args, **kwargs):
    with _history_op_lock(session_id):
        return fn(*args, **kwargs)


def _runtime_sync_file_sig(path: Path) -> dict:
    try:
        st = path.stat()
        return {"exists": True, "mtime_ns": int(st.st_mtime_ns), "size": int(st.st_size)}
    except OSError:
        return {"exists": False, "mtime_ns": 0, "size": 0}


def _runtime_sync_paths(session_id: str) -> dict[str, Path]:
    from runtime_v2.ui_projection import RuntimeUiProjection

    projection = RuntimeUiProjection(
        session_manager.repository.sessions_dir,
        path_resolver=session_manager._resolve_session_path,
    )
    session_path = Path(session_manager._resolve_session_path(session_id))
    def manager_path(method_name: str, fallback_name: str) -> Path:
        method = getattr(session_manager, method_name, None)
        return Path(method(session_id)) if callable(method) else session_path / fallback_name

    return {
        "legacy_ui": manager_path("_get_ui_events_path", "ui_events.json"),
        "legacy_model": manager_path("_get_llm_history_path", "llm_history.json"),
        "legacy_context": manager_path("_get_key_context_path", "key_context.md"),
        "legacy_todo": manager_path("_get_todo_plan_path", "todo_plan.md"),
        "runtime_events": projection.event_log.event_path(session_id),
        "manifest": session_path / "runtime_v2_migration.json",
    }


def _runtime_sync_fingerprints(session_id: str) -> dict:
    return {
        key: _runtime_sync_file_sig(path)
        for key, path in _runtime_sync_paths(session_id).items()
        if key != "manifest"
    }


def _read_runtime_migration_manifest(session_id: str) -> dict:
    path = _runtime_sync_paths(session_id)["manifest"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _runtime_sync_needed(session_id: str) -> tuple[bool, str, dict]:
    try:
        fingerprints = _runtime_sync_fingerprints(session_id)
        legacy_keys = ("legacy_ui", "legacy_model", "legacy_context", "legacy_todo")
        legacy_present = any(
            bool((fingerprints.get(key) or {}).get("exists"))
            and int((fingerprints.get(key) or {}).get("size") or 0) > 0
            for key in legacy_keys
        )
        runtime_present = bool((fingerprints.get("runtime_events") or {}).get("exists"))
        manifest = _read_runtime_migration_manifest(session_id)
        detail = {
            "fingerprints": fingerprints,
            "manifest_version": manifest.get("manifest_version"),
            "manifest_status": manifest.get("status"),
        }
        if not legacy_present:
            return False, "no_legacy_data", detail
        if not runtime_present:
            return True, "runtime_missing", detail
        recorded = manifest.get("file_fingerprints")
        if isinstance(recorded, dict) and recorded == fingerprints:
            if manifest.get("status") == "blocked":
                return False, "blocked_unchanged", detail
            return False, "verified_unchanged", detail
        legacy_keys = ("legacy_ui", "legacy_model", "legacy_context", "legacy_todo")
        if isinstance(recorded, dict):
            legacy_changed = any(recorded.get(key) != fingerprints.get(key) for key in legacy_keys)
            if not legacy_changed:
                return False, "runtime_changed_only", detail
            return True, "legacy_changed", detail

        # Sessions produced by an older migration release have no fingerprints.
        # Only enqueue automatically when legacy is newer than the V2 event log;
        # scanning every already-authoritative V2 session at startup causes large
        # avoidable disk contention.  Administrators can still request a full
        # verification through the explicit sync-all endpoint.
        runtime_mtime = int((fingerprints.get("runtime_events") or {}).get("mtime_ns") or 0)
        legacy_mtime = max(
            int((fingerprints.get(key) or {}).get("mtime_ns") or 0)
            for key in legacy_keys
        )
        if legacy_mtime > runtime_mtime + 1_000_000:
            return True, "legacy_newer", detail
        return False, "runtime_authoritative_unverified", detail
    except Exception as exc:
        return False, f"check_failed:{exc}", {}


def _runtime_v2_legacy_only_migration_pending(session_id: str) -> dict:
    """Cheap gate preventing an old V1 session from opening as an empty V2 one."""
    try:
        fingerprints = _runtime_sync_fingerprints(session_id)
        runtime_present = bool((fingerprints.get("runtime_events") or {}).get("exists"))
        legacy_present = any(
            bool((fingerprints.get(key) or {}).get("exists"))
            and int((fingerprints.get(key) or {}).get("size") or 0) > 0
            for key in ("legacy_ui", "legacy_model", "legacy_context", "legacy_todo")
        )
    except Exception as exc:
        return {"pending": False, "error": str(exc)}
    if runtime_present or not legacy_present:
        return {"pending": False}
    queued = _enqueue_runtime_sync(session_id, "auto_on_open", check_needed=True)
    return {
        "pending": True,
        "queued": bool(queued.get("queued")),
        "reason": queued.get("reason") or "runtime_missing",
        "retry_after_ms": 250,
    }


def _runtime_sync_worker_loop() -> None:
    import time as _time

    while not _RUNTIME_SYNC_CANCEL.is_set():
        with _RUNTIME_SYNC_LOCK:
            if not _RUNTIME_SYNC_QUEUE:
                return
            sid = _RUNTIME_SYNC_QUEUE.popleft()
            status = dict(_RUNTIME_SYNC_STATUS.get(sid) or {})
            status.update({
                "state": "running",
                "started_at": _time.time(),
                "queued": False,
            })
            _RUNTIME_SYNC_STATUS[sid] = status
        t0 = _time.perf_counter()
        try:
            result = _sync_runtime_session(
                sid,
                automatic=bool(status.get("automatic")),
            )
            state = "done" if result.get("ok") else ("blocked" if result.get("blocked") else "failed")
            error = result.get("error")
        except Exception as exc:
            result = {"ok": False, "session_id": sid, "error": str(exc)}
            state = "failed"
            error = str(exc)
            logger.warning("background runtime sync failed for %s: %s", sid, exc)
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        with _RUNTIME_SYNC_LOCK:
            status = dict(_RUNTIME_SYNC_STATUS.get(sid) or {})
            status.update({
                "state": state,
                "queued": False,
                "finished_at": _time.time(),
                "elapsed_ms": elapsed_ms,
                "result": result,
            })
            if error:
                status["error"] = error
            else:
                status.pop("error", None)
            _RUNTIME_SYNC_STATUS[sid] = status
        if _RUNTIME_SYNC_SLEEP_SEC > 0:
            _time.sleep(_RUNTIME_SYNC_SLEEP_SEC)


def _ensure_runtime_sync_worker_locked(*, urgent: bool = False) -> None:
    global _RUNTIME_SYNC_WORKER, _RUNTIME_SYNC_EXTRA_WORKERS
    _RUNTIME_SYNC_EXTRA_WORKERS = {
        worker for worker in _RUNTIME_SYNC_EXTRA_WORKERS if worker.is_alive()
    }
    if _RUNTIME_SYNC_WORKER is not None and _RUNTIME_SYNC_WORKER.is_alive():
        if urgent and not _RUNTIME_SYNC_EXTRA_WORKERS:
            worker = threading.Thread(
                target=_runtime_sync_worker_loop,
                name="runtime-sync-urgent-worker",
                daemon=True,
            )
            _RUNTIME_SYNC_EXTRA_WORKERS.add(worker)
            worker.start()
        return
    _RUNTIME_SYNC_CANCEL.clear()
    _RUNTIME_SYNC_WORKER = threading.Thread(
        target=_runtime_sync_worker_loop,
        name="runtime-sync-worker",
        daemon=True,
    )
    _RUNTIME_SYNC_WORKER.start()


def _enqueue_runtime_sync(session_id: str, reason: str = "manual", *, check_needed: bool = False) -> dict:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    detail = {}
    needed = True
    check_reason = reason
    if check_needed:
        needed, check_reason, detail = _runtime_sync_needed(sid)
        if not needed:
            return {"ok": True, "session_id": sid, "queued": False, "reason": check_reason, "detail": detail}
    with _RUNTIME_SYNC_LOCK:
        existing = _RUNTIME_SYNC_STATUS.get(sid) or {}
        if existing.get("state") == "running" or sid in _RUNTIME_SYNC_QUEUE:
            return {"ok": True, "session_id": sid, "queued": True, "deduped": True, "reason": existing.get("reason") or reason}
        urgent = reason in {"auto_on_open", "manual"}
        if urgent:
            _RUNTIME_SYNC_QUEUE.appendleft(sid)
        else:
            _RUNTIME_SYNC_QUEUE.append(sid)
        _RUNTIME_SYNC_STATUS[sid] = {
            "state": "queued",
            "queued": True,
            "reason": check_reason,
            "trigger": reason,
            "automatic": str(reason or "").startswith("auto_"),
            "detail": detail,
        }
        _ensure_runtime_sync_worker_locked(urgent=urgent)
    return {"ok": True, "session_id": sid, "queued": True, "reason": check_reason, "detail": detail}


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def schedule_runtime_auto_migration() -> dict:
    """Start one low-priority V1->V2 scan without delaying UI startup."""
    global _RUNTIME_AUTO_MIGRATION_SCAN_STARTED

    try:
        from runtime_v2 import runtime_v2_primary

        enabled = (
            runtime_v2_primary()
            and _env_enabled("RUNTIME_V2_AUTO_MIGRATE_LEGACY", True)
            and _env_enabled("RUNTIME_V2_AUTO_MIGRATE_STARTUP", False)
        )
    except Exception:
        enabled = False
    if not enabled:
        return {"ok": True, "scheduled": False, "reason": "startup_scan_disabled"}
    with _RUNTIME_SYNC_LOCK:
        if _RUNTIME_AUTO_MIGRATION_SCAN_STARTED:
            return {"ok": True, "scheduled": False, "reason": "already_scheduled"}
        _RUNTIME_AUTO_MIGRATION_SCAN_STARTED = True
        _RUNTIME_AUTO_MIGRATION_STATUS.clear()
        _RUNTIME_AUTO_MIGRATION_STATUS.update({"state": "scheduled", "started_at": None})

    def scan() -> None:
        import time as _time

        if _RUNTIME_AUTO_MIGRATION_DELAY_SEC:
            _time.sleep(_RUNTIME_AUTO_MIGRATION_DELAY_SEC)
        with _RUNTIME_SYNC_LOCK:
            _RUNTIME_AUTO_MIGRATION_STATUS.update({"state": "scanning", "started_at": _time.time()})
        queued = skipped = failed = 0
        try:
            rows = session_manager.list_sessions(include_archived=True)
            session_ids = {
                str((row or {}).get("id") or "").strip()
                for row in rows
                if str((row or {}).get("id") or "").strip()
            }
            try:
                session_ids.update(
                    str(value or "").strip()
                    for pair in session_manager._load_subagent_index().items()
                    for value in pair
                    if str(value or "").strip()
                )
            except Exception:
                pass
            limit = max(0, int(os.getenv("RUNTIME_V2_AUTO_MIGRATE_LIMIT", "0") or 0))
            ordered = sorted(session_ids)
            if limit:
                ordered = ordered[:limit]
            for sid in ordered:
                result = _enqueue_runtime_sync(sid, "auto_startup", check_needed=True)
                if not result.get("ok"):
                    failed += 1
                elif result.get("queued"):
                    queued += 1
                else:
                    skipped += 1
        except Exception as exc:
            failed += 1
            logger.warning("Runtime V2 automatic migration scan failed: %s", exc)
        finally:
            with _RUNTIME_SYNC_LOCK:
                _RUNTIME_AUTO_MIGRATION_STATUS.update({
                    "state": "done" if failed == 0 else "failed",
                    "finished_at": _time.time(),
                    "queued": queued,
                    "skipped": skipped,
                    "failed": failed,
                })

    threading.Thread(target=scan, name="runtime-v2-auto-migration-scan", daemon=True).start()
    return {"ok": True, "scheduled": True, "reason": "runtime_v2_startup"}


def _read_text_cached(path: Path, fallback: str = "") -> str:
    try:
        st = path.stat()
        sig = (True, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        sig = (False, 0, 0)
        return fallback
    key = str(path.resolve())
    with _STATIC_TEXT_CACHE_LOCK:
        cached = _STATIC_TEXT_CACHE.get(key)
        if cached and cached[0] == sig:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        _STATIC_TEXT_CACHE[key] = (sig, text)
        return text

def _cleanup_stale_active_chat():
    import time as _t
    now = _t.time()
    stale = [sid for sid, ts in list(_active_chat_last_seen.items()) if now - ts > _CHAT_ACTIVE_TIMEOUT_SEC]
    for sid in stale:
        _active_chat_by_session.pop(sid, None)
        _active_chat_last_seen.pop(sid, None)
    with _chat_start_lock:
        stale_starting = []
        for sid, entry in list(_chat_starting_by_session.items()):
            ts = entry[0] if isinstance(entry, tuple) else entry
            if now - float(ts or 0.0) > _CHAT_ACTIVE_TIMEOUT_SEC:
                stale_starting.append(sid)
        for sid in stale_starting:
            _chat_starting_by_session.pop(sid, None)


def _is_session_stream_active(sid: str) -> bool:
    x = str(sid or "").strip()
    if not x:
        return False
    with _chat_start_lock:
        if x in _chat_starting_by_session:
            return True
    return bool(_session_run_state_fields(x).get("stream_active"))


def _reserve_session_chat_start(sid: str, run_id: str = "") -> Optional[str]:
    x = str(sid or "").strip()
    if not x:
        return ""
    with _chat_start_lock:
        if x in _chat_starting_by_session:
            return None
    followup_takeover = False
    if os.getenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            followup_takeover = session_manager.get_interrupt_reason(x) == "followup"
        except Exception:
            followup_takeover = False
    if bool(_session_run_state_fields(x).get("stream_active")) and not followup_takeover:
        return None
    with _chat_start_lock:
        if x in _chat_starting_by_session:
            return None
        import time as _t
        token = str(run_id or "").strip() or str(uuid.uuid4())
        _chat_starting_by_session[x] = (_t.time(), token)
        return token


def _release_session_chat_start(sid: str, token: str = "") -> None:
    x = str(sid or "").strip()
    if not x:
        return
    with _chat_start_lock:
        expected = str(token or "").strip()
        current = _chat_starting_by_session.get(x)
        if expected and isinstance(current, tuple) and current[1] != expected:
            return
        _chat_starting_by_session.pop(x, None)


def _runtime_v2_active_run_info(sid: str) -> dict:
    sid = str(sid or "").strip()
    if not sid:
        return {}
    try:
        active_runs = _runtime_v2_filtered_active_runs(sid)
        if not active_runs:
            return {}
        first = active_runs[0] if isinstance(active_runs[0], dict) else {}
        run_id = str(first.get("run_id") or "").strip()
        started_at = first.get("started_at") or first.get("heartbeat_at")
        return {
            "session_id": sid,
            "run_id": run_id,
            "run_active": True,
            "started_at": started_at,
            "runtime_v2": True,
            "active_run_count": len(active_runs),
        }
    except Exception as exc:
        logger.debug("Runtime V2 active run read failed for %s: %s", sid, exc)
        return {}


def _has_local_run_activity(sid: str) -> bool:
    sid = str(sid or "").strip()
    if not sid:
        return False
    if bool(is_run_active(sid)):
        return True
    if int(_active_chat_by_session.get(sid, 0) or 0) > 0:
        return True
    with _chat_start_lock:
        return sid in _chat_starting_by_session


def _has_local_worker_activity(sid: str) -> bool:
    sid = str(sid or "").strip()
    if not sid:
        return False
    if bool(is_run_active(sid)):
        return True
    with _chat_start_lock:
        return sid in _chat_starting_by_session


def _session_pending_human_counts(session_id: str) -> dict[str, int]:
    """Return durable pending interaction counts for recovery gating."""
    try:
        from human_interaction import get_human_interaction_service

        counts = get_human_interaction_service().pending_counts(str(session_id))
        questions = max(0, int(counts.get("questions") or 0))
        approvals = max(0, int(counts.get("approvals") or 0))
        return {
            "questions": questions,
            "approvals": approvals,
            "total": questions + approvals,
        }
    except Exception:
        return {"questions": 0, "approvals": 0, "total": 0}


def _session_pending_human_count(session_id: str) -> int:
    """Total pending ask_user questions + tool approvals for one session."""
    return int(_session_pending_human_counts(session_id).get("total") or 0)


def _session_pending_human_question_count(session_id: str) -> int:
    """Pending ask_user questions only; storage failures preserve old behavior."""
    return int(_session_pending_human_counts(session_id).get("questions") or 0)


def _session_was_manually_stopped(session_id: str) -> bool:
    """Return whether the durable interrupt marker represents an explicit user stop."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    try:
        if not session_manager.is_interrupt_requested(sid):
            return False
        reason = str(session_manager.get_interrupt_reason(sid) or "").strip().lower()
    except Exception:
        return False
    return reason in {"user", "user_button", "manual", "manual_stop", "stop_button"}


def _append_recovered_question_tool_result(record: dict) -> bool:
    """Close an orphaned ask_user tool call exactly once before continuation."""
    sid = str((record or {}).get("session_id") or "").strip()
    interaction_id = str((record or {}).get("interaction_id") or "").strip()
    tool_call_id = str((record or {}).get("tool_call_id") or "").strip()
    if not sid or not interaction_id or not tool_call_id:
        return False
    operation_id = "interaction-recovery-tool:" + interaction_id
    from runtime_v2 import RuntimeHistoryOps, SnapshotStore

    resolver = getattr(session_manager, "_resolve_session_path", None)
    snapshot = SnapshotStore(
        session_manager.repository.sessions_dir,
        path_resolver=resolver,
    ).read_consistent(sid)
    if operation_id in set(str(value) for value in snapshot.get("operation_ids") or []):
        return False
    content = json.dumps(
        {
            "status": str(record.get("status") or "resolved"),
            "interaction_id": interaction_id,
            "answers": record.get("answers") or [],
            **({"reason": record.get("reason")} if record.get("reason") else {}),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    RuntimeHistoryOps(
        session_manager.repository.sessions_dir,
        path_resolver=resolver,
    ).append_model_message(
        sid,
        "tool",
        content,
        tool_call_id=tool_call_id,
        operation_id=operation_id,
        run_id=str(record.get("run_id") or "") or None,
    )
    return True


async def _run_human_interaction_recovery_background(session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    recovery_run_id = "interaction-recovery-" + uuid.uuid4().hex
    start_token = _reserve_session_chat_start(sid, recovery_run_id) or ""
    if not start_token:
        return
    try:
        def should_stop(sid_: str) -> bool:
            return session_manager.is_interrupt_requested(sid_)

        async for _event in astream_events_continuation(
            sid,
            should_stop=should_stop,
            require_pending_subagents=False,
            recovery_reason="human_interaction_resolved_after_restart",
            run_id=recovery_run_id,
            continuation_source="recovery",
        ):
            pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Human interaction recovery failed for %s: %s", sid, exc)
    finally:
        _release_session_chat_start(sid, start_token)


def _schedule_human_interaction_recovery(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    existing = _human_interaction_recovery_workers.get(sid)
    if existing is not None and not existing.done():
        return False
    task = asyncio.create_task(
        _run_human_interaction_recovery_background(sid),
        name=f"interaction-recovery-{sid}",
    )
    _human_interaction_recovery_workers[sid] = task

    def cleanup(done: asyncio.Task, session_id_: str = sid) -> None:
        if _human_interaction_recovery_workers.get(session_id_) is done:
            _human_interaction_recovery_workers.pop(session_id_, None)

    task.add_done_callback(cleanup)
    return True


def _discover_recoverable_react_sessions() -> list[str]:
    """Return every non-archived interrupted ReAct session safe to resume."""
    from workflow_extensions import session_workflows
    recoverable: list[str] = []
    for row in session_manager.list_sessions(include_archived=False):
        sid = str((row or {}).get("id") or "").strip()
        if (
            not sid
            or _has_local_worker_activity(sid)
            or int(_active_chat_by_session.get(sid, 0) or 0) > 0
        ):
            continue
        try:
            # Optional workflows own their durable continuation scheduling and
            # must not be started a second time by generic ReAct recovery.
            if session_workflows.continuation_source(sid):
                continue
            # The normal app lifespan reconciles orphan runs before this scan.
            # Keep direct `uvicorn webui:fastapi_app` startup equally correct.
            _cleanup_orphan_runtime_v2_active_runs(
                sid,
                reason="no_local_activity",
                respect_grace=False,
            )
            if _session_pending_human_count(sid) > 0:
                continue
            if not _runtime_v2_auto_resume_pending(sid):
                continue
            if session_manager.can_continue_react_session(sid):
                recoverable.append(sid)
        except Exception:
            logger.debug("ReAct recovery discovery failed for %s", sid, exc_info=True)
    return recoverable


async def _run_react_recovery_background(session_id: str) -> None:
    """Drain a recovered run on the server so it is independent of the UI."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    recovery_run_id = "react-recovery-" + uuid.uuid4().hex
    start_token = _reserve_session_chat_start(sid, recovery_run_id) or ""
    if not start_token:
        return
    try:
        if _session_pending_human_count(sid) > 0:
            return
        if not _runtime_v2_auto_resume_pending(sid):
            return
        if not session_manager.can_continue_react_session(sid):
            return

        def should_stop(sid_: str) -> bool:
            return session_manager.is_interrupt_requested(sid_)

        async for _event in astream_events_continuation(
            sid,
            should_stop=should_stop,
            require_pending_subagents=False,
            recovery_reason="process_or_network_interruption",
            run_id=recovery_run_id,
            continuation_source="recovery",
        ):
            # The agent loop persists durable events and publishes them to all
            # observers. This worker only owns/drains execution.
            pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Background ReAct recovery failed for %s: %s", sid, exc)
    finally:
        _release_session_chat_start(sid, start_token)


def _schedule_react_recovery(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    existing = _react_recovery_workers.get(sid)
    if existing is not None and not existing.done():
        return False
    now = time.monotonic()
    if now - float(_react_recovery_attempt_at.get(sid) or 0.0) < _REACT_RECOVERY_RETRY_SECONDS:
        return False
    _react_recovery_attempt_at[sid] = now
    task = asyncio.create_task(
        _run_react_recovery_background(sid),
        name=f"react-recovery-{sid}",
    )
    _react_recovery_workers[sid] = task

    def cleanup(done: asyncio.Task, session_id_: str = sid) -> None:
        if _react_recovery_workers.get(session_id_) is done:
            _react_recovery_workers.pop(session_id_, None)

    task.add_done_callback(cleanup)
    return True


async def recover_interrupted_react_sessions() -> list[str]:
    """Discover and schedule all recoverable sessions without changing UI state."""
    session_ids = await asyncio.to_thread(_discover_recoverable_react_sessions)
    return [sid for sid in session_ids if _schedule_react_recovery(sid)]


async def start_react_recovery_runner() -> bool:
    """Start one non-blocking recovery scan; safe to call more than once."""
    global _react_recovery_scan_task
    if _react_recovery_scan_task and not _react_recovery_scan_task.done():
        return False
    _react_recovery_scan_task = asyncio.create_task(
        recover_interrupted_react_sessions(),
        name="react-recovery-scan",
    )
    return True


async def stop_react_recovery_runner() -> None:
    global _react_recovery_scan_task
    scan = _react_recovery_scan_task
    _react_recovery_scan_task = None
    if scan and not scan.done():
        scan.cancel()
    workers = [task for task in _react_recovery_workers.values() if task and not task.done()]
    for task in workers:
        task.cancel()
    pending = [task for task in [scan, *workers] if task is not None]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _react_recovery_workers.clear()


def _runtime_v2_timestamp_age_seconds(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _runtime_v2_active_runs_are_recent(snapshot: dict, max_age_seconds: Optional[int] = None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    max_age = int(max_age_seconds if max_age_seconds is not None else _RUNTIME_V2_ORPHAN_GRACE_SEC)
    if max_age <= 0:
        return False
    active_runs = snapshot.get("active_runs")
    if not isinstance(active_runs, list) or not active_runs:
        return False
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        for key in ("heartbeat_at", "updated_at", "started_at"):
            age = _runtime_v2_timestamp_age_seconds(run.get(key))
            if age is not None and age <= max_age:
                return True
    age = _runtime_v2_timestamp_age_seconds(snapshot.get("updated_at"))
    return bool(age is not None and age <= max_age)


def _has_running_subagent_activity(sid: str) -> bool:
    sid = str(sid or "").strip()
    if not sid:
        return False
    try:
        from agent_subagent import subagent_registry

        flat = session_manager.list_subagents_flat(
            sid,
            running_checker=subagent_registry.is_running,
            include_dialogue_turns=False,
        )
        return any(bool(n.get("running")) for n in flat if isinstance(n, dict))
    except Exception as exc:
        logger.debug("running subagent check failed for %s: %s", sid, exc)
        return False


def _runtime_v2_snapshot(sid: str, *, fail_closed: bool = False) -> dict:
    sid = str(sid or "").strip()
    if not sid:
        return {}
    try:
        from runtime_v2.event_log import SessionEventLog
        from runtime_v2.projector import RuntimeProjector
        from runtime_v2.snapshot_store import SnapshotStore

        root = session_manager.repository.sessions_dir
        resolver = session_manager._resolve_session_path
        return SnapshotStore(root, path_resolver=resolver).read_consistent(
            sid,
            SessionEventLog(root, path_resolver=resolver),
            RuntimeProjector(),
        )
    except Exception as exc:
        logger.debug("Runtime V2 snapshot read failed for %s: %s", sid, exc)
        if fail_closed:
            raise
        return {}


def _runtime_v2_extensions_snapshot(sid: str) -> dict:
    """Read the small plugin-owned projection used by session badges and panels."""

    sid = str(sid or "").strip()
    if not sid:
        return {"extensions": {}}
    from runtime_v2 import SessionExtensionStateStore

    store = SessionExtensionStateStore(
        session_manager.repository.sessions_dir,
        path_resolver=getattr(
            session_manager,
            "_resolve_session_path",
            getattr(session_manager.repository, "_path_resolver", None),
        ),
    )
    return {"extensions": store.read_all_lightweight(sid)}


def _runtime_v2_filtered_active_runs(sid: str) -> list[dict]:
    snapshot = _runtime_v2_snapshot(sid)
    active_runs = snapshot.get("active_runs") if isinstance(snapshot, dict) else None
    if not isinstance(active_runs, list) or not active_runs:
        return []
    if (
        not _has_local_run_activity(sid)
        and not _runtime_v2_active_runs_are_recent(snapshot)
        and not _has_running_subagent_activity(sid)
    ):
        return []
    runs = snapshot.get("runs") if isinstance(snapshot, dict) else None
    latest_started = ""
    latest_started_seq = 0
    if isinstance(runs, dict):
        for run in runs.values():
            if not isinstance(run, dict):
                continue
            try:
                seq = int(run.get("started_seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            if seq > latest_started_seq:
                latest_started_seq = seq
            started = str(run.get("started_at") or "")
            if started > latest_started:
                latest_started = started
    filtered = []
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        try:
            run_seq = int(run.get("started_seq") or 0)
        except (TypeError, ValueError):
            run_seq = 0
        if latest_started_seq and run_seq and run_seq < latest_started_seq:
            continue
        if latest_started and str(run.get("started_at") or "") < latest_started:
            continue
        filtered.append(run)
    return filtered


def _cleanup_orphan_runtime_v2_active_runs(
    sid: str,
    reason: str = "orphaned",
    *,
    respect_grace: bool = True,
) -> int:
    try:
        from runtime_v2 import runtime_v2_primary

        if not runtime_v2_primary():
            return 0
    except Exception:
        return 0
    sid = str(sid or "").strip()
    if not sid or _has_local_run_activity(sid) or _has_running_subagent_activity(sid):
        return 0
    snapshot = _runtime_v2_snapshot(sid)
    active_runs = snapshot.get("active_runs") if isinstance(snapshot, dict) else None
    if not isinstance(active_runs, list) or not active_runs:
        return 0
    if respect_grace and _runtime_v2_active_runs_are_recent(snapshot):
        logger.debug(
            "Skip orphan Runtime V2 cleanup for recent active run(s): session=%s grace=%ss",
            sid,
            _RUNTIME_V2_ORPHAN_GRACE_SEC,
        )
        return 0
    cleaned = 0
    try:
        from runtime_v2.mirror import RuntimeMirror

        mirror = RuntimeMirror(
            session_manager.sessions_dir,
            path_resolver=session_manager._resolve_session_path,
        )
        for run in active_runs:
            if not isinstance(run, dict):
                continue
            rid = str(run.get("run_id") or "").strip()
            if not rid:
                continue
            mirror.mirror_run_interrupted(sid, rid, {"reason": reason})
            cleaned += 1
    except Exception as e:
        logger.debug("cleanup orphan runtime v2 active runs failed for %s: %s", sid, e)
        return 0
    if cleaned:
        logger.info("Cleaned %s orphan Runtime V2 active run(s) for session %s", cleaned, sid)
    return cleaned


def _runtime_v2_context_snapshot(sid: str) -> dict:
    snapshot = _runtime_v2_snapshot(sid, fail_closed=True)
    context = snapshot.get("context") if isinstance(snapshot, dict) else None
    return context if isinstance(context, dict) else {}


def _runtime_v2_auto_resume_pending(sid: str) -> bool:
    # A pending durable interaction is an intentional pause. Only the
    # interaction resolve/cancel path may append its tool result and resume.
    if _session_pending_human_count(sid) > 0:
        return False
    # Runtime cancellation can append a later generic `cancelled` event after
    # the stop endpoint persisted `user_button`. The durable session marker is
    # authoritative so that this event-order race cannot restart the session.
    if _session_was_manually_stopped(sid):
        return False
    try:
        from runtime_v2 import runtime_v2_primary

        if not runtime_v2_primary():
            return False
    except Exception:
        return False
    snapshot = _runtime_v2_snapshot(sid)
    runs = snapshot.get("runs") if isinstance(snapshot, dict) else None
    if not isinstance(runs, dict) or not runs:
        return False
    rows = [run for run in runs.values() if isinstance(run, dict)]
    if not rows:
        return False
    latest = max(rows, key=lambda run: int(run.get("finished_seq") or run.get("heartbeat_seq") or run.get("started_seq") or 0))
    return (
        str(latest.get("status") or "") == "interrupted"
        and str(latest.get("reason") or "") in {
            "no_local_activity",
            "orphaned",
            "process_restart",
            "cancelled",
            "unspecified",
        }
    )


def _empty_todo_plan_snapshot(source: str) -> dict:
    return {
        "has_plan": False,
        "items": [],
        "done": 0,
        "total": 0,
        "source": source,
    }


def _runtime_v2_todo_plan_snapshot(session_id: str) -> dict:
    snapshot = _runtime_v2_snapshot(session_id, fail_closed=True)
    extensions = snapshot.get("extensions") if isinstance(snapshot, dict) else None
    plugin_state = extensions.get("session-todo") if isinstance(extensions, dict) else None
    row = plugin_state.get("plan") if isinstance(plugin_state, dict) else None
    todo = row.get("value") if isinstance(row, dict) else None
    if isinstance(todo, dict):
        out = dict(todo)
        out.setdefault("source", "extension_state")
        return out
    # Transitional read compatibility for snapshots produced before Todo moved
    # to the domain-neutral extension-state service. New writes never use it.
    legacy_todo = snapshot.get("todo") if isinstance(snapshot, dict) else None
    if isinstance(legacy_todo, dict):
        out = dict(legacy_todo)
        out.setdefault("source", "legacy_runtime_v2_snapshot")
        return out
    return _empty_todo_plan_snapshot("runtime_v2_snapshot")


def _runtime_v2_event_dicts(session_id: str, *, after_seq: int = 0, before_seq: Optional[int] = None, limit: int = 200) -> list[dict]:
    from runtime_v2.event_log import SessionEventLog

    log = SessionEventLog(
        session_manager.repository.sessions_dir,
        path_resolver=session_manager._resolve_session_path,
    )
    lim = max(1, min(int(limit or 200), 1000))
    if before_seq is not None:
        events = log.read_before_seq(session_id, int(before_seq), lim)
    elif int(after_seq or 0) > 0:
        events = log.read_after_seq(session_id, int(after_seq or 0))[:lim]
    else:
        events = log.read_latest(session_id, lim)
    return [ev.to_dict() for ev in events]


def _runtime_v2_chat_sse_payload(session_id: str, event_dict: dict) -> Optional[dict]:
    try:
        from runtime_v2.event_schema import RuntimeEvent
        from runtime_v2.ui_projection import RuntimeUiProjection

        event = RuntimeEvent.from_dict(event_dict)
        runtime_seq = int(event.seq)
        run_id = event.run_id or str((event.payload or {}).get("run_id") or "")
        ui_event: Optional[dict] = None
        skip_ui = False
        if event.type == "run_started":
            ui_event = {"type": "run_started", "run_id": run_id, "ephemeral": True}
        elif event.type == "run_finished":
            ui_event = {"type": "run_finished", "run_id": run_id, "ephemeral": True}
        elif event.type == "run_interrupted":
            ui_event = {"type": "run_interrupted", "run_id": run_id, "ephemeral": True}
        elif event.type == "run_failed":
            payload = dict(event.payload or {})
            ui_event = {
                "type": "run_failed",
                "run_id": run_id,
                "error": payload.get("error") or "",
                "ephemeral": True,
            }
        elif event.type == "runtime_resumed":
            resume_payload = dict(event.payload or {})
            seconds = max(0.0, float(resume_payload.get("suspended_seconds") or 0.0))
            cause = str(resume_payload.get("cause") or "process_suspended")
            ui_event = {
                "type": "runtime_resumed",
                "run_id": run_id,
                "content": (
                    "检测到系统睡眠约 %.0f 秒，任务已恢复"
                    if cause == "system_sleep"
                    else "检测到 Agent 进程暂停约 %.0f 秒，任务已恢复"
                ) % seconds,
                "ephemeral": True,
                **resume_payload,
            }
        elif event.type in {"message_user", "user_turn_committed"}:
            # Regular /chat user messages are rendered optimistically in the
            # browser. Steer messages are intentionally not, so they must be
            # projected through this live Runtime V2 stream.
            if str((event.payload or {}).get("ui_type") or "") == "user_steer":
                projection = RuntimeUiProjection(
                    session_manager.repository.sessions_dir,
                    path_resolver=session_manager._resolve_session_path,
                )
                ui_event = projection._event_to_ui(session_id, event)
            else:
                skip_ui = True
        else:
            projection = RuntimeUiProjection(
                session_manager.repository.sessions_dir,
                path_resolver=session_manager._resolve_session_path,
            )
            ui_event = projection._event_to_ui(session_id, event)
        payload = {
            "protocol": "runtime_v2",
            "type": "runtime_v2_event",
            "session_id": session_id,
            "seq": runtime_seq,
            "runtime_seq": runtime_seq,
            "runtime_event": event_dict,
        }
        if skip_ui:
            payload["skip_ui"] = True
        if ui_event is not None:
            ui_event = dict(ui_event)
            ui_event.setdefault("session_id", session_id)
            ui_event["runtime_seq"] = runtime_seq
            payload["ui_event"] = ui_event
        return payload
    except Exception as exc:
        logger.debug("Runtime V2 chat SSE payload mapping failed for %s: %s", session_id, exc)
        return None


def _runtime_v2_ephemeral_sse_payload(session_id: str, event: dict) -> dict:
    payload = {
        "protocol": "runtime_v2",
        "type": "runtime_v2_ephemeral",
        "session_id": session_id,
        "ui_event": dict(event or {}),
    }
    if isinstance(event, dict) and event.get("run_id"):
        payload["run_id"] = event.get("run_id")
    return payload


def _user_turns_from_ui_events(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for idx, event in enumerate(events or []):
        if not isinstance(event, dict) or event.get("type") != "user":
            continue
        raw = event.get("content")
        text = raw if isinstance(raw, str) else str(raw or "")
        preview = re.sub(r"\s+", " ", text).strip()
        if len(preview) > 180:
            preview = preview[:177] + "..."
        out.append({"event_index": idx, "preview": preview})
    return out


def _runtime_v2_chat_protocol_enabled(requested: str, sid: Optional[str]) -> bool:
    if str(requested or "").strip().lower() != "runtime_v2":
        return False
    if not sid:
        return False
    try:
        from runtime_v2 import runtime_v2_primary

        return bool(runtime_v2_primary())
    except Exception:
        return False


def _runtime_v2_debug_state(include_archived: bool = True) -> dict:
    try:
        from runtime_v2.config import runtime_version
    except Exception:
        runtime_version = lambda: 1
    sessions = session_manager.list_sessions(include_archived=include_archived)
    active_runs: list[dict] = []
    session_rows: list[dict] = []
    for item in sessions:
        sid = str((item or {}).get("id") or "").strip()
        if not sid:
            continue
        snapshot = _runtime_v2_snapshot(sid)
        snapshot_active = snapshot.get("active_runs") if isinstance(snapshot, dict) else []
        filtered_active = _runtime_v2_filtered_active_runs(sid)
        if filtered_active:
            active_runs.extend([dict(run, session_id=sid) for run in filtered_active if isinstance(run, dict)])
        session_rows.append({
            "id": sid,
            "name": item.get("name"),
            "archived": bool(item.get("archived")),
            "snapshot_seq": int(snapshot.get("last_seq") or 0) if isinstance(snapshot, dict) else 0,
            "snapshot_active_run_count": len(snapshot_active) if isinstance(snapshot_active, list) else 0,
            "active_run_count": len(filtered_active),
            "updated_at": snapshot.get("updated_at") if isinstance(snapshot, dict) else None,
        })
    return {
        "runtime_version": int(runtime_version()),
        "session_count": len(session_rows),
        "active_run_count": len(active_runs),
        "active_runs": active_runs,
        "sessions": session_rows,
    }


def _runtime_v2_active_run_ids(sid: str) -> list[str]:
    active_runs = _runtime_v2_filtered_active_runs(sid)
    out: list[str] = []
    for run in active_runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "").strip()
        if run_id:
            out.append(run_id)
    return out


def _interrupt_runtime_v2_active_runs(sid: str, run_id: str = "", reason: str = "user") -> list[str]:
    try:
        from runtime_v2 import runtime_v2_primary

        if not runtime_v2_primary():
            return []
    except Exception:
        return []
    targets = [str(run_id or "").strip()] if str(run_id or "").strip() else _runtime_v2_active_run_ids(sid)
    targets = [rid for rid in targets if rid]
    if not targets:
        return []
    reason = str(reason or "user").strip() or "user"
    try:
        from runtime_v2.mirror import RuntimeMirror

        mirror = RuntimeMirror(
            session_manager.sessions_dir,
            path_resolver=session_manager._resolve_session_path,
        )
        for rid in targets:
            mirror.mirror_run_interrupted(sid, rid, {"reason": reason})
    except Exception as e:
        logger.debug("mirror interrupt failed for %s: %s", sid, e)
    return targets


def _session_run_state_fields(sid: str) -> dict:
    sid = str(sid or "").strip()
    if not sid:
        return {
            "stream_active": False,
            "run_active": False,
            "run_started_at": None,
            "active_run": None,
        }
    stream_connections = int(_active_chat_by_session.get(sid, 0) or 0)
    try:
        from runtime_v2 import runtime_v2_primary
    except Exception:
        runtime_v2_primary = lambda: True
    if runtime_v2_primary():
        # Status reads must be observational. Orphan reconciliation writes a
        # durable run_interrupted event and therefore belongs only to explicit
        # lifecycle/recovery paths, never to GET /sessions or busy checks.
        v2_info = _runtime_v2_active_run_info(sid)
        if not v2_info:
            return {
                "stream_active": False,
                "run_active": False,
                "run_started_at": None,
                "stream_connections": stream_connections,
                "active_run": None,
            }
        started_at = v2_info.get("started_at")
        return {
            "stream_active": True,
            "run_active": True,
            "run_started_at": started_at,
            "stream_connections": stream_connections,
            "active_run": dict(v2_info, stream_connections=stream_connections),
        }
    legacy_run_active = bool(is_run_active(sid))
    started_at = get_run_started_at(sid)
    return {
        "stream_active": legacy_run_active,
        "run_active": legacy_run_active,
        "run_started_at": started_at,
        "stream_connections": stream_connections,
        "active_run": {
            "session_id": sid,
            "stream_connections": stream_connections,
            "run_active": legacy_run_active,
            "started_at": started_at,
            "runtime_v2": False,
        } if legacy_run_active else None,
    }


def _session_run_state_fields_light(sid: str) -> dict:
    sid = str(sid or "").strip()
    stream_connections = int(_active_chat_by_session.get(sid, 0) or 0) if sid else 0
    if not sid:
        return {
            "stream_active": False,
            "run_active": False,
            "run_started_at": None,
            "stream_connections": stream_connections,
            "active_run": None,
        }
    local_run_active = bool(is_run_active(sid))
    starting = False
    with _chat_start_lock:
        starting = sid in _chat_starting_by_session
    run_active = bool(local_run_active or starting or stream_connections > 0)
    started_at = get_run_started_at(sid) if local_run_active else None
    try:
        from runtime_v2 import runtime_v2_primary
        is_runtime_v2 = bool(runtime_v2_primary())
    except Exception:
        is_runtime_v2 = False
    return {
        "stream_active": run_active,
        "run_active": run_active,
        "run_started_at": started_at,
        "stream_connections": stream_connections,
        "active_run": {
            "session_id": sid,
            "stream_connections": stream_connections,
            "run_active": run_active,
            "started_at": started_at,
            "runtime_v2": is_runtime_v2,
            "lightweight": True,
        } if run_active else None,
    }


def _build_sessions_state_snapshot(include_archived: bool = False) -> dict:
    import time as _time

    t0 = _time.perf_counter()
    sessions = session_manager.list_sessions(include_archived=include_archived)
    archived_count = session_manager.archived_session_count()
    _cleanup_stale_active_chat()
    active_runs = []
    pending_subagents = {}
    try:
        from human_interaction import get_human_interaction_service

        human_interaction_service = get_human_interaction_service()
    except Exception:
        logger.exception("Failed to initialize pending human-interaction state for /sessions/state")
        human_interaction_service = None
    pending_counts_by_session = {}
    if human_interaction_service is not None:
        try:
            pending_counts_by_session = human_interaction_service.pending_counts_many(
                str(item.get("id") or "") for item in sessions if item.get("id")
            )
        except Exception:
            logger.exception("Failed to batch pending human-interaction state for /sessions/state")
    for s in sessions:
        sid = s.get("id")
        if not sid:
            s["stream_active"] = False
            s["pending_human_interactions"] = {"questions": 0, "approvals": 0, "total": 0}
            continue
        sid = str(sid)
        run_state = _session_run_state_fields_light(sid)
        s["stream_active"] = bool(run_state["stream_active"])
        s["run_active"] = bool(run_state["run_active"])
        s["run_started_at"] = run_state["run_started_at"]
        s["title_generation_pending"] = is_session_title_generation_pending(sid)
        try:
            pending_counts = (
                pending_counts_by_session.get(sid)
                or human_interaction_service.pending_counts(sid)
                if human_interaction_service is not None
                else {"questions": 0, "approvals": 0, "total": 0}
            )
        except Exception:
            logger.exception("Failed to read pending human-interaction state session_id=%s", sid)
            pending_counts = {"questions": 0, "approvals": 0, "total": 0}
        s["pending_human_interactions"] = {
            "questions": int(pending_counts.get("questions") or 0),
            "approvals": int(pending_counts.get("approvals") or 0),
            "total": int(pending_counts.get("total") or 0),
        }
        if run_state.get("active_run"):
            active_runs.append(run_state["active_run"])
    out = {
        "seq": int(_time.time() * 1000),
        "sessions": sessions,
        "archived_count": archived_count,
        "active_runs": active_runs,
        "pending_subagents": pending_subagents,
    }
    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    if elapsed_ms >= 500:
        logger.warning(
            "/sessions/state slow include_archived=%s sessions=%s elapsed_ms=%s",
            include_archived,
            len(sessions),
            elapsed_ms,
        )
    return out


# The sidebar polls /sessions/state every few seconds.  A rebuild walks all
# sessions (seconds on a busy disk) and a burst of polls then starves real
# work like message startup, so serve a short-TTL snapshot in between.
_SESSIONS_STATE_TTL_SEC = 5.0
_sessions_state_cache: dict = {
    False: {"ts": 0.0, "payload": None},
    True: {"ts": 0.0, "payload": None},
}
_sessions_state_cache_lock = threading.Lock()
_sessions_state_build_locks = {False: threading.Lock(), True: threading.Lock()}
_sessions_state_refreshing: set[bool] = set()


def _refresh_sessions_state_cache(key: bool) -> None:
    import time as _time

    try:
        payload = _build_sessions_state_snapshot(include_archived=key)
        with _sessions_state_cache_lock:
            _sessions_state_cache[key] = {
                "ts": _time.monotonic(),
                "payload": payload,
            }
    except Exception:
        logger.exception("Background /sessions/state refresh failed")
    finally:
        with _sessions_state_cache_lock:
            _sessions_state_refreshing.discard(key)


def _build_sessions_state_snapshot_cached(include_archived: bool = False) -> dict:
    import time as _time

    now = _time.monotonic()
    key = bool(include_archived)
    with _sessions_state_cache_lock:
        cached = _sessions_state_cache[key]
        if (
            cached["payload"] is not None
            and now - float(cached["ts"] or 0.0) < _SESSIONS_STATE_TTL_SEC
        ):
            return cached["payload"]
        # Once a snapshot exists, never make a polling browser wait for the
        # next disk scan. One daemon refreshes it while every concurrent caller
        # receives the stale-but-complete value, preventing refresh stampedes.
        if cached["payload"] is not None:
            if key not in _sessions_state_refreshing:
                _sessions_state_refreshing.add(key)
                threading.Thread(
                    target=_refresh_sessions_state_cache,
                    args=(key,),
                    name=f"sessions-state-refresh-{int(key)}",
                    daemon=True,
                ).start()
            return cached["payload"]

    # The first request has no stale value to serve. Serialize that cold build
    # per include_archived variant, then re-check in case another request won.
    build_lock = _sessions_state_build_locks[key]
    with build_lock:
        with _sessions_state_cache_lock:
            cached = _sessions_state_cache[key]
            if cached["payload"] is not None:
                return cached["payload"]
        payload = _build_sessions_state_snapshot(include_archived=key)
        with _sessions_state_cache_lock:
            _sessions_state_cache[key] = {
                "ts": _time.monotonic(),
                "payload": payload,
            }
        return payload

def get_index_html():
    """读取并返回 Vite 构建产物 templates/dist/index.html。"""
    import agent_harness as _ui_ah

    work_dir = str(WORK_DIR.resolve())
    sessions_dir = str((WORK_DIR / "sessions").resolve())
    app_dotenv = str(dotenv_file_path().resolve())
    ctx_thr = _ui_ah.CONTEXT_WINDOW
    default_steer_mode = str(os.getenv("MYAGENT_STEER_MODE", "append") or "append").strip().lower()
    if default_steer_mode not in {"interrupt", "append"}:
        default_steer_mode = "append"
    from security import security_enabled

    feature_flags = {
        "askUser": ask_user_enabled(),
        "followupRestart": os.getenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1").strip().lower() in {"1", "true", "yes", "on"},
        "streamReconnect": os.getenv("MYAGENT_ENABLE_STREAM_RECONNECT", "1").strip().lower() in {"1", "true", "yes", "on"},
        "finalReconcile": os.getenv("MYAGENT_ENABLE_FINAL_RECONCILE", "1").strip().lower() in {"1", "true", "yes", "on"},
        "smoothStream": os.getenv("MYAGENT_SMOOTH_STREAM_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"},
        "security": security_enabled(),
    }
    inject = (
        "<script>"
        f"window.__UI_LOG_TRUNCATE_KEEP_LINES__={UI_LOG_TRUNCATE_KEEP_LINES};"
        f"window.__CONTEXT_WINDOW__={ctx_thr};"
        f"window.__WORK_DIR__={json.dumps(work_dir)};"
        f"window.__SESSIONS_DIR__={json.dumps(sessions_dir)};"
        f"window.__APP_DOTENV_PATH__={json.dumps(app_dotenv)};"
        f"window.__MYAGENT_STEER_MODE__={json.dumps(default_steer_mode)};"
        f"window.__MYAGENT_FEATURES__={json.dumps(feature_flags)};"
        "</script>"
    )
    if _DIST_INDEX.is_file():
        try:
            html = _DIST_INDEX.read_text(encoding="utf-8")
            html = html.replace("</head>", inject + "</head>", 1)
            return html
        except OSError:
            pass
    return (
        "<h1>UI not built</h1>"
        "<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>"
    )

@fastapi_app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return HTMLResponse(
        content=get_index_html(),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _resolve_allowed_local_path(
    raw_value: str, require_file: bool = False
) -> Path:
    """Resolve a native absolute or virtual workspace path.

    Native absolute paths (drive-letter, POSIX, or UNC) are returned when
    they exist on disk; no workspace-root restriction is applied. Slash-rooted
    and relative values retain the historic virtual-workspace meaning and are
    resolved under WORK_DIR.
    """

    raw = unquote(raw_value or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("empty path")

    work_root = WORK_DIR.resolve()
    windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", raw))
    native = Path(raw).expanduser()
    native_absolute = windows_absolute or native.is_absolute()

    if native_absolute:
        resolved_native = Path(os.path.abspath(str(native))).resolve()
        if resolved_native.exists():
            candidate = resolved_native
        else:
            virtual = (work_root / raw.lstrip("/\\")).resolve()
            if virtual.exists():
                candidate = virtual
            else:
                raise FileNotFoundError(raw)
    else:
        rel_raw = raw.lstrip("/\\")
        candidate = (work_root / rel_raw).resolve()
        first = rel_raw.replace("\\", "/").split("/", 1)[0]
        if first and first.lower() == WORK_DIR.name.lower() and not candidate.exists():
            candidate = (WORK_DIR.parent / rel_raw).resolve()

    if require_file:
        if not candidate.is_file():
            raise FileNotFoundError(raw)
    elif not candidate.exists() or not (candidate.is_file() or candidate.is_dir()):
        raise FileNotFoundError("file or directory does not exist")
    return candidate


@fastapi_app.get("/api/open-workspace-file")
async def open_workspace_file(
    rel: str = Query("", description="工作区相对路径、虚拟 /path 或本机绝对路径"),
):
    """
    用系统默认应用打开本机文件。浏览器禁止从 http(s) 页面跳转 file://，故通过后端 os.startfile / open / xdg-open 打开。
    """
    import os
    import platform
    import subprocess

    raw = unquote(rel or "").strip().strip('"').strip("'")
    if not raw:
        return JSONResponse({"ok": False, "error": "路径为空"}, status_code=400)
    try:
        safe_path = await asyncio.wait_for(
            run_in_threadpool(_resolve_allowed_local_path, raw, False),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "path check timed out"}, status_code=504)
    except PermissionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
    except FileNotFoundError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    def _open_detached() -> None:
        p = str(safe_path)
        sysname = platform.system()
        if sysname == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        elif sysname == "Darwin":
            subprocess.Popen(["open", p], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", p], close_fds=True)

    threading.Thread(target=_open_detached, name="open-workspace-file", daemon=True).start()
    return JSONResponse({"ok": True, "path": str(safe_path)})


def _resolve_workspace_view_path(raw_value: str) -> Path:
    raw = unquote(raw_value or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("empty path")
    return _resolve_allowed_local_path(raw, True)


def _svg_length_px(raw_value: Any) -> Optional[float]:
    text = str(raw_value or "").strip()
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*(px|pt|pc|mm|cm|in)?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    scale = {
        "": 1.0,
        "px": 1.0,
        "pt": 96.0 / 72.0,
        "pc": 16.0,
        "mm": 96.0 / 25.4,
        "cm": 96.0 / 2.54,
        "in": 96.0,
    }.get((match.group(2) or "").lower())
    if scale is None or value <= 0:
        return None
    return value * scale


def _svg_image_dimensions(path: Path) -> tuple[int, int]:
    import xml.etree.ElementTree as ET

    with path.open("rb") as source:
        root = next(
            (element for _event, element in ET.iterparse(source, events=("start",))),
            None,
        )
    if root is None or str(root.tag).rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("invalid SVG root")
    width = _svg_length_px(root.attrib.get("width"))
    height = _svg_length_px(root.attrib.get("height"))
    if width is None or height is None:
        view_box = re.split(r"[\s,]+", str(root.attrib.get("viewBox") or "").strip())
        if len(view_box) == 4:
            try:
                view_width = float(view_box[2])
                view_height = float(view_box[3])
            except (TypeError, ValueError):
                view_width = view_height = 0
            if view_width > 0 and view_height > 0:
                if width is None and height is None:
                    width, height = view_width, view_height
                elif width is None:
                    width = height * view_width / view_height
                elif height is None:
                    height = width * view_height / view_width
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError("SVG has no usable intrinsic dimensions")
    return max(1, round(width)), max(1, round(height))


@functools.lru_cache(maxsize=512)
def _cached_workspace_image_dimensions(
    path_value: str,
    modified_ns: int,
    file_size: int,
) -> tuple[int, int]:
    del modified_ns, file_size  # 这两个参数用于文件变化时自动生成新的缓存键。
    path = Path(path_value)
    if path.suffix.lower() == ".svg":
        return _svg_image_dimensions(path)
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        try:
            orientation = int(image.getexif().get(274, 1) or 1)
        except Exception:
            orientation = 1
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    if width <= 0 or height <= 0:
        raise ValueError("image has no usable intrinsic dimensions")
    return int(width), int(height)


def _workspace_image_metadata_item(rel: str) -> dict[str, Any]:
    path = _resolve_workspace_view_path(rel)
    if path.suffix.lower() not in _VIEWABLE_IMAGE_SUFFIXES:
        raise ValueError("not a supported image")
    stat = path.stat()
    width, height = _cached_workspace_image_dimensions(
        str(path),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )
    return {
        "rel": rel,
        "width": width,
        "height": height,
        "version": f"{stat.st_mtime_ns:x}-{stat.st_size:x}",
    }


async def _workspace_media_response(
    rel: str,
    *,
    allowed_suffixes: set[str],
    kind_label: str,
):
    try:
        cand = await run_in_threadpool(_resolve_workspace_view_path, rel)
    except ValueError:
        return JSONResponse({"ok": False, "error": "path is empty"}, status_code=400)
    except PermissionError:
        return JSONResponse({"ok": False, "error": "path outside allowed roots"}, status_code=403)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": f"{kind_label} not found"}, status_code=404)
    except Exception as exc:
        logger.warning("workspace %s resolve failed: %s", kind_label, exc)
        return JSONResponse({"ok": False, "error": f"invalid {kind_label} path"}, status_code=400)
    suffix = cand.suffix.lower()
    if suffix not in allowed_suffixes:
        return JSONResponse(
            {"ok": False, "error": f"not a supported {kind_label}"},
            status_code=415,
        )
    media_type = (
        _WORKSPACE_MEDIA_MIME_OVERRIDES.get(suffix)
        or mimetypes.guess_type(str(cand))[0]
        or "application/octet-stream"
    )
    return FileResponse(
        str(cand),
        media_type=media_type,
        filename=cand.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@fastapi_app.get("/api/workspace-media")
async def workspace_media(
    rel: str = Query("", description="Media path relative to workspace, or a native absolute path"),
):
    return await _workspace_media_response(
        rel,
        allowed_suffixes=_VIEWABLE_MEDIA_SUFFIXES,
        kind_label="media",
    )


@fastapi_app.post("/api/workspace-image-metadata")
async def workspace_image_metadata(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    rels = body.get("rels") if isinstance(body, dict) else None
    if not isinstance(rels, list):
        return JSONResponse({"ok": False, "error": "rels must be a list"}, status_code=400)
    clean_rels = list(
        dict.fromkeys(
            str(rel).strip()
            for rel in rels[:128]
            if isinstance(rel, str) and str(rel).strip()
        )
    )

    def _collect() -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        for rel in clean_rels:
            try:
                images.append(_workspace_image_metadata_item(rel))
            except (ValueError, PermissionError, FileNotFoundError, OSError):
                continue
            except Exception as exc:
                logger.debug("workspace image metadata failed for %s: %s", rel, exc)
        return images

    try:
        images = await asyncio.wait_for(run_in_threadpool(_collect), timeout=5.0)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "metadata timed out"}, status_code=504)
    return JSONResponse(
        {"ok": True, "images": images},
        headers={"Cache-Control": "no-store"},
    )


@fastapi_app.get("/api/workspace-image")
async def workspace_image(
    rel: str = Query("", description="Image path relative to workspace, or a native absolute path"),
):
    return await _workspace_media_response(
        rel,
        allowed_suffixes=_VIEWABLE_IMAGE_SUFFIXES,
        kind_label="image",
    )


def _html_with_path_picker_script(body: str) -> str:
    try:
        v = int(_PATH_PICKER_JS_PATH.stat().st_mtime)
    except OSError:
        v = 0
    tag = f'<script src="/static/myagent_path_picker.js?v={v}"></script>'
    if tag in body:
        return body
    if "</head>" in body:
        return body.replace("</head>", tag + "</head>", 1)
    return tag + body


@fastapi_app.get("/static/myagent_path_picker.js")
async def serve_path_picker_js():
    content = _read_text_cached(_PATH_PICKER_JS_PATH, "")
    if not content:
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=content, media_type="application/javascript")


@fastapi_app.get("/static/setup_i18n.js")
async def serve_setup_i18n_js():
    content = _read_text_cached(_SETUP_I18N_JS_PATH, "")
    if not content:
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=content, media_type="application/javascript")


def _safe_upload_filename(name: str) -> str:
    raw = Path(str(name or "upload.bin")).name.strip()
    if not raw or raw in (".", ".."):
        raw = "upload.bin"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw).strip(" .")
    return safe or "upload.bin"


def _dedupe_upload_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem or "upload"
    suffix = dest.suffix
    parent = dest.parent
    for i in range(2, 10000):
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    raise RuntimeError("too many duplicate upload filenames")


class _ChatUploadLimitError(Exception):
    pass


_WORKSPACE_FILE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".trash",
    ".tool_results",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "sessions",
    "skills",
}


def _workspace_file_item(path: Path, root: Path) -> dict:
    st = path.stat()
    rel = str(path.relative_to(root)).replace("\\", "/")
    return {
        "kind": "file",
        "name": path.name,
        "path": str(path),
        "rel": rel,
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
    }


def _workspace_dir_item(path: Path, root: Path) -> dict:
    rel = str(path.relative_to(root)).replace("\\", "/")
    return {
        "kind": "directory",
        "name": path.name,
        "path": str(path),
        "rel": rel,
    }


def _is_workspace_visible_dir(name: str) -> bool:
    return name not in _WORKSPACE_FILE_SKIP_DIRS and not name.startswith(".venv")


def _resolve_workspace_rel_dir(rel_dir: str) -> Path:
    root = WORK_DIR.resolve()
    rel = str(rel_dir or "").strip().replace("\\", "/").strip("/")
    target = (root / rel).resolve() if rel else root
    target.relative_to(root)
    if not target.is_dir():
        raise FileNotFoundError(rel or ".")
    return target


def _list_workspace_dir(rel_dir: str) -> list[dict]:
    root = WORK_DIR.resolve()
    target = _resolve_workspace_rel_dir(rel_dir)
    dirs: list[dict] = []
    files: list[dict] = []
    with os.scandir(target) as it:
        for entry in it:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if _is_workspace_visible_dir(entry.name):
                        dirs.append(_workspace_dir_item(Path(entry.path), root))
                elif entry.is_file(follow_symlinks=False):
                    files.append(_workspace_file_item(Path(entry.path), root))
            except OSError:
                continue
    dirs.sort(key=lambda item: str(item.get("name") or "").lower())
    files.sort(key=lambda item: str(item.get("name") or "").lower())
    return dirs + files


def _scan_workspace_files(query: str) -> list[dict]:
    root = WORK_DIR.resolve()
    q = (query or "").strip().lower()
    terms = [x for x in re.split(r"\s+", q) if x]
    matches: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if _is_workspace_visible_dir(d)
        ]
        base = Path(dirpath)
        if terms:
            for dirname in dirnames:
                path = base / dirname
                try:
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    hay = rel.lower()
                    if all(term in hay for term in terms):
                        matches.append(_workspace_dir_item(path, root))
                except OSError:
                    continue
        for filename in filenames:
            path = base / filename
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
                hay = rel.lower()
                if terms and not all(term in hay for term in terms):
                    continue
                matches.append(_workspace_file_item(path, root))
            except OSError:
                continue
    if terms:
        def score(item: dict) -> tuple[int, int, str]:
            rel = str(item.get("rel") or "").lower()
            name = str(item.get("name") or "").lower()
            first = terms[0] if terms else ""
            rank = 0
            if name.startswith(first):
                rank = -3
            elif rel.startswith(first):
                rank = -2
            elif first and first in name:
                rank = -1
            return rank, len(rel), rel
        matches.sort(key=score)
    else:
        matches.sort(key=lambda item: float(item.get("mtime") or 0), reverse=True)
    return matches


@fastapi_app.get("/api/workspace-files")
async def list_workspace_files(
    q: str = Query("", max_length=200),
    dir: str = Query("", max_length=1000),
):
    try:
        query = (q or "").strip()
        if query:
            files = await run_in_threadpool(_scan_workspace_files, query)
        else:
            files = await run_in_threadpool(_list_workspace_dir, dir)
        return JSONResponse({"ok": True, "root": str(WORK_DIR.resolve()), "files": files})
    except Exception as exc:
        logger.warning("workspace file scan failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.get("/api/skills")
async def list_registered_skills():
    try:
        skills = await run_in_threadpool(lambda: discover_skills(include_disabled=True))
        public = [
            {
                "name": str(skill.get("name") or ""),
                "description": str(skill.get("description") or ""),
                "enabled": skill.get("enabled") is not False,
            }
            for skill in skills
            if str(skill.get("name") or "").strip()
        ]
        return JSONResponse({"ok": True, "skills": public})
    except Exception as exc:
        logger.warning("skills scan failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/skills/{skill_name}/enabled")
async def set_registered_skill_enabled(skill_name: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    enabled = data.get("enabled") if isinstance(data, dict) else None
    if not isinstance(enabled, bool):
        return JSONResponse({"ok": False, "error": "enabled must be boolean"}, status_code=400)
    name = str(skill_name or "").strip()
    available = {
        str(skill.get("name") or "")
        for skill in await run_in_threadpool(lambda: discover_skills(include_disabled=True))
    }
    if name not in available:
        return JSONResponse({"ok": False, "error": "unknown skill"}, status_code=404)
    await run_in_threadpool(set_skill_enabled, name, enabled)
    return JSONResponse({"ok": True, "name": name, "enabled": enabled})


@fastapi_app.post("/api/upload-chat-files")
async def upload_chat_files(files: list[UploadFile] = File(...)):
    if not files:
        return JSONResponse({"ok": False, "error": "no files"}, status_code=400)
    declared_total = 0
    for uf in files:
        declared_size = getattr(uf, "size", None)
        if not isinstance(declared_size, int) or declared_size < 0:
            continue
        if declared_size > CHAT_UPLOAD_MAX_FILE_BYTES:
            return JSONResponse(
                {"ok": False, "error": f"文件“{_safe_upload_filename(uf.filename or '')}”超过 100 MB 限制。"},
                status_code=413,
            )
        declared_total += declared_size
    if declared_total > CHAT_UPLOAD_MAX_TOTAL_BYTES:
        return JSONResponse(
            {"ok": False, "error": "本次上传总大小超过 200 MB 限制。"},
            status_code=413,
        )
    import datetime
    upload_root = (WORK_DIR / "uploads" / "chat" / datetime.datetime.now().strftime("%Y%m%d")).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    saved = []
    created_paths: list[Path] = []
    actual_total = 0
    try:
        for uf in files:
            filename = _safe_upload_filename(uf.filename or "")
            dest = _dedupe_upload_path((upload_root / filename).resolve())
            try:
                dest.relative_to(upload_root)
            except ValueError:
                return JSONResponse({"ok": False, "error": "invalid filename"}, status_code=400)
            created_paths.append(dest)
            actual_file_size = 0
            with dest.open("wb") as out:
                while True:
                    chunk = await uf.read(1024 * 1024)
                    if not chunk:
                        break
                    actual_file_size += len(chunk)
                    actual_total += len(chunk)
                    if actual_file_size > CHAT_UPLOAD_MAX_FILE_BYTES:
                        raise _ChatUploadLimitError(f"文件“{filename}”超过 100 MB 限制。")
                    if actual_total > CHAT_UPLOAD_MAX_TOTAL_BYTES:
                        raise _ChatUploadLimitError("本次上传总大小超过 200 MB 限制。")
                    out.write(chunk)
            saved.append({
                "name": filename,
                "path": str(dest),
                "rel": str(dest.relative_to(WORK_DIR.resolve())).replace("\\", "/"),
                "size": dest.stat().st_size,
            })
    except _ChatUploadLimitError as exc:
        for path in created_paths:
            path.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=413)
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    finally:
        for uf in files:
            try:
                await uf.close()
            except Exception:
                pass
    return JSONResponse({"ok": True, "files": saved})


@fastapi_app.post("/api/pick-path")
async def api_pick_path(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    kind = (data.get("kind") or "directory").strip().lower()
    if kind not in ("file", "directory"):
        return JSONResponse(
            {"ok": False, "error": "kind 须为 file 或 directory"},
            status_code=400,
        )
    initial = str(data.get("initial") or "")
    multiple = bool(data.get("multiple", False))
    try:
        chosen = await run_in_threadpool(pick_native_path, kind, initial, multiple)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"无法打开选择对话框: {e}"},
            status_code=500,
        )
    if multiple:
        paths = chosen if isinstance(chosen, list) else ([chosen] if chosen else [])
        if not paths:
            return JSONResponse({"ok": False, "cancelled": True, "error": "已取消"})
        return JSONResponse({"ok": True, "paths": paths, "path": paths[0]})
    if not chosen:
        return JSONResponse({"ok": False, "cancelled": True, "error": "已取消"})
    return JSONResponse({"ok": True, "path": chosen})


def _attach_subagent_sidebar_fields(s: dict, session_id: str) -> None:
    """为会话摘要附加 subagent 运行/待续接计数（供侧栏状态与续接横幅）。"""
    try:
        from agent_subagent import subagent_registry

        flat = session_manager.list_subagents_flat(
            str(session_id),
            running_checker=subagent_registry.is_running,
            include_dialogue_turns=False,
        )
        s["subagent_count"] = len(flat)
        s["subagent_running"] = sum(1 for n in flat if n.get("running"))
    except Exception:
        s["subagent_count"] = 0
        s["subagent_running"] = 0
    try:
        s["subagent_pending_continue"] = session_manager.count_actionable_pending_subagent_results(
            str(session_id)
        )
        s["subagent_can_continue"] = session_manager.can_continue_after_subagents(str(session_id))
        pending_count = int(s["subagent_pending_continue"] or 0)
        running_count = int(s.get("subagent_running") or 0)
        s["subagent_continuation"] = {
            "state": "ready" if pending_count > 0 and running_count == 0 else (
                "wait_children" if running_count > 0 else "none"
            ),
            "pending_count": pending_count,
            "reason": "results_not_in_parent_answer" if pending_count > 0 else "",
        }
    except Exception:
        s["subagent_pending_continue"] = 0
        s["subagent_can_continue"] = False
        s["subagent_continuation"] = {"state": "none", "pending_count": 0, "reason": ""}
    try:
        s["react_can_continue"] = session_manager.can_continue_react_session(str(session_id))
    except Exception:
        s["react_can_continue"] = False


@fastapi_app.get("/sessions")
async def list_sessions(
    include_archived: bool = Query(False),
    archived_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1),
):
    schedule_runtime_auto_migration()

    def _build_response() -> JSONResponse:
        sessions = session_manager.list_sessions(include_archived=include_archived or archived_only)
        archived_count = session_manager.archived_session_count()
        if archived_only:
            sessions = [s for s in sessions if bool(s.get("archived"))]
        if offset or limit is not None:
            end = None if limit is None else offset + limit
            sessions = sessions[offset:end]
        _cleanup_stale_active_chat()
        try:
            from human_interaction import get_human_interaction_service

            interaction_service = get_human_interaction_service()
        except Exception:
            interaction_service = None
        for s in sessions:
            sid = s.get("id")
            if sid:
                sid = str(sid)
                run_state = _session_run_state_fields_light(sid)
                s["stream_active"] = bool(run_state["stream_active"])
                s["run_active"] = bool(run_state["run_active"])
                s["run_started_at"] = run_state["run_started_at"]
                s["title_generation_pending"] = is_session_title_generation_pending(sid)
                if interaction_service is not None:
                    try:
                        pending_human = interaction_service.pending_counts(sid)
                    except Exception:
                        pending_human = {"questions": 0, "approvals": 0, "total": 0}
                else:
                    pending_human = {"questions": 0, "approvals": 0, "total": 0}
                s["pending_human_interactions"] = pending_human
            else:
                s["stream_active"] = False
                s["run_active"] = False
                s["run_started_at"] = None
                s["title_generation_pending"] = False
                s["pending_human_interactions"] = {"questions": 0, "approvals": 0, "total": 0}
        return JSONResponse(
            content=sessions,
            headers={"X-Archived-Count": str(archived_count)},
        )

    return await asyncio.to_thread(_build_response)


@fastapi_app.get("/sessions/state")
async def sessions_state(include_archived: bool = Query(False)):
    payload = await asyncio.to_thread(
        _build_sessions_state_snapshot_cached, include_archived=include_archived
    )
    return JSONResponse(content=payload)


@fastapi_app.post("/sessions/recover")
async def recover_sessions():
    """Resume every interrupted recoverable session in server-owned workers."""
    scheduled = await recover_interrupted_react_sessions()
    return JSONResponse(content={"ok": True, "scheduled": scheduled, "count": len(scheduled)})


@fastapi_app.get("/state")
async def app_state(include_archived: bool = Query(False)):
    payload = await asyncio.to_thread(
        _build_sessions_state_snapshot_cached, include_archived=include_archived
    )
    return JSONResponse(content=payload)


@fastapi_app.get("/runtime-v2/state")
async def runtime_v2_state(include_archived: bool = Query(True)):
    """Read-only Runtime V2 debug state built from snapshots."""
    return JSONResponse(content=_runtime_v2_debug_state(include_archived=include_archived))


@fastapi_app.get("/runtime-v2/sessions/{session_id}/events")
async def runtime_v2_session_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    before_seq: Optional[int] = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=1000),
):
    """Read-only Runtime V2 event log page for debugging and audit."""
    try:
        events = _runtime_v2_event_dicts(
            session_id,
            after_seq=int(after_seq or 0),
            before_seq=before_seq,
            limit=int(limit or 200),
        )
    except ValueError as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(content={
        "ok": True,
        "session_id": session_id,
        "events": events,
        "count": len(events),
        "after_seq": int(after_seq or 0),
        "before_seq": before_seq,
        "limit": int(limit or 200),
    })


@fastapi_app.get("/runtime-v2/events")
async def runtime_v2_events(
    session_id: str = Query(..., min_length=1),
    after_seq: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    """Read-only Runtime V2 after-seq event page for reconnect/debug clients."""
    try:
        events = _runtime_v2_event_dicts(
            session_id,
            after_seq=int(after_seq or 0),
            limit=int(limit or 200),
        )
    except ValueError as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=400)
    latest_seq = 0
    if events:
        try:
            latest_seq = int(events[-1].get("seq") or 0)
        except (TypeError, ValueError):
            latest_seq = 0
    return JSONResponse(content={
        "ok": True,
        "session_id": session_id,
        "events": events,
        "count": len(events),
        "after_seq": int(after_seq or 0),
        "latest_seq": latest_seq,
        "limit": int(limit or 200),
    })


@fastapi_app.get("/runtime-v2/sessions/{session_id}/stream")
async def runtime_v2_session_stream(
    session_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    poll_ms: int = Query(50, ge=10, le=5000),
):
    """Runtime V2-native SSE stream backed by events.jsonl seq reads."""
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)

    async def event_generator():
        cursor = int(after_seq or 0)
        idle_ticks = 0
        poll_seconds = max(0.01, min(float(poll_ms or 50) / 1000.0, 5.0))
        while True:
            if await request.is_disconnected():
                break
            try:
                events = _runtime_v2_event_dicts(sid, after_seq=cursor, limit=100)
            except Exception as exc:
                payload = {
                    "type": "runtime_v2_stream_error",
                    "session_id": sid,
                    "error": str(exc),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            if events:
                idle_ticks = 0
                for event in events:
                    try:
                        cursor = max(cursor, int(event.get("seq") or cursor))
                    except (TypeError, ValueError):
                        pass
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)
                continue
            if not _has_local_worker_activity(sid):
                yield "data: [DONE]\n\n"
                break
            idle_ticks += 1
            if idle_ticks % max(1, int(15000 / max(10, int(poll_ms or 50)))) == 0:
                yield f": runtime-v2 keepalive {cursor}\n\n"
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@fastapi_app.get("/runtime-v2/runs")
async def runtime_v2_runs(include_archived: bool = Query(True)):
    """Read-only Runtime V2 active run view."""
    state = _runtime_v2_debug_state(include_archived=include_archived)
    return JSONResponse(content={
        "runtime_version": state.get("runtime_version"),
        "active_run_count": state.get("active_run_count", 0),
        "active_runs": state.get("active_runs", []),
    })

@fastapi_app.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    include_subagents: bool = Query(False, description="为 true 时也聚合 subagent 面板中的状态字段"),
):
    """单条会话摘要（与列表项结构一致），供侧栏增量更新。"""
    def _build_detail_response() -> JSONResponse:
        s = session_manager.get_session_summary(session_id)
        _cleanup_stale_active_chat()
        if not s:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        sid = s.get("id")
        if sid:
            run_state = _session_run_state_fields(str(sid))
            s["stream_active"] = bool(run_state["stream_active"])
            s["run_active"] = bool(run_state["run_active"])
            s["run_started_at"] = run_state["run_started_at"]
            s["title_generation_pending"] = is_session_title_generation_pending(str(sid))
            s["pending_human_interactions"] = _session_pending_human_counts(str(sid))
            try:
                s["react_can_continue"] = session_manager.can_continue_react_session(str(sid))
            except Exception:
                s["react_can_continue"] = False
            from workflow_extensions import session_workflows
            workflow_source = session_workflows.continuation_source(str(sid))
            s["react_can_continue"] = bool(s["react_can_continue"] or workflow_source)
            s["react_auto_resume"] = bool(
                not workflow_source
                and s["react_can_continue"]
                and int(s["pending_human_interactions"].get("total") or 0) == 0
                and _runtime_v2_auto_resume_pending(str(sid))
            )
            if include_subagents:
                _attach_subagent_sidebar_fields(s, str(sid))
        else:
            s["stream_active"] = False
            s["run_active"] = False
            s["run_started_at"] = None
            s["title_generation_pending"] = False
            s["pending_human_interactions"] = {"questions": 0, "approvals": 0, "total": 0}
            s["react_can_continue"] = False
            s["react_auto_resume"] = False
        return JSONResponse(content=s)

    return await asyncio.to_thread(_build_detail_response)


@fastapi_app.get("/sessions/{session_id}/subagents")
async def list_session_subagents(
    session_id: str,
    lite: bool = Query(False, description="为 true 时不加载 dialogue_turns，减轻列表刷新开销"),
):
    """返回当前会话下 subagent 扁平列表（含嵌套），供 UI 树展示。"""
    return await asyncio.to_thread(_build_session_subagents_response, session_id, lite)


def _build_session_subagents_response(session_id: str, lite: bool) -> JSONResponse:
    try:
        from runtime_v2 import runtime_v2_primary

        if runtime_v2_primary():
            try:
                return _build_runtime_v2_session_subagents_response(session_id, lite)
            except ValueError as exc:
                return JSONResponse(content={"error": str(exc)}, status_code=400)
            except Exception as exc:
                logger.debug("Runtime V2 subagent response failed for %s: %s", session_id, exc)
                return JSONResponse(content={
                    "session_id": session_id,
                    "subagents": [],
                    "source": "runtime_v2_subagents_error",
                    "error": str(exc),
                }, status_code=500)
    except Exception as exc:
        logger.debug("Runtime V2 subagent response check failed for %s: %s", session_id, exc)
    try:
        from agent_subagent import subagent_registry

        nodes = session_manager.list_subagents_flat(
            session_id,
            running_checker=subagent_registry.is_running,
            include_dialogue_turns=not lite,
        )
        task_rows = session_manager.list_subagent_tasks(session_id)
        task_by_id = {
            str(t.get("task_id") or t.get("agent_id") or t.get("id") or ""): t
            for t in task_rows
            if isinstance(t, dict)
        }
        node_ids = {str(n.get("id") or "") for n in nodes if isinstance(n, dict)}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            tid = str(n.get("id") or "")
            task = task_by_id.get(tid)
            if not task:
                continue
            n["task_id"] = str(task.get("task_id") or tid)
            n["task_status"] = str(task.get("status") or "")
            n["background"] = bool(task.get("background"))
            n["output_file"] = str(task.get("output_file") or n.get("output_file") or "")
            n["started_at"] = task.get("started_at") or n.get("created_at")
            n["finished_at"] = task.get("finished_at")
            n["updated_at"] = task.get("updated_at") or n.get("updated_at")
            if task.get("status"):
                n["status"] = task.get("status")
            if task.get("error"):
                n["error"] = task.get("error")
                n["ok"] = False
            if task.get("result_preview"):
                n["result_preview"] = str(task.get("result_preview") or "")[:1200]
        for tid, task in task_by_id.items():
            if not tid or tid in node_ids:
                continue
            task_status = str(task.get("status") or "")
            output_file = str(task.get("output_file") or "")
            has_output = False
            if output_file:
                try:
                    has_output = Path(output_file).expanduser().resolve().is_file()
                except Exception:
                    has_output = False
            virtual_error = str(task.get("error") or "")
            if task_status == "completed" and not has_output:
                task_status = "failed"
                virtual_error = virtual_error or "missing final/output"
            nodes.append(
                {
                    "id": tid,
                    "task_id": tid,
                    "parent_id": session_id,
                    "description": str(task.get("description") or task.get("subagent_type") or tid[:8]),
                    "subagent_type": str(task.get("subagent_type") or "subagent"),
                    "depth": int(task.get("depth") or 1),
                    "created_at": task.get("created_at") or task.get("started_at"),
                    "updated_at": task.get("updated_at") or task.get("finished_at") or task.get("started_at"),
                    "started_at": task.get("started_at"),
                    "finished_at": task.get("finished_at"),
                    "background": bool(task.get("background")),
                    "running": task_status == "running",
                    "ok": True if task_status == "completed" else (None if task_status == "running" else False),
                    "status": task_status,
                    "task_status": task_status,
                    "error": virtual_error,
                    "has_final": has_output,
                    "result_preview": str(task.get("result_preview") or "")[:1200],
                    "output_file": output_file if has_output else "",
                    "dialogue_turns": [],
                    "session_metrics": {},
                    "virtual_task": True,
                }
            )
        return JSONResponse(content={"session_id": session_id, "subagents": nodes})
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _build_runtime_v2_session_subagents_response(session_id: str, lite: bool) -> JSONResponse:
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(
        session_manager.repository.sessions_dir,
        path_resolver=session_manager._resolve_session_path,
    )
    task_rows = store.list_tasks(session_id)
    parent_snapshot = _runtime_v2_snapshot(session_id)
    subagent_states = parent_snapshot.get("subagents") if isinstance(parent_snapshot, dict) else {}
    if not isinstance(subagent_states, dict):
        subagent_states = {}

    nodes: list[dict] = []
    seen: set[str] = set()
    for task in task_rows:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("task_id") or task.get("agent_id") or task.get("id") or "").strip()
        if not tid:
            continue
        state = subagent_states.get(tid) if isinstance(subagent_states.get(tid), dict) else {}
        nodes.append(_runtime_v2_subagent_node(session_id, tid, task, state, lite))
        seen.add(tid)

    for tid, state in subagent_states.items():
        sid = str(tid or "").strip()
        if not sid or sid in seen or not isinstance(state, dict):
            continue
        nodes.append(_runtime_v2_subagent_node(session_id, sid, {}, state, lite))

    return JSONResponse(content={
        "session_id": session_id,
        "subagents": nodes,
        "source": "runtime_v2_subagents",
    })


def _runtime_v2_subagent_node(parent_id: str, task_id: str, task: dict, state: dict, lite: bool) -> dict:
    raw_status = str(task.get("status") or state.get("status") or "").strip()
    normalized_status = {
        "finished": "completed",
        "done": "completed",
    }.get(raw_status, raw_status or "running")
    output_file = str(task.get("output_file") or state.get("output_file") or "").strip()
    has_output = False
    if output_file:
        try:
            has_output = Path(output_file).expanduser().resolve().is_file()
        except Exception:
            has_output = False
    error = str(task.get("error") or state.get("error") or "")
    has_final = bool(task.get("has_final") or state.get("has_final") or has_output)
    if normalized_status == "completed" and error:
        normalized_status = "failed"
    try:
        depth = int(task.get("depth") or state.get("depth") or 1)
    except (TypeError, ValueError):
        depth = 1
    event_count = 0
    has_session_events = False
    try:
        from runtime_v2.ui_projection import RuntimeUiProjection

        session_path = Path(session_manager._resolve_session_path(task_id))
        has_session_events = (session_path / "events.jsonl").is_file()
        projection = RuntimeUiProjection(
            session_manager.repository.sessions_dir,
            path_resolver=session_manager._resolve_session_path,
        )
        event_count, _latest_truncate_seq = projection.count_ui_events_light(task_id)
    except Exception as exc:
        logger.debug("Runtime V2 subagent event count failed for %s: %s", task_id, exc)
    subagent_type = str(task.get("subagent_type") or state.get("subagent_type") or "subagent")
    try:
        metadata = session_manager._load_metadata(task_id) or {}
    except Exception:
        metadata = {}
    model_profile_id = str(
        task.get("model_profile_id")
        or state.get("model_profile_id")
        or metadata.get("model_profile_id")
        or ""
    ).strip()
    executor_model = str(
        task.get("executor_model")
        or state.get("executor_model")
        or metadata.get("executor_model")
        or ""
    ).strip()
    if not executor_model and model_profile_id:
        profile = model_profiles.get_profile(PROJECT_ROOT, model_profile_id)
        if isinstance(profile, dict):
            executor_model = str(profile.get("model") or "").strip()
    virtual_task = subagent_type == "best-of-n-runner" or (
        normalized_status != "running" and has_output and not has_session_events
    )
    return {
        "id": task_id,
        "task_id": task_id,
        "parent_id": parent_id,
        "description": str(
            task.get("description")
            or state.get("description")
            or task.get("subagent_type")
            or state.get("subagent_type")
            or task_id[:8]
        ),
        "subagent_type": subagent_type,
        "model_profile_id": model_profile_id,
        "executor_model": executor_model,
        "last_model_switch": dict(
            task.get("last_model_switch")
            or state.get("last_model_switch")
            or metadata.get("last_model_switch")
            or {}
        ),
        "depth": depth,
        "created_at": task.get("created_at") or state.get("started_at"),
        "updated_at": task.get("updated_at") or state.get("finished_at") or task.get("finished_at") or task.get("started_at"),
        "started_at": task.get("started_at") or state.get("started_at"),
        "finished_at": task.get("finished_at") or state.get("finished_at"),
        "background": bool(task.get("background") or state.get("background")),
        "running": normalized_status == "running",
        "ok": True if normalized_status == "completed" else (None if normalized_status == "running" else False),
        "status": normalized_status,
        "task_status": normalized_status,
        "error": error,
        "has_final": has_final,
        "result_preview": str(task.get("result_preview") or state.get("result_preview") or "")[:1200],
        "output_file": output_file if has_output else "",
        "dialogue_turns": [] if lite else [],
        "event_count": int(event_count or 0),
        "session_metrics": {},
        "virtual_task": virtual_task,
        "source": "runtime_v2_subagents",
    }


@fastapi_app.get("/sessions/{parent_id}/subagents/{task_id}/output")
async def get_subagent_output(parent_id: str, task_id: str):
    """读取 subagent/task 的最终可读输出，供前端卡片按需展开。"""
    out = session_manager.read_subagent_task_output(parent_id, task_id)
    if not out.get("ok"):
        return JSONResponse(content=out, status_code=404)
    return JSONResponse(content=out)


@fastapi_app.post("/sessions/{parent_id}/subagents/{child_id}/interrupt")
async def interrupt_subagent(parent_id: str, child_id: str):
    """中断指定 subagent（含后台任务）。"""
    valid = session_manager.validate_subagent_resume(parent_id, child_id)
    if not valid:
        return JSONResponse(content={"error": "invalid subagent"}, status_code=404)
    try:
        from agent_subagent import subagent_registry
        from session_lifecycle import cancel_run_tasks

        session_manager.request_interrupt(child_id)
        await subagent_registry.cancel(child_id)
        await cancel_run_tasks([child_id])
        return JSONResponse(content={"status": "ok", "agent_id": child_id})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@fastapi_app.post("/sessions/{parent_id}/subagents/{child_id}/model_profile")
async def switch_subagent_model_profile_api(
    parent_id: str,
    child_id: str,
    request: Request,
):
    """Retarget a subagent without replacing its identity or durable history."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"ok": False, "error": "invalid json"}, status_code=400)
    profile_id = str((data or {}).get("profile_id") or "").strip()
    if not profile_id:
        return JSONResponse(
            content={"ok": False, "error": "profile_id is required"},
            status_code=400,
        )
    try:
        from agent_subagent import switch_subagent_model_profile

        result = await switch_subagent_model_profile(
            parent_id,
            child_id,
            profile_id,
            instruction=str((data or {}).get("instruction") or ""),
            source_run_id=str((data or {}).get("source_run_id") or ""),
            requested_by="user",
        )
    except Exception as exc:
        logger.exception("switch subagent model failed: %s", exc)
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=500)
    status_code = int(result.pop("status_code", 200) or 200)
    return JSONResponse(content=result, status_code=status_code)


@fastapi_app.delete("/sessions/{parent_id}/subagents/{child_id}")
async def delete_subagent(parent_id: str, child_id: str):
    """删除指定 subagent 会话（含其嵌套 subagents）。"""
    valid = session_manager._resolve_subagent_child_for_delete(parent_id, child_id)
    if not valid:
        try:
            ok_virtual = session_manager.delete_virtual_subagent_task(parent_id, child_id)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
        if ok_virtual:
            return JSONResponse(
                content={"status": "ok", "agent_id": child_id, "virtual_task": True}
            )
        return JSONResponse(content={"error": "invalid subagent"}, status_code=404)
    try:
        from agent_subagent import cleanup_git_worktree_for_session, subagent_registry
        from session_lifecycle import cancel_run_tasks

        descendants = session_manager.list_subagent_descendants(valid)
        all_ids = [valid, *descendants]
        for sid in all_ids:
            try:
                session_manager.request_interrupt(sid)
            except Exception:
                pass
        try:
            await subagent_registry.cancel(valid)
            await subagent_registry.cancel_for_parent(valid, also_ids=set(descendants))
        except Exception:
            pass
        await cancel_run_tasks(all_ids)
        for sid in all_ids:
            try:
                cleanup_git_worktree_for_session(sid)
            except Exception:
                pass
        ok = session_manager.delete_subagent_session(parent_id, valid)
        if not ok:
            return JSONResponse(content={"error": "delete failed"}, status_code=400)
        return JSONResponse(content={"status": "ok", "agent_id": valid})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@fastapi_app.post("/sessions")
async def create_session():
    # get_or_create_session 现在返回6个值，我们只需要 session_id
    session_id, _, _, _, _, metadata = session_manager.get_or_create_session()
    session = {
        "id": session_id,
        "name": (metadata or {}).get("name") or "新会话",
        "created_at": (metadata or {}).get("created_at"),
        "updated_at": (metadata or {}).get("updated_at") or (metadata or {}).get("created_at"),
        "last_activity_at": (metadata or {}).get("updated_at") or (metadata or {}).get("created_at"),
        "archived": bool((metadata or {}).get("archived", False)),
        "pinned": bool((metadata or {}).get("pinned", False)),
        "todo": bool((metadata or {}).get("todo", False)),
        "pinned_at": (metadata or {}).get("pinned_at") if (metadata or {}).get("pinned") else None,
        "last_user_preview": "",
        "stream_active": False,
    }
    return JSONResponse(content={"session_id": session_id, "session": session})


def _model_profiles_response() -> dict:
    profiles = [
        model_profiles.public_profile(p)
        for p in model_profiles.sorted_profiles(PROJECT_ROOT)
    ]
    top = next((p for p in profiles if model_profiles.is_usable_profile(p)), None)
    return {
        "ok": True,
        "default_profile": top or {},
        "new_session_default_profile_id": str((top or {}).get("id") or ""),
        "profiles": profiles,
    }


def _validate_model_name_in_discovered_list(data: dict, model_name: str) -> None:
    discovered = data.get("discovered_model_ids")
    if not isinstance(discovered, list):
        return
    ids = {str(item or "").strip() for item in discovered if str(item or "").strip()}
    if ids and str(model_name or "").strip() not in ids:
        raise ValueError("模型名称必须与已获取模型列表中的模型 ID 完全一致")


@fastapi_app.get("/api/model_profiles")
async def get_model_profiles():
    return JSONResponse(_model_profiles_response())


@fastapi_app.post("/api/model_profiles")
async def save_model_profile(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    if not str(data.get("model") or "").strip():
        return JSONResponse({"ok": False, "error": "missing model"}, status_code=400)
    try:
        _validate_model_name_in_discovered_list(data, str(data.get("model") or "").strip())
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    if not str(data.get("base_url") or "").strip():
        return JSONResponse({"ok": False, "error": "missing base_url"}, status_code=400)
    old_profile = model_profiles.get_profile(PROJECT_ROOT, str(data.get("id") or "").strip())
    incoming_key = str(data.get("api_key") or "").strip() if "api_key" in data else ""
    llm_type = str(data.get("llm_type") or "openai").strip().lower()
    if llm_type != "local" and not incoming_key and not str((old_profile or {}).get("api_key") or "").strip():
        return JSONResponse({"ok": False, "error": "missing api_key"}, status_code=400)
    try:
        profile = model_profiles.upsert_profile(PROJECT_ROOT, data)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    _invalidate_executor_config_cache()
    refresh_executor_client_from_env()
    return JSONResponse({"ok": True, "profile": model_profiles.public_profile(profile)})


@fastapi_app.post("/api/model_profiles/reorder")
async def reorder_model_profiles(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    ids = (data or {}).get("ordered_ids") or []
    if not isinstance(ids, list):
        return JSONResponse({"ok": False, "error": "ordered_ids must be list"}, status_code=400)
    model_profiles.reorder_profiles(PROJECT_ROOT, [str(x) for x in ids])
    _invalidate_executor_config_cache()
    refresh_executor_client_from_env()
    return JSONResponse(_model_profiles_response())


@fastapi_app.post("/api/model_profiles/{profile_id}/enabled")
async def set_model_profile_enabled(profile_id: str, req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    enabled = data.get("enabled") if isinstance(data, dict) else None
    if not isinstance(enabled, bool):
        return JSONResponse({"ok": False, "error": "enabled must be boolean"}, status_code=400)
    profile = model_profiles.set_profile_enabled(PROJECT_ROOT, (profile_id or "").strip(), enabled)
    if profile is None:
        return JSONResponse({"ok": False, "error": "unknown profile_id"}, status_code=404)
    _invalidate_executor_config_cache()
    refresh_executor_client_from_env()
    return JSONResponse({"ok": True, "profile": model_profiles.public_profile(profile)})


@fastapi_app.delete("/api/model_profiles/{profile_id}")
async def delete_model_profile(profile_id: str):
    ok = model_profiles.delete_profile(PROJECT_ROOT, (profile_id or "").strip())
    if ok:
        _invalidate_executor_config_cache()
        refresh_executor_client_from_env()
    return JSONResponse({"ok": ok})


@fastapi_app.post("/api/model_profiles/discover")
async def discover_model_profiles(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    base_url = str((data or {}).get("base_url") or "").strip()
    api_key = str((data or {}).get("api_key") or "").strip()
    try:
        models = await run_in_threadpool(model_profiles.discover_models, base_url, api_key)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "models": models})


@fastapi_app.post("/api/model_profiles/probe")
async def probe_model_profile(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    base_url = str(data.get("base_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    model_id = str(data.get("model") or data.get("id") or "").strip()
    fallback = dict(data.get("metadata")) if isinstance(data.get("metadata"), dict) else dict(data)
    if data.get("llm_type"):
        fallback["llm_type"] = str(data.get("llm_type") or "").strip()
    try:
        model = await run_in_threadpool(model_profiles.probe_model_context, base_url, api_key, model_id, fallback)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "model": model})


@fastapi_app.get("/api/security/settings")
async def get_security_settings():
    from security import security_settings

    return JSONResponse({"ok": True, **security_settings()})


@fastapi_app.post("/api/security/settings")
async def set_security_settings(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    from security import update_security_settings

    settings = update_security_settings(
        **{
            key: body[key] is True
            for key in (
                "auto_review_enabled",
                "allow_external_workspace_ops",
            )
            if key in body
        }
    )
    return JSONResponse({"ok": True, **settings})


@fastapi_app.get("/api/security/rules")
async def get_security_rules(
    session_id: str = Query(default=""),
    workspace: str = Query(default=""),
):
    try:
        from security import list_permission_rules

        rules = list_permission_rules(
            session_id=session_id, workspace=workspace
        )
        return JSONResponse({"ok": True, "rules": rules})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.post("/api/security/rules")
async def add_security_rule(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    try:
        from security import add_permission_rule

        rule = add_permission_rule(
            behavior=str(body.get("behavior") or ""),
            action=str(body.get("action") or ""),
            pattern=str(body.get("pattern") or ""),
            source=str(body.get("source") or "user"),
            session_id=str(body.get("session_id") or ""),
            workspace=str(body.get("workspace") or ""),
        )
        return JSONResponse({"ok": True, "rule": rule})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.delete("/api/security/rules")
async def clear_security_rules(session_id: str = Query(default="")):
    try:
        from security import clear_session_permission_rules

        deleted = clear_session_permission_rules(session_id)
        return JSONResponse({"ok": True, "deleted": deleted})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.delete("/api/security/rules/{rule_id}")
async def delete_security_rule(rule_id: str):
    try:
        from security import delete_permission_rule

        ok = delete_permission_rule(rule_id)
        return JSONResponse({"ok": ok})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.get("/api/security/web-fetch-domains")
async def get_web_fetch_preapproved_domains():
    try:
        from security import web_fetch_preapproved_domains

        return JSONResponse(
            {"ok": True, "domains": web_fetch_preapproved_domains()}
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.post("/api/security/web-fetch-domains")
async def set_web_fetch_preapproved_domains(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
    try:
        from security import set_web_fetch_preapproved_domains

        raw = body.get("domains")
        if not isinstance(raw, (list, tuple, str)):
            return JSONResponse(
                {"ok": False, "error": "domains must be a list or text"},
                status_code=422,
            )
        if isinstance(raw, str):
            raw = [item for item in raw.replace(",", "\n").splitlines() if item.strip()]
        domains = set_web_fetch_preapproved_domains(raw)
        return JSONResponse({"ok": True, "domains": domains})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.get("/sessions/{session_id}/permissions")
async def get_session_permissions(session_id: str):
    sid = str(session_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        from security import security_status_for_session

        return JSONResponse({"ok": True, **security_status_for_session(sid)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@fastapi_app.post("/sessions/{session_id}/permissions")
async def set_session_permissions(session_id: str, req: Request):
    sid = str(session_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    try:
        from security import security_status_for_session, set_session_permission_mode

        set_session_permission_mode(sid, (body or {}).get("mode"))
        status = security_status_for_session(sid)
        event = {"type": "permission_mode_changed", **status, "ephemeral": True}
        for row in session_manager.list_sessions(include_archived=True):
            target = str(row.get("id") or row.get("session_id") or "").strip()
            if not target:
                continue
            try:
                await publish_session_event(target, event)
            except Exception:
                logger.debug("Permission mode broadcast failed for %s", target, exc_info=True)
        return JSONResponse({"ok": True, **status})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.get("/api/security/extensions")
async def get_security_extensions():
    try:
        from security.extensions import extension_candidates

        return JSONResponse({"ok": True, "extensions": extension_candidates()})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.post("/api/security/extensions/{kind}/{extension_id}/trust")
async def trust_security_extension(kind: str, extension_id: str):
    try:
        from agent_extensions import get_plugin_runtime_registry
        from security.extensions import trust_current_extension
        from security.runtime import security_store

        descriptor = trust_current_extension(kind, extension_id)
        security_store().audit(
            session_id="",
            event_type="extension_trust",
            outcome="allow",
            payload={
                "kind": descriptor["kind"],
                "extension_id": descriptor["extension_id"],
                "content_digest": descriptor["content_digest"],
            },
        )
        get_plugin_runtime_registry().invalidate()
        await agent_mcp.force_reload()
        return JSONResponse({"ok": True, "extension": descriptor})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.delete("/api/security/extensions/{kind}/{extension_id}/trust")
async def revoke_security_extension(kind: str, extension_id: str):
    try:
        from agent_extensions import get_plugin_runtime_registry
        from security.extensions import revoke_extension
        from security.runtime import security_store

        revoked = revoke_extension(kind, extension_id)
        security_store().audit(
            session_id="",
            event_type="extension_trust",
            outcome="deny",
            payload={"kind": kind, "extension_id": extension_id},
        )
        get_plugin_runtime_registry().invalidate()
        await agent_mcp.force_reload()
        return JSONResponse({"ok": True, "revoked": revoked})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.post("/api/security/mcp/{extension_id}/registration")
async def decide_mcp_registration(extension_id: str, request: Request):
    """Confirm or reject starting one exact MCP server configuration."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    try:
        from security.extensions import decide_current_mcp_registration
        from security.runtime import security_store

        approved_value = (body or {}).get("approved")
        if not isinstance(approved_value, bool):
            raise ValueError("approved must be a boolean")
        approved = approved_value
        descriptor = decide_current_mcp_registration(
            extension_id,
            config_digest=str((body or {}).get("config_digest") or ""),
            approved=approved,
        )
        security_store().audit(
            session_id="",
            event_type="mcp_registration",
            outcome="allow" if approved else "deny",
            payload={
                "extension_id": descriptor["extension_id"],
                "config_digest": descriptor["config_digest"],
                "transport": descriptor.get("runtime") or "",
            },
        )
        await agent_mcp.force_reload()
        if approved:
            await agent_mcp.ensure_started()
        return JSONResponse({"ok": True, "registration": descriptor})
    except ValueError as exc:
        message = str(exc)
        lowered = message.lower()
        status = 409 if "changed" in lowered else (422 if "must be" in lowered else 404)
        return JSONResponse({"ok": False, "error": message}, status_code=status)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.get("/sessions/{session_id}/model_profile")
async def get_session_model_profile(session_id: str):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        meta = session_manager._load_metadata(sid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    pid = str((meta or {}).get("model_profile_id") or "").strip()
    if not model_profiles.is_usable_profile(model_profiles.get_profile(PROJECT_ROOT, pid)):
        top = model_profiles.top_profile(PROJECT_ROOT)
        pid = str((top or {}).get("id") or "")
    return JSONResponse({"ok": True, "profile_id": pid})


@fastapi_app.post("/sessions/{session_id}/model_profile")
async def set_session_model_profile(session_id: str, req: Request):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    pid = str((data or {}).get("profile_id") or "").strip()
    if not model_profiles.is_usable_profile(model_profiles.get_profile(PROJECT_ROOT, pid)):
        return JSONResponse({"ok": False, "error": "unknown profile_id"}, status_code=404)
    with session_manager._session_metadata_lock(sid):
        meta = session_manager._load_metadata_unlocked(sid)
        if not isinstance(meta, dict):
            meta = {}
        meta["model_profile_id"] = pid
        meta["updated_at"] = __import__("datetime").datetime.now().isoformat()
        session_manager._save_metadata_unlocked(sid, meta)
        _invalidate_executor_config_cache(sid)
    return JSONResponse({"ok": True, "profile_id": pid})

@fastapi_app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    try:
        from agent_subagent import subagent_registry
        from session_lifecycle import stop_session_tree

        await stop_session_tree(sid, session_manager, subagent_registry)
    except Exception as e:
        logger.exception("stop session before delete failed: %s", e)
    _active_chat_by_session.pop(sid, None)
    _active_chat_last_seen.pop(sid, None)
    try:
        await run_in_threadpool(_run_history_op_locked, sid, session_manager.delete_session, sid)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str, request: Request):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    run_id = ""
    reason = "unspecified"
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            data = await request.json()
            run_id = str((data or {}).get("run_id") or (data or {}).get("client_run_id") or "").strip()
            reason = str((data or {}).get("reason") or reason).strip() or reason
        elif "form" in ctype:
            form = await request.form()
            run_id = str(form.get("run_id") or form.get("client_run_id") or "").strip()
            reason = str(form.get("reason") or reason).strip() or reason
    except Exception:
        run_id = ""
        reason = "unspecified"
    session_manager.request_interrupt(sid, run_id, reason=reason)
    if reason != "followup":
        session_manager.mark_session_unread_result(sid, status="failed")
    _active_chat_by_session.pop(sid, None)
    _active_chat_last_seen.pop(sid, None)
    interrupted_run_ids = _interrupt_runtime_v2_active_runs(sid, run_id, reason=reason)
    try:
        from session_event_bus import publish_session_event

        if interrupted_run_ids:
            for rid in interrupted_run_ids:
                await publish_session_event(sid, {"type": "run_interrupted", "run_id": rid, "reason": reason, "ephemeral": True})
        else:
            await publish_session_event(sid, {"type": "run_interrupted", "run_id": run_id, "reason": reason, "ephemeral": True})
    except Exception as e:
        logger.debug("publish interrupt failed for %s: %s", sid, e)
    try:
        from agent_subagent import subagent_registry
        from session_lifecycle import cancel_run_tasks

        descendants = session_manager.list_subagent_descendants(sid)
        all_ids = [sid, *descendants]
        for child_sid in descendants:
            try:
                session_manager.request_interrupt(child_sid)
            except Exception:
                pass
        try:
            await subagent_registry.cancel_for_parent(sid, also_ids=set(descendants))
        except Exception as e:
            logger.warning("cancel subagents on interrupt failed: %s", e)
        await cancel_run_tasks(all_ids)
    except Exception as e:
        logger.warning("cancel run tasks on interrupt failed: %s", e)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/tool-approval")
async def post_tool_approval(session_id: str, request: Request):
    """浏览器对「工作区放宽 Shell / web_download」的确认回调，解锁 agent_loop 中的等待。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"ok": False, "error": "invalid json"}, status_code=400)
    aid = str((body or {}).get("approval_id") or "").strip()
    approve = bool((body or {}).get("approve"))
    rejection_reason = str((body or {}).get("rejection_reason") or "").strip()

    from tool_approval_gate import resolve_tool_approval

    # Persist the digest-bound grant before waking the execution waiter. Otherwise
    # the tool can race ahead, re-authorize, and fail even though the user approved.
    try:
        from human_interaction import get_human_interaction_service
        from security import add_approval_grant

        record = get_human_interaction_service().get(session_id, aid, kind="approval")
        if approve and record.get("security_request_digest"):
            add_approval_grant(
                session_id,
                str(record.get("security_request_digest")),
                "allow_once",
            )
    except Exception:
        record = {}
    matched = resolve_tool_approval(session_id, aid, approve, rejection_reason)
    if matched and record:
        try:
            await publish_session_event(session_id, {"type": "approval_resolved", **record})
        except Exception:
            pass
    return JSONResponse(content={"ok": matched})


def _interaction_resolver_metadata(request: Request) -> dict:
    return {
        "channel": "webui",
        "client": str(request.headers.get("x-client-id") or "browser")[:120],
    }


def _human_interaction_error_response(exc: Exception) -> JSONResponse:
    from human_interaction import (
        HumanInteractionConflict,
        HumanInteractionNotFound,
        HumanInteractionValidationError,
    )

    if isinstance(exc, HumanInteractionNotFound):
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=404)
    if isinstance(exc, HumanInteractionConflict):
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=409)
    if isinstance(exc, HumanInteractionValidationError):
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=422)
    if isinstance(exc, ValueError):
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=422)
    logger.exception("human interaction request failed")
    return JSONResponse(content={"ok": False, "error": "human interaction request failed"}, status_code=500)


@fastapi_app.get("/sessions/{session_id}/interactions")
async def get_session_interactions(session_id: str, status: str = Query(default="pending")):
    try:
        from human_interaction import get_human_interaction_service

        normalized = str(status or "").strip().lower()
        if normalized not in {"", "pending", "resolved", "cancelled", "expired"}:
            return JSONResponse(content={"ok": False, "error": "invalid status"}, status_code=422)
        rows = await asyncio.to_thread(
            get_human_interaction_service().list,
            session_id,
            kind="question",
            status=normalized,
        )
        return JSONResponse(content={"ok": True, "interactions": rows})
    except Exception as exc:
        return _human_interaction_error_response(exc)


@fastapi_app.post("/sessions/{session_id}/interactions/{interaction_id}/resolve")
async def resolve_session_interaction(session_id: str, interaction_id: str, request: Request):
    try:
        body = await request.json()
        from human_interaction import get_human_interaction_service, has_registered_waiter

        had_waiter = has_registered_waiter(session_id, "question", interaction_id)
        record = await asyncio.to_thread(
            get_human_interaction_service().resolve_question,
            session_id,
            interaction_id,
            body,
            resolver=_interaction_resolver_metadata(request),
        )
        await publish_session_event(session_id, {"type": "interaction_resolved", **record})
        recovery_scheduled = False
        if not had_waiter and not _has_local_worker_activity(session_id):
            appended = await asyncio.to_thread(_append_recovered_question_tool_result, record)
            if appended:
                recovery_scheduled = _schedule_human_interaction_recovery(session_id)
        return JSONResponse(content={"ok": True, "interaction": record, "recovery_scheduled": recovery_scheduled})
    except Exception as exc:
        return _human_interaction_error_response(exc)


@fastapi_app.post("/sessions/{session_id}/interactions/{interaction_id}/cancel")
async def cancel_session_interaction(session_id: str, interaction_id: str, request: Request):
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        from human_interaction import get_human_interaction_service, has_registered_waiter

        had_waiter = has_registered_waiter(session_id, "question", interaction_id)
        cancel_reason = str((body or {}).get("reason") or "user_cancelled")
        if cancel_reason == "superseded_by_history_mutation":
            # Stop the old run before its waiter wakes. Otherwise it may append
            # a tool result or another assistant turn after the history tail
            # has already been truncated for a rewrite/delete operation.
            session_manager.request_interrupt(
                session_id,
                reason="history_mutation",
            )
        record = await asyncio.to_thread(
            get_human_interaction_service().cancel,
            session_id,
            interaction_id,
            kind="question",
            reason=cancel_reason,
        )
        await publish_session_event(session_id, {"type": "interaction_cancelled", **record})
        recovery_scheduled = False
        if not had_waiter and not _has_local_worker_activity(session_id):
            appended = await asyncio.to_thread(_append_recovered_question_tool_result, record)
            if appended and cancel_reason not in {
                "superseded_by_user_message",
                "superseded_by_history_mutation",
            }:
                recovery_scheduled = _schedule_human_interaction_recovery(session_id)
        return JSONResponse(content={"ok": True, "interaction": record, "recovery_scheduled": recovery_scheduled})
    except Exception as exc:
        return _human_interaction_error_response(exc)


@fastapi_app.get("/sessions/{session_id}/approvals")
async def get_session_approvals(session_id: str, status: str = Query(default="pending")):
    try:
        from human_interaction import get_human_interaction_service

        normalized = str(status or "").strip().lower()
        if normalized not in {"", "pending", "resolved", "cancelled", "expired"}:
            return JSONResponse(content={"ok": False, "error": "invalid status"}, status_code=422)
        rows = await asyncio.to_thread(
            get_human_interaction_service().list,
            session_id,
            kind="approval",
            status=normalized,
        )
        return JSONResponse(content={"ok": True, "approvals": rows})
    except Exception as exc:
        return _human_interaction_error_response(exc)


@fastapi_app.post("/sessions/{session_id}/approvals/{approval_id}/analyze")
async def analyze_session_approval(session_id: str, approval_id: str):
    """Ask the auto-review model for advice without resolving the approval."""
    try:
        from human_interaction import get_human_interaction_service
        from security.reviewer import review_request
        from tool_approval_gate import get_live_approval_review_context

        record = await asyncio.to_thread(
            get_human_interaction_service().get,
            session_id,
            approval_id,
            kind="approval",
        )
        if str(record.get("status") or "") != "pending":
            return JSONResponse(
                content={"ok": False, "error": "该审批已处理，无法继续分析。"},
                status_code=409,
            )
        context = get_live_approval_review_context(session_id, approval_id)
        if not context or context.get("request") is None:
            return JSONResponse(
                content={
                    "ok": False,
                    "error": "原执行已结束或缺少审查上下文，请让 Agent 重新发起该操作。",
                },
                status_code=409,
            )
        review = await review_request(
            context["request"],
            user_intent=str(context.get("user_intent") or ""),
            session_id=session_id,
            review_context=(
                dict(context.get("review_context") or {})
                if isinstance(context.get("review_context"), dict)
                else None
            ),
        )
        return JSONResponse(
            content={
                "ok": True,
                "analysis": {
                    "recommendation": "allow" if review.approved else "deny",
                    "risk": review.risk,
                    "reason": review.reason,
                    "intercept_reason": getattr(review, "intercept_reason", ""),
                    "risk_analysis": getattr(review, "risk_analysis", ""),
                    "command_purpose": getattr(review, "command_purpose", ""),
                    "available": review.available,
                },
            }
        )
    except Exception as exc:
        return _human_interaction_error_response(exc)


@fastapi_app.post("/sessions/{session_id}/approvals/{approval_id}/resolve")
async def resolve_session_approval(session_id: str, approval_id: str, request: Request):
    try:
        body = await request.json()
        decision = str((body or {}).get("decision") or "")
        rejection_reason = str((body or {}).get("rejection_reason") or "").strip()
        from human_interaction import get_human_interaction_service
        from tool_approval_gate import has_live_approval_waiter

        service = get_human_interaction_service()
        if not has_live_approval_waiter(session_id, approval_id):
            record = await asyncio.to_thread(
                service.cancel,
                session_id,
                approval_id,
                kind="approval",
                reason="原执行已结束，审批未执行；请让 Agent 重新发起该操作。",
            )
            await publish_session_event(session_id, {"type": "approval_cancelled", **record})
            return JSONResponse(
                content={
                    "ok": False,
                    "error": "原执行已结束，不能继续执行旧审批；请让 Agent 重新发起。",
                    "approval": record,
                },
                status_code=409,
            )
        resolve_kwargs = {"resolver": _interaction_resolver_metadata(request)}
        if decision.strip().lower().replace("-", "_") == "deny":
            resolve_kwargs["rejection_reason"] = rejection_reason
        record = await asyncio.to_thread(
            service.resolve_approval,
            session_id,
            approval_id,
            decision,
            **resolve_kwargs,
        )
        decision_value = str(record.get("decision") or "")
        security_digest = str(record.get("security_request_digest") or "").strip()
        if not security_digest:
            raise ValueError("approval is not bound to a security request")
        if decision_value in {
            "allow_external_workspace",
            "allow_external_workspace_once",
        }:
            from tool_approval_gate import resolve_tool_approval_decision
            try:
                from human_interaction.service import get_human_interaction_service
                svc = get_human_interaction_service()
                try:
                    pending = svc.get(session_id, approval_id, kind="approval")
                    req_dirs = list(pending.get("required_dirs") or [])
                    if not req_dirs:
                        req_dirs = list(record.get("required_dirs") or [])
                except Exception:
                    req_dirs = list(record.get("required_dirs") or [])
                if req_dirs:
                    from session_authorized_dirs import add_authorized_dir
                    for rd in req_dirs:
                        try:
                            add_authorized_dir(session_id, rd)
                        except Exception:
                            pass
                elif decision_value == "allow_external_workspace":
                    from security import update_security_settings
                    update_security_settings(allow_external_workspace_ops=True)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("directory grant failed: %s", e)
            resolve_tool_approval_decision(
                session_id, approval_id, decision_value
            )
        else:
            from security import add_approval_grant, add_permission_rule
            from tool_approval_gate import resolve_tool_approval

            if decision_value in {"allow_once", "allow_session", "allow_always"}:
                rule_action = str(record.get("rule_action") or "").strip()
                rule_pattern = str(record.get("rule_pattern") or "").strip()
                if (
                    decision_value == "allow_always"
                    and rule_action
                    and rule_pattern
                ):
                    # "始终允许同类操作" writes a durable pattern rule (like
                    # Claude Code's Bash(git push:*) / Read(src/**)), so changing
                    # arguments no longer triggers a fresh approval.
                    add_permission_rule(
                        behavior="allow",
                        action=rule_action,
                        pattern=rule_pattern,
                        source="user",
                        session_id=session_id,
                    )
                else:
                    grant_digest = (
                        str(record.get("security_grant_digest") or "").strip()
                        if decision_value == "allow_session"
                        else ""
                    ) or security_digest
                    add_approval_grant(
                        session_id,
                        grant_digest,
                        decision_value,
                    )
                resolve_tool_approval(session_id, approval_id, True)
            else:
                resolve_tool_approval(
                    session_id,
                    approval_id,
                    False,
                    str(record.get("rejection_reason") or ""),
                )
        await publish_session_event(session_id, {"type": "approval_resolved", **record})
        return JSONResponse(content={"ok": True, "approval": record})
    except Exception as exc:
        return _human_interaction_error_response(exc)


def _valid_selected_skill_names(raw_selected: Any) -> list[str]:
    if isinstance(raw_selected, str):
        try:
            requested = json.loads(raw_selected or "[]")
        except Exception:
            requested = []
    else:
        requested = raw_selected
    if not isinstance(requested, list):
        requested = []
    requested_names = [str(item).strip() for item in requested if str(item).strip()]
    if not requested_names:
        return []
    available = {str(skill.get("name") or "") for skill in discover_skills()}
    valid_names: list[str] = []
    seen = set()
    for name in requested_names:
        if name in available and name not in seen:
            seen.add(name)
            valid_names.append(name)
    return valid_names


def _build_agent_message_with_selected_skills(raw_message: str, valid_names: list[str]) -> str:
    if not valid_names:
        return raw_message
    lines = [
        raw_message,
        "",
        "<selected_skills_for_this_conversation>",
        "用户已在输入框中选择以下 Skill。请在本次对话中按需使用这些 Skill；需要具体规程时调用 activate_skill 读取对应说明：",
    ]
    lines.extend(f"- {name}" for name in valid_names)
    lines.append("</selected_skills_for_this_conversation>")
    return "\n".join(lines)


def _build_ui_message_with_selected_skills(raw_message: str, valid_names: list[str]) -> str:
    if not valid_names:
        return raw_message
    suffix = "\n\nActivated Skill: " + ", ".join(valid_names)
    return raw_message if raw_message.endswith(suffix) else raw_message + suffix


@fastapi_app.post("/sessions/{session_id}/steer")
async def post_session_steer(session_id: str, request: Request):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"ok": False, "error": "invalid json"}, status_code=400)
    message = str((data or {}).get("message") or "").strip()
    client_id = str((data or {}).get("client_id") or "").strip()
    selected_skills = (data or {}).get("selected_skills") or []
    requested_ui_content = str((data or {}).get("ui_content") or "").strip()
    source_run_id = str((data or {}).get("source_run_id") or "").strip()
    default_steer_mode = str(os.getenv("MYAGENT_STEER_MODE", "append") or "append").strip().lower()
    if default_steer_mode not in {"interrupt", "append"}:
        default_steer_mode = "append"
    steer_mode = str((data or {}).get("mode") or default_steer_mode).strip().lower()
    if not message:
        return JSONResponse(content={"ok": False, "error": "empty steer"}, status_code=400)
    if not client_id:
        return JSONResponse(content={"ok": False, "error": "missing client_id"}, status_code=400)
    if steer_mode not in {"interrupt", "append"}:
        return JSONResponse(content={"ok": False, "error": "invalid steer mode"}, status_code=400)
    if not _is_session_stream_active(sid):
        existing = get_session_steer(sid, client_id=client_id) if client_id else {"ok": False}
        if existing.get("ok"):
            existing_item = existing.get("item") if isinstance(existing.get("item"), dict) else {}
            is_restarting = str(existing_item.get("state") or "") == "restarting"
            return JSONResponse(content={
                "ok": True,
                "item": existing_item,
                "deduplicated": True,
                "restart": is_restarting,
                "replacement_run_id": str(existing_item.get("replacement_run_id") or ""),
                "aborted": False,
            })
        return JSONResponse(content={"ok": False, "error": "session is not running"}, status_code=409)
    if not isinstance(selected_skills, list):
        selected_skills = []
    valid_selected_skills = _valid_selected_skill_names(selected_skills)
    agent_message = _build_agent_message_with_selected_skills(message, valid_selected_skills)
    ui_message = _build_ui_message_with_selected_skills(requested_ui_content or message, valid_selected_skills)
    followup_restart_enabled = os.getenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1").strip().lower() in {"1", "true", "yes", "on"}
    result = enqueue_session_steer(
        sid,
        agent_message,
        client_id=client_id,
        ui_content=ui_message,
        source_run_id=source_run_id,
        mode=steer_mode,
    )
    if not result.get("ok"):
        return JSONResponse(content=result, status_code=400)
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    if str(item.get("mode") or steer_mode) == "append":
        # Append mode never aborts the active LLM/tool operation. The running
        # ReAct loop claims it after the current round is durably complete and
        # before constructing the next model request.
        result["aborted"] = False
        result["restart"] = False
        return JSONResponse(content=result)
    result["aborted"] = abort_session_steer_run(sid, reason="steer")
    if result["aborted"]:
        transitioned = transition_session_steer(
            sid, str(item.get("id") or ""), {"queued"}, "interrupting"
        )
        if transitioned.get("ok"):
            result["item"] = transitioned.get("item")
        result["restart"] = False
        return JSONResponse(content=result)
    if not followup_restart_enabled:
        return JSONResponse(content={"ok": False, "error": "session is not running"}, status_code=409)
    aborted = abort_session_steer_run(sid, reason="steer")
    session_manager.request_interrupt(sid, reason="followup")
    _active_chat_by_session.pop(sid, None)
    _active_chat_last_seen.pop(sid, None)
    with _chat_start_lock:
        _chat_starting_by_session.pop(sid, None)
    interrupted_run_ids = _interrupt_runtime_v2_active_runs(sid, reason="followup")
    try:
        from session_event_bus import close_session_stream, publish_session_event

        for rid in interrupted_run_ids:
            await publish_session_event(
                sid,
                {
                    "type": "run_interrupted",
                    "run_id": rid,
                    "reason": "followup",
                    "checkpoint_ok": False,
                    "cleanup_scope": "none",
                    "ephemeral": True,
                },
            )
        await close_session_stream(sid)
    except Exception as e:
        logger.debug("publish steer restart interrupt failed for %s: %s", sid, e)
    replacement_run_id = str(uuid.uuid4())
    transitioned = transition_session_steer(
        sid,
        str(item.get("id") or ""),
        {"queued", "interrupting"},
        "restarting",
        replacement_run_id=replacement_run_id,
    )
    steer_item = transitioned.get("item") if transitioned.get("ok") else item
    return JSONResponse(
        content={
            "ok": True,
            "restart": True,
            "aborted": bool(aborted or interrupted_run_ids),
            "item": steer_item,
            "replacement_run_id": replacement_run_id,
        }
    )


@fastapi_app.get("/sessions/{session_id}/steer/{steer_id}")
async def get_session_steer_status(session_id: str, steer_id: str):
    result = get_session_steer(session_id, steer_id=steer_id)
    return JSONResponse(content=result, status_code=200 if result.get("ok") else 404)


@fastapi_app.get("/sessions/{session_id}/steer")
async def list_session_steer_status(session_id: str, include_terminal: bool = False):
    result = list_session_steers(session_id, include_terminal=include_terminal)
    return JSONResponse(content=result, status_code=200 if result.get("ok") else 400)


@fastapi_app.post("/sessions/{session_id}/steer/{steer_id}/recover")
async def recover_session_steer(session_id: str, steer_id: str):
    current = get_session_steer(session_id, steer_id=steer_id)
    item = current.get("item") if isinstance(current.get("item"), dict) else {}
    if not current.get("ok"):
        return JSONResponse(content=current, status_code=404)
    state_name = str(item.get("state") or "")
    if state_name == "consumed":
        return JSONResponse(content=current)
    if state_name == "restarting" and item.get("replacement_run_id"):
        return JSONResponse(content=current)
    stale_claim = state_name == "claimed" and time.time() - float(item.get("claimed_at") or 0.0) >= 30.0
    if state_name == "claimed" and not stale_claim:
        return JSONResponse(content={"ok": False, "error": "steer claim is still active"}, status_code=409)
    replacement_run_id = str(item.get("replacement_run_id") or uuid.uuid4())
    result = transition_session_steer(
        session_id,
        steer_id,
        {"queued", "interrupting", "restarting", "claimed"},
        "restarting",
        replacement_run_id=replacement_run_id,
    )
    return JSONResponse(content=result, status_code=200 if result.get("ok") else 409)


@fastapi_app.post("/api/client_timing")
async def client_timing(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(content={"ok": False, "error": "body must be object"}, status_code=400)
    label = str(data.get("label") or "client_pipeline_step_timing").strip()[:80]
    session_id = str(data.get("session_id") or data.get("sessionId") or "").strip()[:120]
    run_id = str(data.get("run_id") or data.get("runId") or "").strip()[:120]
    step = str(data.get("step") or "").strip()[:120]
    final_step = str(data.get("final_step") or data.get("finalStep") or "").strip()[:120]
    mode = str(data.get("mode") or "").strip()[:80]
    try:
        ms = int(float(data.get("ms") or 0))
    except Exception:
        ms = 0
    try:
        total_ms = int(float(data.get("total_ms") or data.get("totalMs") or ms or 0))
    except Exception:
        total_ms = max(0, ms)
    try:
        since_start_ms = int(float(data.get("since_start_ms") or data.get("sinceStartMs") or 0))
    except Exception:
        since_start_ms = 0
    extra = data.get("extra")
    extra_text = ""
    if isinstance(extra, dict):
        clean_extra = {
            str(k)[:40]: str(v)[:120]
            for k, v in extra.items()
            if k is not None and v is not None
        }
        if clean_extra:
            extra_text = " " + " ".join(f"{k}={v}" for k, v in clean_extra.items())
    steps = data.get("steps")
    if isinstance(steps, dict):
        step_parts: list[str] = []
        for raw_name, raw_info in steps.items():
            name = str(raw_name or "").strip()[:80]
            if not name:
                continue
            info = raw_info if isinstance(raw_info, dict) else {}
            try:
                step_ms = int(float(info.get("ms") if isinstance(info, dict) else raw_info or 0))
            except Exception:
                step_ms = 0
            detail = f"{name}={max(0, step_ms)}ms"
            step_extra = info.get("extra") if isinstance(info, dict) else None
            if isinstance(step_extra, dict):
                clean_step_extra = {
                    str(k)[:30]: str(v)[:80]
                    for k, v in step_extra.items()
                    if k is not None and v is not None
                }
                if clean_step_extra:
                    detail += "(" + ",".join(f"{k}={v}" for k, v in clean_step_extra.items()) + ")"
            step_parts.append(detail)
        logger.info(
            "%s session=%s total=%sms run_id=%s mode=%s final_step=%s steps=%s",
            label,
            session_id,
            max(0, total_ms),
            run_id,
            mode,
            final_step,
            " ".join(step_parts),
        )
        return JSONResponse(content={"ok": True})
    logger.info(
        "%s session=%s step=%s ms=%sms since_start=%sms run_id=%s mode=%s%s",
        label,
        session_id,
        step,
        max(0, ms),
        max(0, since_start_ms),
        run_id,
        mode,
        extra_text,
    )
    return JSONResponse(content={"ok": True})


def _ui_presence_prune(now: float) -> None:
    expired = [
        token
        for token, entry in _ui_presence_tokens.items()
        if now - float(entry.get("seen_at") or 0) > _UI_PRESENCE_TOKEN_TTL_SEC
    ]
    for token in expired:
        _ui_presence_tokens.pop(token, None)


def _ui_presence_has_active() -> bool:
    """Return True when at least one WebUI tab is visible and focused."""

    return any(bool(entry.get("active")) for entry in _ui_presence_tokens.values())


def _ui_presence_has_reusable(now: Optional[float] = None) -> bool:
    """Return whether a page heartbeat is fresh enough for tray activation."""

    stamp = time.time() if now is None else float(now)
    return any(
        stamp - float(entry.get("seen_at") or 0) <= 20.0
        for entry in _ui_presence_tokens.values()
    )


def _notification_context(session_id: str, status: str, pending_count: int = 0) -> tuple[str, str]:
    """Build the stable status/session/question layout used by system notices."""

    sid = str(session_id or "").strip()
    try:
        summary = session_manager.get_session_summary(sid) if sid else None
    except Exception:
        summary = None
    summary = summary if isinstance(summary, dict) else {}
    name = str(summary.get("name") or "未命名会话").strip() or "未命名会话"
    preview = str(summary.get("last_user_preview") or "").strip() or "暂无"
    labels = {
        "completed": "已完成",
        "failed": "失败",
        "interrupted": "已中断",
        "pending": "待处理",
        "running": "后台运行中",
        "idle": "后台待命",
    }
    label = labels.get(str(status or ""), str(status or "后台待命"))
    if status == "pending" and pending_count > 0:
        label = f"{label}（{pending_count} 项）"
    return "SugarAgent", f"状态：{label}\n会话：{name}\n最近问题：{preview}"


def _runtime_status_payload() -> dict[str, Any]:
    """Return a lightweight UI/tray status without materializing chat history."""

    try:
        with session_manager._lock:
            sessions = [
                dict(row)
                for row in session_manager.index
                if isinstance(row, dict) and not row.get("archived")
            ]
    except Exception:
        sessions = []
    active_count = 0
    for row in sessions:
        sid = str((row or {}).get("id") or "").strip()
        if not sid:
            continue
        try:
            if _session_run_state_fields_light(sid).get("run_active"):
                active_count += 1
        except Exception:
            logger.debug("runtime status read failed for session %s", sid, exc_info=True)
    if active_count:
        status = "busy"
    else:
        # Alert: CPU severe pressure, or any non-archived session whose latest
        # run ended failed/interrupted (a manual pause ends the run as
        # "interrupted" and may show red). The alert clears once the run is
        # superseded by a newer run or the user resolved the residue, e.g. by
        # truncating the interrupted tail.
        alert = False
        try:
            from cpu_pressure import snapshot as cpu_snapshot

            alert = cpu_snapshot().mode == "severe"
        except Exception:
            logger.debug("runtime status cpu probe failed", exc_info=True)
        if not alert:
            try:
                from runtime_observability import snapshot as run_snapshot

                for row in sessions:
                    sid = str((row or {}).get("id") or "").strip()
                    if not sid:
                        continue
                    try:
                        data = run_snapshot(sid)
                        latest = (data.get("runs") or [None])[-1]
                    except Exception:
                        latest = None
                    if (
                        latest
                        and not latest.get("resolved")
                        and str(latest.get("status") or "") in {"failed", "interrupted"}
                    ):
                        alert = True
                        break
            except Exception:
                logger.debug("runtime status run-probe failed", exc_info=True)
        status = "alert" if alert else "online"

    with _ui_presence_lock:
        activation_seq = int(_ui_activation_seq)
        activation_path = str(_ui_activation_path or "/")
        activation_session = str(_ui_activation_session or "")
    return {
        "ok": True,
        "status": status,
        "active_run_count": active_count,
        "activation_seq": activation_seq,
        "activation_path": activation_path,
        "activation_session": activation_session,
    }


def _cancel_pending_ui_closed_notify() -> None:
    global _ui_closed_notify_task
    task = _ui_closed_notify_task
    _ui_closed_notify_task = None
    if task and not task.done():
        task.cancel()


def _cancel_pending_ui_attention_notify() -> None:
    global _ui_attention_notify_task
    task = _ui_attention_notify_task
    _ui_attention_notify_task = None
    if task and not task.done():
        task.cancel()
    with _ui_attention_notify_lock:
        _ui_attention_notify_reasons.clear()


def _schedule_ui_attention_notify(session_id: str, reason: str) -> None:
    """Schedule a desktop notification when the UI is not being actively used.

    Called from the session event bus for terminal run events and new pending
    human interactions. Reasons for every session are coalesced into one toast
    after the grace window; opening or focusing a WebUI tab cancels it.
    """

    global _ui_attention_notify_task
    if not _UI_CLOSED_NOTIFY_ENABLED or not str(session_id or "").strip():
        return
    with _ui_presence_lock:
        if _ui_presence_has_active():
            return
    with _ui_attention_notify_lock:
        _ui_attention_notify_reasons.setdefault(str(session_id), set()).add(reason)
        if _ui_attention_notify_task and not _ui_attention_notify_task.done():
            return
        _ui_attention_notify_task = asyncio.create_task(
            _delayed_ui_attention_notify()
        )


async def _delayed_ui_attention_notify() -> None:
    try:
        await asyncio.sleep(_UI_CLOSED_NOTIFY_GRACE_SEC)
        with _ui_attention_notify_lock:
            pending = {
                sid: set(reasons)
                for sid, reasons in _ui_attention_notify_reasons.items()
                if reasons
            }
            _ui_attention_notify_reasons.clear()
        if not pending:
            return
        with _ui_presence_lock:
            if _ui_presence_has_active():
                return

        notifications: list[tuple[str, str]] = []
        for sid, reasons in pending.items():
            pending_total = 0
            if "pending" in reasons:
                pending_total += await run_in_threadpool(
                    _session_pending_human_count, sid
                )
            if pending_total > 0:
                status = "pending"
            elif "failed" in reasons:
                status = "failed"
            elif "interrupted" in reasons:
                status = "interrupted"
            elif "completed" in reasons:
                status = "completed"
            else:
                continue
            ctx = await run_in_threadpool(
                _notification_context, sid, status, pending_total
            )
            notifications.append((ctx[0], ctx[1], sid))
        if not notifications:
            return
        global _last_ui_attention_notify_at
        _last_ui_attention_notify_at = time.time()
        for title, message, notify_session_id in notifications:
            await notify_user(title, message, session_id=notify_session_id)
    except asyncio.CancelledError:
        return
    finally:
        global _ui_attention_notify_task
        if _ui_attention_notify_task is asyncio.current_task():
            _ui_attention_notify_task = None


_UI_ATTENTION_NOTIFY_EVENT_TYPES = {
    "run_finished": "completed",
    "run_failed": "failed",
    "run_interrupted": "interrupted",
    "approval_requested": "pending",
    "interaction_requested": "pending",
}


def _on_session_event_for_attention_notify(session_id: str, event: dict) -> None:
    if not isinstance(event, dict) or event.get("_subagent_forward"):
        return
    reason = _UI_ATTENTION_NOTIFY_EVENT_TYPES.get(str(event.get("type") or ""))
    if not reason:
        return
    # Agent streams publish from worker-thread event loops; forward to the
    # main server loop so the delayed task outlives the worker stream.
    loop = _UI_ATTENTION_MAIN_LOOP
    if loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(
            _schedule_ui_attention_notify,
            str(session_id or ""),
            reason,
        )
    except Exception:
        logger.debug("UI attention notify schedule failed", exc_info=True)


add_event_listener(_on_session_event_for_attention_notify)


async def _schedule_ui_closed_notify() -> None:
    global _ui_closed_notify_task
    if not _UI_CLOSED_NOTIFY_ENABLED:
        return
    if _ui_closed_notify_task and not _ui_closed_notify_task.done():
        return
    # A completion/pending-item toast is more specific; don't stack a generic
    # "still running in the background" toast on top of it.
    if _ui_attention_notify_task and not _ui_attention_notify_task.done():
        return
    with _ui_attention_notify_lock:
        if _ui_attention_notify_reasons:
            return
    if time.time() - _last_ui_attention_notify_at < 60:
        return

    async def delayed_notify() -> None:
        try:
            await asyncio.sleep(_UI_CLOSED_NOTIFY_GRACE_SEC)
            with _ui_presence_lock:
                if _ui_presence_tokens:
                    return
            # Page-closed notice is session-agnostic: restore the original
            # "still running in the background" copy instead of a
            # status/session/question summary.
            await notify_user()
        except asyncio.CancelledError:
            return
        finally:
            global _ui_closed_notify_task
            if _ui_closed_notify_task is asyncio.current_task():
                _ui_closed_notify_task = None

    _ui_closed_notify_task = asyncio.create_task(delayed_notify())


@fastapi_app.post("/api/ui-presence")
async def ui_presence(request: Request):
    """Track open WebUI tabs so the backend can notify after the last one closes."""

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return JSONResponse(content={"ok": False, "error": "body must be object"}, status_code=400)

    action = str(data.get("action") or "").strip().lower()
    token = str(data.get("token") or "").strip()
    if action not in {"register", "update", "unregister"}:
        return JSONResponse(content={"ok": False, "error": "invalid action"}, status_code=400)
    if not token or len(token) > 200:
        return JSONResponse(content={"ok": False, "error": "missing token"}, status_code=400)

    active = bool(data.get("active", True))
    now = time.time()
    global _last_ui_session_id
    session_id = str(data.get("session_id") or "").strip()
    with _ui_presence_lock:
        if session_id:
            _last_ui_session_id = session_id
        if action == "register":
            _ui_presence_tokens[token] = {
                "seen_at": now,
                "active": active,
                "session_id": session_id,
            }
        elif action == "update":
            entry = _ui_presence_tokens.get(token)
            if entry is not None:
                entry["seen_at"] = now
                entry["active"] = active
                if session_id:
                    entry["session_id"] = session_id
        else:
            _ui_presence_tokens.pop(token, None)
        _ui_presence_prune(now)

    with _ui_presence_lock:
        has_active_ui = _ui_presence_has_active()
        has_open_pages = bool(_ui_presence_tokens)

    if action == "register":
        _cancel_pending_ui_closed_notify()
    if action in {"register", "update"} and has_active_ui:
        _cancel_pending_ui_attention_notify()

    if action == "register":
        return JSONResponse(content={"ok": True, "action": "register"})
    if action == "update":
        return JSONResponse(content={"ok": True, "action": "update"})
    if not has_open_pages:
        await _schedule_ui_closed_notify()
    return JSONResponse(content={"ok": True, "action": "unregister"})


@fastapi_app.get("/api/runtime-status")
async def runtime_status():
    return JSONResponse(content=await asyncio.to_thread(_runtime_status_payload))


@fastapi_app.post("/api/ui-activation")
async def request_ui_activation(request: Request):
    """Signal an existing main WebUI page before the tray opens another tab."""

    try:
        data = await request.json()
    except Exception:
        data = {}
    path = str((data or {}).get("path") or "/").strip()
    if path != "/":
        path = "/"
    raw_session = str((data or {}).get("session") or "").strip()
    session = re.sub(r"[^A-Za-z0-9\-]", "", raw_session)[:64]
    now = time.time()
    global _ui_activation_seq, _ui_activation_path, _ui_activation_session
    with _ui_presence_lock:
        _ui_presence_prune(now)
        reused = _ui_presence_has_reusable(now)
        if reused:
            _ui_activation_seq += 1
            _ui_activation_path = path
            _ui_activation_session = session
        seq = int(_ui_activation_seq)
    return JSONResponse(content={"ok": True, "reused": reused, "activation_seq": seq})


@fastapi_app.delete("/sessions/{session_id}/steer")
async def delete_session_steer(session_id: str, request: Request):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"ok": False, "error": "missing session_id"}, status_code=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    steer_id = str((data or {}).get("steer_id") or "").strip()
    client_id = str((data or {}).get("client_id") or "").strip()
    result = remove_session_steer(sid, steer_id=steer_id, client_id=client_id)
    if not result.get("ok"):
        return JSONResponse(content=result, status_code=409)
    return JSONResponse(content=result)


@fastapi_app.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(None),
    client_run_id: str = Form(None),
    stream_protocol: str = Form("runtime_v2"),
    followup_steer: bool = Form(False),
    steer_id: str = Form(""),
    selected_skills: str = Form(""),
    ui_message: str = Form(""),
    ui_language: str = Form("zh-CN"),
    attachments: str = Form(""),
    preserve_unread_result: bool = Form(False),
):
    sid = (session_id or "").strip() or None
    run_id = str(client_run_id or "").strip()
    steer_operation_id = str(steer_id or "").strip()
    logger.info("chat_request_received sid=%s run_id=%s steer=%s", sid, run_id, steer_operation_id)
    prompt_language = normalize_prompt_language(ui_language)
    use_runtime_v2_stream = _runtime_v2_chat_protocol_enabled(stream_protocol, sid)
    start_token = ""
    if sid:
        try:
            from runtime_v2 import runtime_v2_primary

            migration_gate = (
                _runtime_v2_legacy_only_migration_pending(sid)
                if runtime_v2_primary()
                else {"pending": False}
            )
        except Exception:
            migration_gate = {"pending": False}
        if migration_gate.get("pending"):
            return JSONResponse(
                content={
                    "ok": False,
                    "reason": "runtime_migration_pending",
                    "session_id": sid,
                    **migration_gate,
                },
                status_code=425,
            )
        if followup_steer and steer_operation_id:
            steer_status = get_session_steer(sid, steer_id=steer_operation_id)
            steer_item = steer_status.get("item") if isinstance(steer_status.get("item"), dict) else {}
            if not steer_status.get("ok"):
                return JSONResponse(content={"ok": False, "reason": "unknown_steer"}, status_code=409)
            if str(steer_item.get("state") or "") == "consumed":
                return JSONResponse(content={"ok": False, "reason": "duplicate_steer"}, status_code=409)
            if str(steer_item.get("state") or "") != "restarting":
                return JSONResponse(
                    content={"ok": False, "reason": "steer_already_claimed", "state": steer_item.get("state")},
                    status_code=409,
                )
            expected_run_id = str(steer_item.get("replacement_run_id") or "").strip()
            if expected_run_id and run_id and expected_run_id != run_id:
                return JSONResponse(content={"ok": False, "reason": "replacement_run_mismatch"}, status_code=409)
            claimed = transition_session_steer(
                sid,
                steer_operation_id,
                {"restarting"},
                "claimed",
                claimed_by=run_id,
                claimed_at=time.time(),
            )
            if not claimed.get("ok"):
                return JSONResponse(content={"ok": False, "reason": "steer_already_claimed"}, status_code=409)
        start_token = _reserve_session_chat_start(sid, run_id) or ""
        if not start_token:
            if followup_steer and steer_operation_id:
                transition_session_steer(
                    sid, steer_operation_id, {"claimed"}, "restarting", claimed_by="", claimed_at=0
                )
            run_state = _session_run_state_fields(sid)
            return JSONResponse(
                content={
                    "ok": False,
                    "reason": "busy",
                    "session_id": sid,
                    "requested_run_id": run_id,
                    "active_run": run_state.get("active_run"),
                    "stream_connections": int(run_state.get("stream_connections") or 0),
                },
                status_code=409,
            )
        session_manager.clear_interrupt(sid, run_id)

    def should_stop(sid_: str) -> bool:
        return session_manager.is_interrupt_requested(sid_, run_id)

    def selected_skill_names(selected_raw: str) -> list[str]:
        try:
            requested = json.loads(selected_raw or "[]")
        except Exception:
            requested = []
        if not isinstance(requested, list):
            requested = []
        requested_names = [str(item).strip() for item in requested if str(item).strip()]
        if not requested_names:
            return []
        available = {str(skill.get("name") or "") for skill in discover_skills()}
        valid_names = []
        seen = set()
        for name in requested_names:
            if name in available and name not in seen:
                seen.add(name)
                valid_names.append(name)
        return valid_names

    def build_agent_message(raw_message: str, valid_names: list[str]) -> str:
        if not valid_names:
            return raw_message
        lines = [
            raw_message,
            "",
            "<selected_skills_for_this_conversation>",
            "用户已在输入框中选择以下 Skill。请在本次对话中按需使用这些 Skill；需要具体规程时调用 activate_skill 读取对应说明：",
        ]
        lines.extend(f"- {name}" for name in valid_names)
        lines.append("</selected_skills_for_this_conversation>")
        return "\n".join(lines)

    valid_selected_skills = selected_skill_names(selected_skills)
    agent_message = build_agent_message(message, valid_selected_skills)
    structured_attachments: list[dict[str, Any]] = []
    try:
        requested_attachments = json.loads(attachments or "[]")
    except Exception:
        requested_attachments = []
    if isinstance(requested_attachments, list):
        seen_attachment_paths: set[str] = set()
        for item in requested_attachments[:16]:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            try:
                attachment_path = Path(raw_path).expanduser().resolve()
            except OSError:
                continue
            path_key = os.path.normcase(str(attachment_path))
            if path_key in seen_attachment_paths or not attachment_path.is_file():
                continue
            seen_attachment_paths.add(path_key)
            structured_attachments.append(
                {
                    "type": "local_file",
                    "local_file": {
                        "path": str(attachment_path),
                        "name": str(item.get("name") or attachment_path.name),
                    },
                }
            )
    structured_user_content = (
        [{"type": "text", "text": agent_message}, *structured_attachments]
        if structured_attachments
        else None
    )
    ui_base_message = str(ui_message or "").strip() or message
    ui_message = _build_ui_message_with_selected_skills(ui_base_message, valid_selected_skills)

    async def event_generator():
        if sid:
            _active_chat_by_session[sid] = _active_chat_by_session.get(sid, 0) + 1
            import time as _time_stamp; _active_chat_last_seen[sid] = _time_stamp.time()
        main_loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue = asyncio.Queue()
        stop_event = threading.Event()
        client_disconnected = False
        def should_stop_worker(sid_: str) -> bool:
            return stop_event.is_set() or should_stop(sid_)

        def put_from_worker(item) -> None:
            if client_disconnected:
                return
            try:
                main_loop.call_soon_threadsafe(event_queue.put_nowait, item)
            except Exception:
                pass

        async def consume_agent_stream() -> None:
            try:
                async for event in astream_events(
                    agent_message,
                    session_id=sid,
                    should_stop=should_stop_worker,
                    run_id=run_id,
                    ui_user_event_type="user_steer" if followup_steer else "user",
                    ui_user_content=ui_message,
                    user_operation_id=steer_operation_id,
                    prompt_language=prompt_language,
                    user_content=structured_user_content,
                    preserve_unread_result=preserve_unread_result,
                ):
                    put_from_worker(event)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                put_from_worker({"type": "error", "content": str(exc), "ephemeral": True})
            finally:
                put_from_worker(None)

        def worker_main() -> None:
            asyncio.run(consume_agent_stream())

        threading.Thread(
            target=worker_main,
            name=f"chat-stream-{sid or 'new'}",
            daemon=True,
        ).start()
        try:
            try:
                skip_worker_run_started = False
                skip_worker_start_status = False
                if sid and not use_runtime_v2_stream:
                    yield f"data: {json.dumps({'type': 'run_started', 'run_id': run_id, 'ephemeral': True}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'status', 'content': 'New Agent Loop Start', 'ephemeral': True}, ensure_ascii=False)}\n\n"
                    skip_worker_run_started = True
                    skip_worker_start_status = True

                while True:
                    if sid and await request.is_disconnected():
                        client_disconnected = True
                        logger.info("Chat stream disconnected for session %s; leaving run active", sid)
                        try:
                            await _schedule_ui_closed_notify()
                        except Exception:
                            logger.debug("ui-closed notify schedule failed", exc_info=True)
                        break
                    try:
                        event = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=_CHAT_SSE_KEEPALIVE_SEC,
                        )
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'type': 'sse_keepalive', 'keepalive': True, 'session_id': sid}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0)
                        continue
                    if event is None:
                        break
                    if skip_worker_run_started and isinstance(event, dict) and event.get("type") == "run_started":
                        skip_worker_run_started = False
                        continue
                    if (
                        skip_worker_start_status
                        and isinstance(event, dict)
                        and event.get("type") == "status"
                        and str(event.get("content") or "") == "New Agent Loop Start"
                    ):
                        skip_worker_start_status = False
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)  # 让 ASGI/uvicorn 尽快把分块刷到客户端，利于工具/LLM 分条显示
                if not await request.is_disconnected():
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                # 浏览器主动断开 SSE 连接属于正常情况，避免打印冗长异常栈
                client_disconnected = True
                if sid:
                    try:
                        await _schedule_ui_closed_notify()
                    except Exception:
                        logger.debug("ui-closed notify schedule failed", exc_info=True)
                return
        finally:
            if not client_disconnected:
                stop_event.set()
            if sid:
                _release_session_chat_start(sid, start_token)
            if sid:
                n = _active_chat_by_session.get(sid, 1) - 1
                if n <= 0:
                    _active_chat_by_session.pop(sid, None)
                else:
                    _active_chat_by_session[sid] = n

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@fastapi_app.get("/sessions/{session_id}/stream")
async def stream_session_events(
    session_id: str,
    request: Request,
    after_index: Optional[int] = Query(None, ge=-1),
):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)

    async def runtime_v2_event_generator():
        _observer_streams_by_session[sid] = _observer_streams_by_session.get(sid, 0) + 1
        cursor = int(after_index) if after_index is not None else -1
        runtime_cursor: Optional[int] = None
        subscription = subscribe_session_events(sid, replay_recent=True)
        next_live_event = asyncio.create_task(subscription.__anext__())

        async def drain_projection(projection):
            nonlocal cursor, runtime_cursor
            while True:
                incremental = runtime_cursor is not None
                if incremental:
                    page = await asyncio.to_thread(
                        projection.read_ui_after_runtime_seq,
                        sid,
                        after_runtime_seq=runtime_cursor,
                        limit=100,
                    )
                    if page.get("requires_reprojection"):
                        reprojected_through = int(page.get("last_runtime_seq") or 0)
                        incremental = False
                        page = await asyncio.to_thread(
                            projection.read_ui_page,
                            sid,
                            after_index=cursor,
                            limit=100,
                        )
                        page["reprojected_through"] = reprojected_through
                else:
                    page = await asyncio.to_thread(
                        projection.read_ui_page,
                        sid,
                        after_index=cursor,
                        limit=100,
                    )
                events = page.get("events") if isinstance(page, dict) else []
                if not isinstance(events, list) or not events:
                    if incremental:
                        runtime_cursor = max(
                            int(runtime_cursor or 0),
                            int(page.get("last_runtime_seq") or 0),
                        )
                    elif runtime_cursor is None:
                        runtime_cursor = max(0, projection.event_log.next_seq(sid) - 1)
                    runtime_cursor = max(
                        int(runtime_cursor or 0),
                        int(page.get("reprojected_through") or 0),
                    )
                    break
                start = cursor + 1 if incremental else int(page.get("range_start") or (cursor + 1))
                for offset, event in enumerate(events):
                    if await request.is_disconnected():
                        return
                    if not isinstance(event, dict):
                        continue
                    event_index = start + offset
                    payload = dict(event)
                    payload["session_id"] = sid
                    payload["seq"] = event_index + 1
                    payload["seq_scope"] = "ui_projection"
                    yield_payload = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    cursor = max(cursor, event_index)
                    runtime_seq = payload.get("runtime_seq")
                    if runtime_seq is not None:
                        runtime_cursor = max(int(runtime_cursor or 0), int(runtime_seq))
                    yield yield_payload
                    await asyncio.sleep(0)
                if incremental:
                    runtime_cursor = max(
                        int(runtime_cursor or 0),
                        int(page.get("last_runtime_seq") or 0),
                    )
                runtime_cursor = max(
                    int(runtime_cursor or 0),
                    int(page.get("reprojected_through") or 0),
                )
                if len(events) < 100 and not page.get("has_more"):
                    break

        try:
            try:
                from runtime_v2.ui_projection import RuntimeUiProjection

                projection = RuntimeUiProjection(
                    session_manager.repository.sessions_dir,
                    path_resolver=session_manager._resolve_session_path,
                )
                # Start the live subscription before reading the projection. Any
                # event committed during catch-up is queued, closing the old
                # query-then-subscribe race that could hide tool/final events.
                await asyncio.sleep(0)
                async for payload in drain_projection(projection):
                    yield payload
                if await request.is_disconnected():
                    return
                while True:
                    if await request.is_disconnected():
                        break
                    if not _has_local_worker_activity(sid) and not next_live_event.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(next_live_event), timeout=0.05)
                        except asyncio.TimeoutError:
                            async for payload in drain_projection(projection):
                                yield payload
                            yield "data: [DONE]\n\n"
                            return
                    try:
                        event = await asyncio.wait_for(asyncio.shield(next_live_event), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield f": observer keepalive {cursor}\n\n"
                        continue
                    except StopAsyncIteration:
                        event = None
                    if event is None:
                        async for payload in drain_projection(projection):
                            yield payload
                        break
                    next_live_event = asyncio.create_task(subscription.__anext__())
                    if event.get("ephemeral"):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0)
                    else:
                        # Durable events are rendered from the V2 projection so
                        # reconnect and live paths share one ordering/index source.
                        async for payload in drain_projection(projection):
                            yield payload
                if not await request.is_disconnected():
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                return
        finally:
            if not next_live_event.done():
                next_live_event.cancel()
            try:
                await next_live_event
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            except Exception:
                logger.debug("observer live-event task cleanup failed for %s", sid, exc_info=True)
            try:
                await subscription.aclose()
            except Exception:
                pass
            n = _observer_streams_by_session.get(sid, 1) - 1
            if n <= 0:
                _observer_streams_by_session.pop(sid, None)
            else:
                _observer_streams_by_session[sid] = n
            if await request.is_disconnected():
                try:
                    await _schedule_ui_closed_notify()
                except Exception:
                    logger.debug("ui-closed notify schedule failed", exc_info=True)

    try:
        from runtime_v2 import runtime_v2_primary

        if runtime_v2_primary():
            return StreamingResponse(
                runtime_v2_event_generator(),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )
    except Exception as exc:
        logger.debug("Runtime V2 stream path check failed for %s: %s", sid, exc)

    async def event_generator():
        _observer_streams_by_session[sid] = _observer_streams_by_session.get(sid, 0) + 1
        try:
            async for event in subscribe_session_events(sid, replay_recent=True):
                if await request.is_disconnected():
                    break
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
            if not await request.is_disconnected():
                yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            return
        finally:
            n = _observer_streams_by_session.get(sid, 1) - 1
            if n <= 0:
                _observer_streams_by_session.pop(sid, None)
            else:
                _observer_streams_by_session[sid] = n

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@fastapi_app.post("/sessions/{session_id}/continue")
async def continue_react_session(
    session_id: str,
    request: Request,
    recovery: bool = Query(False),
):
    """Continue a parent ReAct loop that has no final answer yet."""
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    pending_human = _session_pending_human_counts(sid)
    if int(pending_human.get("total") or 0) > 0:
        return JSONResponse(
            content={
                "ok": False,
                "reason": "pending_human_interaction",
                "pending_human_interactions": pending_human,
            },
            status_code=409,
        )
    if recovery and _session_was_manually_stopped(sid):
        return JSONResponse(
            content={"ok": False, "reason": "manually_stopped"},
            status_code=409,
        )
    from workflow_extensions import session_workflows
    workflow_source = session_workflows.continuation_source(sid)
    if not session_manager.can_continue_react_session(sid) and not workflow_source:
        return Response(status_code=204)
    continuation_run_id = "react-continue-" + uuid.uuid4().hex
    start_token = _reserve_session_chat_start(sid, continuation_run_id) or ""
    if not start_token:
        return JSONResponse(content={"ok": False, "reason": "busy"}, status_code=409)

    def should_stop(sid_: str) -> bool:
        return session_manager.is_interrupt_requested(sid_)

    async def event_generator():
        _active_chat_by_session[sid] = _active_chat_by_session.get(sid, 0) + 1
        import time as _time_stamp
        _active_chat_last_seen[sid] = _time_stamp.time()
        try:
            try:
                async for event in astream_events_continuation(
                    sid,
                    should_stop=should_stop,
                    require_pending_subagents=False,
                    recovery_reason="process_or_network_interruption" if recovery and not workflow_source else "",
                    run_id=continuation_run_id,
                    continuation_source=(
                        workflow_source if workflow_source else ("recovery" if recovery else "subagent")
                    ),
                ):
                    if await request.is_disconnected():
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)
                if not await request.is_disconnected():
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                return
        finally:
            n = _active_chat_by_session.get(sid, 1) - 1
            if n <= 0:
                _active_chat_by_session.pop(sid, None)
            else:
                _active_chat_by_session[sid] = n
            _release_session_chat_start(sid, start_token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@fastapi_app.post("/sessions/{session_id}/continue-subagents")
async def continue_after_subagents(session_id: str, request: Request):
    """后台 subagent 完成后自动续接父 Agent（无新用户气泡）。"""
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    if not session_manager.has_pending_subagent_notifications(sid):
        return Response(status_code=204)
    if not session_manager.can_continue_after_subagents(sid):
        return Response(status_code=204)
    if _is_session_stream_active(sid):
        return JSONResponse(content={"ok": False, "reason": "busy"}, status_code=409)

    def should_stop(sid_: str) -> bool:
        return session_manager.is_interrupt_requested(sid_)

    async def event_generator():
        _active_chat_by_session[sid] = _active_chat_by_session.get(sid, 0) + 1
        import time as _time_stamp
        _active_chat_last_seen[sid] = _time_stamp.time()
        try:
            try:
                async for event in astream_events_continuation(
                    sid,
                    should_stop=should_stop,
                ):
                    if await request.is_disconnected():
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)
                if not await request.is_disconnected():
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                return
        finally:
            n = _active_chat_by_session.get(sid, 1) - 1
            if n <= 0:
                _active_chat_by_session.pop(sid, None)
            else:
                _active_chat_by_session[sid] = n

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@fastapi_app.post("/sessions/{session_id}/continue-subagents/dismiss")
async def dismiss_continue_after_subagents(session_id: str):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"ok": False, "error": "missing session_id"}, status_code=400)
    removed = session_manager.dismiss_pending_subagent_notifications(sid)
    return JSONResponse(content={"ok": True, "removed": removed})

@fastapi_app.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: Optional[int] = Query(None, ge=1, le=500),
    before_index: Optional[int] = Query(None, ge=0),
    after_index: Optional[int] = Query(None, ge=-1),
    target_index: Optional[int] = Query(None, ge=0),
    turns: Optional[int] = Query(None, ge=1, le=50),
    event_budget: Optional[int] = Query(None, ge=50, le=5000),
):
    """
    与 SSE 同源：默认返回完整 ui_events 数组（兼容旧前端）。
    传入 limit 或 turns 时返回分页对象。
    turns：按「用户提问」轮次分页（每页若干完整对话）；优先于 limit。
    """
    import time as _time
    t0 = _time.perf_counter()
    projection = None
    target_index_value: Optional[int] = None
    if isinstance(target_index, int):
        target_index_value = target_index
    event_budget_value = event_budget if isinstance(event_budget, int) else None
    def _build_messages_response() -> JSONResponse:
        nonlocal projection
        try:
            from runtime_v2 import runtime_v1_primary

            if runtime_v1_primary():
                if after_index is not None:
                    events = session_manager.get_ui_events_for_display(session_id)
                    total = len(events)
                    start = max(0, min(int(after_index) + 1, total))
                    lim = int(limit) if limit is not None else 500
                    end = min(total, start + max(1, min(lim, 500)))
                    return JSONResponse(content={
                        "events": events[start:end],
                        "total": total,
                        "range_start": start,
                        "range_end": end,
                        "has_older": start > 0,
                        "has_newer": end < total,
                        "source": "runtime_v1_after_index",
                    })
                if limit is None and turns is None and before_index is None and after_index is None and target_index_value is None:
                    payload = session_manager.get_ui_events_for_display(session_id)
                    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
                    if elapsed_ms >= 500:
                        logger.warning("/messages slow runtime=1 session=%s full=1 elapsed_ms=%s", session_id, elapsed_ms)
                    return JSONResponse(content=payload)
                lim = int(limit) if limit is not None else 200
                tv = int(turns) if turns is not None else None
                payload = session_manager.get_ui_events_page(
                    session_id,
                    limit=lim,
                    before_index=before_index,
                    target_index=target_index_value,
                    turns=tv,
                )
                elapsed_ms = int((_time.perf_counter() - t0) * 1000)
                if elapsed_ms >= 500:
                    logger.warning(
                        "/messages slow runtime=1 session=%s turns=%s limit=%s before=%s elapsed_ms=%s",
                        session_id,
                        tv,
                        lim,
                        before_index,
                        elapsed_ms,
                    )
                return JSONResponse(content=payload)
        except Exception as exc:
            logger.warning("Runtime version check failed for messages %s: %s", session_id, exc)
        migration_gate = _runtime_v2_legacy_only_migration_pending(session_id)
        if migration_gate.get("pending"):
            return JSONResponse(content={
                "ok": False,
                "source": "runtime_v2_migration",
                "migration_pending": True,
                "session_id": session_id,
                **migration_gate,
            }, status_code=425)
        try:
            if projection is None:
                from runtime_v2.ui_projection import RuntimeUiProjection

                projection = RuntimeUiProjection(
                    session_manager.repository.sessions_dir,
                    path_resolver=session_manager._resolve_session_path,
                )
            if limit is None and turns is None and before_index is None and after_index is None and target_index_value is None:
                payload = projection.read_ui_events(session_id)
                elapsed_ms = int((_time.perf_counter() - t0) * 1000)
                if elapsed_ms >= 500:
                    logger.warning("/messages slow runtime=2 session=%s full=1 elapsed_ms=%s", session_id, elapsed_ms)
                return JSONResponse(content=payload)
            lim = int(limit) if limit is not None else 200
            tv = int(turns) if turns is not None else None
            payload = projection.read_ui_page(
                session_id,
                limit=lim,
                before_index=before_index,
                after_index=after_index,
                target_index=target_index_value,
                turns=tv,
                event_budget=event_budget_value,
            )
            elapsed_ms = int((_time.perf_counter() - t0) * 1000)
            if elapsed_ms >= 500:
                logger.warning(
                    "/messages slow runtime=2 session=%s turns=%s limit=%s before=%s elapsed_ms=%s",
                    session_id,
                    tv,
                    lim,
                    before_index,
                    elapsed_ms,
                )
            return JSONResponse(content=payload)
        except Exception as exc:
            logger.warning("Runtime V2 messages projection failed for %s: %s", session_id, exc)
            return JSONResponse(content={
                "source": "runtime_v2_projection_error",
                "error": "runtime_v2_projection_failed",
                "repair_required": True,
                "detail": str(exc),
            }, status_code=500)

    return await asyncio.to_thread(_build_messages_response)


@fastapi_app.get("/sessions/{session_id}/messages/count")
async def get_session_message_count(session_id: str):
    def _build_count_response() -> JSONResponse:
        try:
            from runtime_v2 import runtime_v1_primary

            if runtime_v1_primary():
                return JSONResponse(content={"count": session_manager.get_ui_event_count(session_id), "source": "runtime_v1"})
        except Exception as exc:
            logger.warning("Runtime version check failed for message count %s: %s", session_id, exc)
        try:
            from runtime_v2.ui_projection import RuntimeUiProjection

            projection = RuntimeUiProjection(
                session_manager.repository.sessions_dir,
                path_resolver=session_manager._resolve_session_path,
            )
            count, _ = projection.count_ui_events_light(session_id)
            return JSONResponse(content={"count": count, "source": "runtime_v2"})
        except Exception as exc:
            logger.warning("Runtime V2 message count failed for %s: %s", session_id, exc)
            return JSONResponse(content={
                "source": "runtime_v2_projection_error",
                "error": "runtime_v2_projection_failed",
                "repair_required": True,
                "detail": str(exc),
            }, status_code=500)

    return await asyncio.to_thread(_build_count_response)
    """仅返回 ui_events 条数，供发送前对齐 eventIndex，避免下载整份 JSON。"""


@fastapi_app.get("/sessions/{session_id}/history_snapshot")
async def get_session_history_snapshot(
    session_id: str,
    limit: Optional[int] = Query(None, ge=1, le=500),
    before_index: Optional[int] = Query(None, ge=0),
    after_index: Optional[int] = Query(None, ge=-1),
    turns: Optional[int] = Query(5, ge=1, le=50),
    event_budget: Optional[int] = Query(None, ge=50, le=5000),
    include_aux: bool = Query(True),
):
    """Return the initial V2 history page plus TOC/count in one request."""
    import time as _time

    t0 = _time.perf_counter()

    def _build_snapshot_response() -> JSONResponse:
        try:
            from runtime_v2 import runtime_v2_primary

            if not runtime_v2_primary():
                return JSONResponse(content={
                    "ok": False,
                    "source": "runtime_v1",
                    "error": "runtime_v2_required",
                }, status_code=409)
        except Exception as exc:
            logger.warning("Runtime version check failed for history snapshot %s: %s", session_id, exc)
            return JSONResponse(content={
                "ok": False,
                "source": "runtime_unknown",
                "error": "runtime_version_check_failed",
            }, status_code=409)
        migration_gate = _runtime_v2_legacy_only_migration_pending(session_id)
        if migration_gate.get("pending"):
            return JSONResponse(content={
                "ok": False,
                "source": "runtime_v2_migration",
                "migration_pending": True,
                "session_id": session_id,
                **migration_gate,
            }, status_code=425)
        try:
            from runtime_v2.ui_projection import RuntimeUiProjection

            projection = RuntimeUiProjection(
                session_manager.repository.sessions_dir,
                path_resolver=session_manager._resolve_session_path,
            )
            lim = int(limit) if limit is not None else 200
            tv = int(turns) if turns is not None else None
            event_budget_value = event_budget if isinstance(event_budget, int) else None
            timings: dict[str, int] = {}
            t_phase = _time.perf_counter()
            page = projection.read_ui_page(
                session_id,
                limit=lim,
                before_index=before_index,
                after_index=after_index,
                turns=tv,
                event_budget=event_budget_value,
            )
            timings["read_page"] = int((_time.perf_counter() - t_phase) * 1000)
            t_phase = _time.perf_counter()
            page_total = page.get("total") if isinstance(page, dict) else None
            if isinstance(page_total, int):
                count, count_source = page_total, "runtime_v2_page"
            else:
                count, count_source = projection.count_ui_events_light(session_id)
            timings["count"] = int((_time.perf_counter() - t_phase) * 1000)
            t_phase = _time.perf_counter()
            user_turns = projection.read_user_turns_light(session_id)
            timings["user_turns"] = int((_time.perf_counter() - t_phase) * 1000)
            include_aux_value = include_aux if isinstance(include_aux, bool) else True
            context_tokens = None
            todo_plan = None
            if include_aux_value:
                t_phase = _time.perf_counter()
                runtime_snapshot = _runtime_v2_snapshot(session_id, fail_closed=True)
                runtime_context = runtime_snapshot.get("context") if isinstance(runtime_snapshot, dict) else None
                if not isinstance(runtime_context, dict):
                    runtime_context = {}
                context_tokens = runtime_context.get("tokens")
                if not isinstance(context_tokens, dict):
                    context_tokens = None
                elif context_tokens.get("stale"):
                    context_tokens = dict(context_tokens)
                    context_tokens["pending_recalculation"] = True
                    context_tokens["source"] = "runtime_v2_snapshot_stale"
                timings["context_tokens"] = int((_time.perf_counter() - t_phase) * 1000)
                t_phase = _time.perf_counter()
                todo_plan = _runtime_v2_todo_plan_snapshot(session_id)
                timings["todo_plan"] = int((_time.perf_counter() - t_phase) * 1000)
            else:
                # The browser fetches these panels after the chat's first paint.
                # Keeping them off the critical history path avoids parsing a
                # multi-megabyte Runtime snapshot before any messages appear.
                timings["context_tokens"] = 0
                timings["todo_plan"] = 0
            elapsed_ms = int((_time.perf_counter() - t0) * 1000)
            timings["total"] = elapsed_ms
            logger.info(
                "open_session_timing session=%s source=%s page_source=%s messages=%s "
                "total=%sms read_page=%sms count=%sms user_turns=%sms context_tokens=%sms todo_plan=%sms",
                session_id,
                "runtime_v2_snapshot",
                str(page.get("source") or "runtime_v2_projection") if isinstance(page, dict) else "unknown",
                len(page.get("events") or []) if isinstance(page, dict) else 0,
                elapsed_ms,
                timings["read_page"],
                timings["count"],
                timings["user_turns"],
                timings["context_tokens"],
                timings["todo_plan"],
            )
            if elapsed_ms >= 500:
                logger.warning(
                    "/history_snapshot slow runtime=2 session=%s turns=%s limit=%s before=%s elapsed_ms=%s read_page=%sms count=%sms user_turns=%sms context_tokens=%sms",
                    session_id,
                    tv,
                    lim,
                    before_index,
                    elapsed_ms,
                    timings["read_page"],
                    timings["count"],
                    timings["user_turns"],
                    timings["context_tokens"],
                )
            try:
                run_state = _session_run_state_fields_light(session_id)
            except Exception:
                run_state = {"stream_active": False, "run_active": False, "run_started_at": None}
            return JSONResponse(content={
                "ok": True,
                "source": "runtime_v2_snapshot",
                "session_id": session_id,
                "messages": page,
                "count": count,
                "count_source": count_source,
                "user_turns": user_turns,
                "todo_plan": todo_plan,
                "context_tokens": context_tokens,
                "elapsed_ms": elapsed_ms,
                "timing": timings,
                "stream_active": bool(run_state.get("stream_active")),
                "run_active": bool(run_state.get("run_active")),
                "run_started_at": run_state.get("run_started_at"),
            })
        except Exception as exc:
            logger.warning("Runtime V2 history snapshot failed for %s: %s", session_id, exc)
            return JSONResponse(content={
                "ok": False,
                "source": "runtime_v2_projection_error",
                "session_id": session_id,
                "error": str(exc),
            }, status_code=500)

    return await asyncio.to_thread(_build_snapshot_response)


class RuntimeSyncBusyError(RuntimeError):
    pass


def _sync_runtime_session_unlocked(
    session_id: str,
    *,
    export_legacy: bool = False,
    automatic: bool = False,
) -> dict:
    from runtime_v2.migration import RuntimeV2MigrationService

    # Keep chat start reservation blocked for the transaction. This closes the
    # race where a run could start after the active check but before rollback.
    with _chat_start_lock:
        if _is_session_stream_active(session_id) or _has_running_subagent_activity(session_id):
            raise RuntimeSyncBusyError(
                f"session {session_id} has an active run; stop it before migration/export"
            )
        service = RuntimeV2MigrationService(
            session_manager.repository.sessions_dir,
            path_resolver=session_manager._resolve_session_path,
        )

        def _load_legacy_model_messages() -> list[dict]:
            return session_manager._load_llm_history(session_id)

        def _load_legacy_context() -> str:
            loader = getattr(session_manager, "_load_key_context", None)
            if not callable(loader):
                return ""
            return key_context_body_for_system_prompt(loader(session_id))

        def _load_legacy_todo() -> dict:
            loader = getattr(session_manager, "get_todo_plan_snapshot", None)
            if not callable(loader):
                return {}
            value = loader(session_id)
            return dict(value) if isinstance(value, dict) else {}

        return service.sync_session(
            session_id,
            load_legacy_ui_events=lambda: session_manager.get_ui_events_for_display(session_id),
            save_legacy_ui_events=lambda events: session_manager._save_ui_events(session_id, events),
            load_legacy_model_messages=_load_legacy_model_messages,
            save_legacy_model_messages=lambda messages: session_manager._save_llm_history(session_id, messages),
            load_legacy_context=_load_legacy_context,
            load_legacy_todo=_load_legacy_todo,
            load_file_fingerprints=lambda: _runtime_sync_fingerprints(session_id),
            export_legacy=bool(export_legacy),
            conflict_policy="record" if automatic else "raise",
        )


def _sync_runtime_session(
    session_id: str,
    *,
    export_legacy: bool = False,
    automatic: bool = False,
) -> dict:
    kwargs = {"export_legacy": bool(export_legacy)}
    if automatic:
        kwargs["automatic"] = True
    return _run_history_op_locked(
        session_id,
        _sync_runtime_session_unlocked,
        session_id,
        **kwargs,
    )


@fastapi_app.post("/sessions/{session_id}/runtime/sync/enqueue")
async def enqueue_session_runtime_sync(session_id: str):
    result = _enqueue_runtime_sync(session_id, "manual", check_needed=False)
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(content=result, status_code=status_code)


@fastapi_app.post("/sessions/runtime/sync-all/enqueue")
async def enqueue_all_runtime_sync(limit: int = Query(0, ge=0, le=10000), check_needed: bool = Query(True)):
    rows = session_manager.list_sessions(include_archived=True)
    if limit and limit > 0:
        rows = rows[:limit]
    queued = 0
    skipped = 0
    results = []
    for row in rows:
        sid = str((row or {}).get("id") or "").strip()
        if not sid:
            continue
        result = _enqueue_runtime_sync(sid, "manual_all", check_needed=bool(check_needed))
        results.append(result)
        if result.get("queued"):
            queued += 1
        else:
            skipped += 1
    return JSONResponse(content={
        "ok": True,
        "session_count": len(results),
        "queued": queued,
        "skipped": skipped,
        "results": results[:200],
        "truncated_results": len(results) > 200,
    })


@fastapi_app.get("/sessions/runtime/sync/status")
async def get_runtime_sync_status():
    with _RUNTIME_SYNC_LOCK:
        queue = list(_RUNTIME_SYNC_QUEUE)
        statuses = {sid: dict(status) for sid, status in _RUNTIME_SYNC_STATUS.items()}
        worker_alive = bool(
            (_RUNTIME_SYNC_WORKER is not None and _RUNTIME_SYNC_WORKER.is_alive())
            or any(worker.is_alive() for worker in _RUNTIME_SYNC_EXTRA_WORKERS)
        )
        auto_migration = dict(_RUNTIME_AUTO_MIGRATION_STATUS)
    return JSONResponse(content={
        "ok": True,
        "worker_alive": worker_alive,
        "queue_length": len(queue),
        "queue": queue[:200],
        "statuses": statuses,
        "auto_migration": auto_migration,
    })


@fastapi_app.post("/sessions/runtime/sync/cancel")
async def cancel_runtime_sync_queue():
    with _RUNTIME_SYNC_LOCK:
        cleared = len(_RUNTIME_SYNC_QUEUE)
        _RUNTIME_SYNC_QUEUE.clear()
        for sid, status in list(_RUNTIME_SYNC_STATUS.items()):
            if status.get("state") == "queued":
                next_status = dict(status)
                next_status.update({"state": "cancelled", "queued": False})
                _RUNTIME_SYNC_STATUS[sid] = next_status
    return JSONResponse(content={"ok": True, "cleared": cleared})


@fastapi_app.post("/sessions/{session_id}/runtime/sync")
async def sync_session_runtime(
    session_id: str,
    export_legacy: bool = Query(False, description="explicitly export Runtime V2 projection back to legacy files"),
):
    import time as _time

    t0 = _time.perf_counter()
    try:
        result = await run_in_threadpool(_sync_runtime_session, session_id, export_legacy=bool(export_legacy))
    except RuntimeSyncBusyError as exc:
        return JSONResponse(
            content={"ok": False, "session_id": session_id, "busy": True, "error": str(exc)},
            status_code=409,
        )
    except Exception as exc:
        logger.warning("runtime sync failed for %s: %s", session_id, exc)
        return JSONResponse(content={"ok": False, "session_id": session_id, "error": str(exc)}, status_code=500)
    result["elapsed_ms"] = int((_time.perf_counter() - t0) * 1000)
    return JSONResponse(content=result)


def _sync_all_runtime_sessions(limit: int = 0, *, export_legacy: bool = False) -> dict:
    rows = session_manager.list_sessions(include_archived=True)
    if limit and limit > 0:
        rows = rows[:limit]
    results = []
    ok_count = 0
    fail_count = 0
    busy_count = 0
    for row in rows:
        sid = str((row or {}).get("id") or "").strip()
        if not sid:
            continue
        try:
            result = _sync_runtime_session(sid, export_legacy=bool(export_legacy))
            ok_count += 1
        except RuntimeSyncBusyError as exc:
            result = {"ok": False, "session_id": sid, "busy": True, "error": str(exc)}
            fail_count += 1
            busy_count += 1
        except Exception as exc:
            result = {"ok": False, "session_id": sid, "error": str(exc)}
            fail_count += 1
        results.append(result)
    return {
        "ok": fail_count == 0,
        "session_count": len(results),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "busy_count": busy_count,
        "results": results,
    }


@fastapi_app.post("/sessions/runtime/sync-all")
async def sync_all_runtime_sessions(
    limit: int = Query(0, ge=0, le=10000),
    export_legacy: bool = Query(False, description="explicitly export Runtime V2 projection back to legacy files"),
):
    import time as _time

    t0 = _time.perf_counter()
    result = await run_in_threadpool(_sync_all_runtime_sessions, int(limit or 0), export_legacy=bool(export_legacy))
    result["elapsed_ms"] = int((_time.perf_counter() - t0) * 1000)
    status_code = 409 if result.get("busy_count") else (200 if result.get("ok") else 500)
    return JSONResponse(content=result, status_code=status_code)


def _repair_runtime_v2_subagent_storage(
    *,
    apply: bool = False,
    child_session_id: str = "",
    limit: int = 0,
) -> dict:
    from runtime_v2 import RuntimeV2SubagentRepairService

    service = RuntimeV2SubagentRepairService(
        session_manager.repository.sessions_dir,
        path_resolver=session_manager._resolve_session_path,
    )
    index = session_manager._load_subagent_index()
    requested_child = str(child_session_id or "").strip()
    rows = [
        (child_id, parent_id)
        for child_id, parent_id in index.items()
        if not requested_child or child_id == requested_child
    ]
    if limit and limit > 0:
        rows = rows[: int(limit)]
    results: list[dict] = []
    repaired = 0
    split_brain = 0
    busy = 0
    failures = 0
    refused = 0
    committed_pending_archive = 0
    applied = 0
    for child_id, parent_id in rows:
        if apply and (
            _has_local_run_activity(child_id)
            or _has_local_run_activity(parent_id)
            or _has_running_subagent_activity(parent_id)
        ):
            busy += 1
            results.append({
                "ok": False,
                "parent_session_id": parent_id,
                "child_session_id": child_id,
                "action": "busy",
                "error": "session or subagent tree is running",
            })
            continue
        try:
            result = service.repair(
                parent_id,
                child_id,
                apply=bool(apply),
                archive_ghost=True,
            )
        except Exception as exc:
            failures += 1
            result = {
                "ok": False,
                "parent_session_id": parent_id,
                "child_session_id": child_id,
                "action": "failed",
                "error": str(exc),
            }
        split_brain += 1 if result.get("split_brain") else 0
        action = str(result.get("action") or "")
        applied += 1 if result.get("applied") else 0
        repaired += 1 if action == "repaired" else 0
        refused += 1 if action in {"refused", "refused_after_lock"} else 0
        committed_pending_archive += 1 if action == "committed_pending_archive" else 0
        results.append(result)
    return {
        "ok": (
            failures == 0
            and busy == 0
            and refused == 0
            and committed_pending_archive == 0
            and all(result.get("ok", False) for result in results)
        ),
        "apply": bool(apply),
        "checked": len(rows),
        "split_brain": split_brain,
        "repaired": repaired,
        "applied": applied,
        "busy": busy,
        "failures": failures,
        "refused": refused,
        "committed_pending_archive": committed_pending_archive,
        "results": results,
    }


@fastapi_app.post("/sessions/runtime-v2/subagent-storage/repair")
async def repair_runtime_v2_subagent_storage(
    apply: bool = Query(False, description="explicitly merge and archive top-level V2 child ghost logs"),
    child_session_id: str = Query("", description="optional single child session id"),
    limit: int = Query(0, ge=0, le=10000),
):
    import time as _time

    started = _time.perf_counter()
    result = await run_in_threadpool(
        _repair_runtime_v2_subagent_storage,
        apply=bool(apply),
        child_session_id=str(child_session_id or ""),
        limit=int(limit or 0),
    )
    result["elapsed_ms"] = int((_time.perf_counter() - started) * 1000)
    status_code = 200 if result.get("ok") else 409 if (result.get("busy") or result.get("refused")) else 500
    return JSONResponse(content=result, status_code=status_code)


@fastapi_app.post("/sessions/index/repair")
async def repair_sessions_index():
    import time as _time

    t0 = _time.perf_counter()
    await run_in_threadpool(session_manager.refresh_sessions_index_from_disk)
    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    return JSONResponse(content={
        "ok": True,
        "session_count": len(session_manager.index),
        "elapsed_ms": elapsed_ms,
    })


@fastapi_app.get("/sessions/{session_id}/user_turns")
async def get_session_user_turns(session_id: str):
    """列出会话内全部用户消息的 event_index 与预览（供右侧「历史记录」目录，与消息是否分页加载无关）。"""
    try:
        from runtime_v2 import runtime_v2_primary

        if runtime_v2_primary():
            from runtime_v2.ui_projection import RuntimeUiProjection

            projection = RuntimeUiProjection(
                session_manager.repository.sessions_dir,
                path_resolver=session_manager._resolve_session_path,
            )
            turns = await asyncio.to_thread(projection.read_user_turns_light, session_id)
            return JSONResponse(content=turns)
    except Exception as exc:
        logger.warning("Runtime V2 user turns failed for %s: %s", session_id, exc)
        return JSONResponse(content=[])
    payload = await asyncio.to_thread(session_manager.get_ui_user_turns_for_toc, session_id)
    return JSONResponse(content=payload)


@fastapi_app.get("/sessions/{session_id}/todo_plan")
async def get_session_todo_plan(session_id: str):
    """当前会话 Todo 计划快照（todo_plan.md），供左侧「当前计划」面板。"""
    def _build_todo_response() -> JSONResponse:
        try:
            from runtime_v2 import runtime_v2_primary

            if runtime_v2_primary():
                return JSONResponse(content=_runtime_v2_todo_plan_snapshot(session_id))
        except Exception as exc:
            logger.debug("Runtime V2 todo snapshot read failed for %s: %s", session_id, exc)
            return JSONResponse(content=_empty_todo_plan_snapshot("runtime_v2_snapshot_error"))
        return JSONResponse(content=session_manager.get_todo_plan_snapshot(session_id))

    return await asyncio.to_thread(_build_todo_response)


@fastapi_app.delete("/sessions/{session_id}/todo_plan")
async def clear_session_todo_plan(session_id: str):
    """用户手动清除当前会话的 Todo 计划。"""
    ok = session_manager.clear_todo_plan(session_id)
    return JSONResponse(content={"ok": ok})


@fastapi_app.get("/sessions/{session_id}/execution-metrics")
async def get_session_execution_metrics(session_id: str):
    sid = str(session_id or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)
    return JSONResponse({"ok": True, "data": execution_metrics.snapshot(sid)})


def _execution_metrics_session_names() -> Dict[str, str]:
    return {
        str(row.get("id") or ""): str(row.get("name") or row.get("id") or "")
        for row in list(session_manager.index)
        if isinstance(row, dict) and row.get("id")
    }


@fastapi_app.get("/api/execution-metrics/sessions")
async def get_execution_metrics_sessions():
    def _build_payload() -> bytes:
        global _execution_metrics_sessions_cached_at, _execution_metrics_sessions_cache
        now = time.monotonic()
        with _execution_metrics_payload_lock:
            if (
                _execution_metrics_sessions_cache
                and now - _execution_metrics_sessions_cached_at < _EXECUTION_METRICS_PAYLOAD_TTL_SEC
            ):
                return _execution_metrics_sessions_cache
            payload = json.dumps(
                {"ok": True, "data": execution_metrics.list_sessions(_execution_metrics_session_names())},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            _execution_metrics_sessions_cache = payload
            _execution_metrics_sessions_cached_at = time.monotonic()
            return payload

    payload = await asyncio.to_thread(_build_payload)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@fastapi_app.get("/api/execution-metrics")
async def get_all_execution_metrics(session_id: Optional[str] = None):
    def _build_payload() -> bytes:
        global _execution_metrics_payload_cached_at, _execution_metrics_payload_cache
        sid = str(session_id or "").strip()
        if sid:
            data = execution_metrics.snapshot(sid)
            if data.get("runs"):
                data["session_name"] = _execution_metrics_session_names().get(sid, sid)
                payload = {"ok": True, "data": {"sessions": [data]}}
            else:
                payload = {"ok": True, "data": {"sessions": []}}
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        now = time.monotonic()
        with _execution_metrics_payload_lock:
            if (
                _execution_metrics_payload_cache
                and now - _execution_metrics_payload_cached_at < _EXECUTION_METRICS_PAYLOAD_TTL_SEC
            ):
                return _execution_metrics_payload_cache
            payload = json.dumps(
                {"ok": True, "data": execution_metrics.snapshot_all(_execution_metrics_session_names())},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            _execution_metrics_payload_cache = payload
            _execution_metrics_payload_cached_at = time.monotonic()
            return payload

    payload = await asyncio.to_thread(_build_payload)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@fastapi_app.get("/sessions/{session_id}/context_tokens")
async def get_session_context_tokens(session_id: str):
    """
    按当前落盘 llm_history / key_context 现算整包输入 token 估算（与主循环一致）。
    在线程池执行，避免阻塞事件循环；CPU 重计算不挡其它轻量 API。
    """
    def _read_snapshot_tokens() -> Optional[Dict[str, Any]]:
        try:
            from runtime_v2 import runtime_v2_primary

            if runtime_v2_primary():
                tokens = _runtime_v2_context_snapshot(session_id).get("tokens")
                if (
                    isinstance(tokens, dict)
                    and tokens.get("estimated") is not None
                ):
                    out = dict(tokens)
                    out["ok"] = True
                    if tokens.get("stale"):
                        out["pending_recalculation"] = True
                        out["source"] = "runtime_v2_snapshot_stale"
                    else:
                        out["source"] = "runtime_v2_snapshot"
                    return out
        except Exception as exc:
            logger.warning("Runtime V2 context token snapshot read failed for %s: %s", session_id, exc)
            return {
                "ok": False,
                "error": "runtime_v2_projection_failed",
                "repair_required": True,
                "detail": str(exc),
            }
        return None

    token_mode = get_context_token_mode()
    snap = None if token_mode == "calculated" else await asyncio.to_thread(_read_snapshot_tokens)
    if snap is not None:
        snap["token_mode"] = token_mode
        if not snap.get("ok", True):
            return JSONResponse(content=snap, status_code=500)
        return JSONResponse(content=snap)
    tool_definitions = await build_combined_tool_definitions_for_session(session_id)
    out = await run_in_threadpool(
        compute_context_tokens_for_session,
        session_id,
        tool_definitions,
    )
    if not out.get("ok"):
        return JSONResponse(content=out, status_code=400)
    # Snapshot misses retain the configured accounting mode. In hybrid mode
    # compute_context_tokens_for_session reuses/calibrates the provider usage
    # baseline instead of switching the UI to an unrelated local scale.
    out["token_mode"] = token_mode
    return JSONResponse(content=out)


@fastapi_app.put("/sessions/{session_id}/name")
async def rename_session(session_id: str, name: str = Form(...)):
    normalized_name = str(name or "").strip()[:160]
    if not normalized_name:
        return JSONResponse(content={"status": "error", "error": "session name is required"}, status_code=400)
    session_manager.set_session_name(session_id, normalized_name)
    return JSONResponse(content={"status": "ok"})


def _remove_session_export_archive(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("failed to remove temporary session export: %s", path)


def _build_session_export_archive(session_id: str) -> tuple[Path, str]:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session id is required")
    session_path = Path(session_manager._resolve_session_path(sid)).resolve()
    sessions_root = Path(session_manager.repository.sessions_dir).resolve()
    try:
        session_path.relative_to(sessions_root)
    except ValueError as exc:
        raise PermissionError("session path is outside the sessions directory") from exc
    if not session_path.is_dir():
        raise FileNotFoundError(f"session directory not found: {sid}")

    archive_root = re.sub(r"[^A-Za-z0-9._-]+", "_", session_path.name).strip("._") or "session"
    download_name = f"session-{archive_root}.zip"
    fd, temp_name = tempfile.mkstemp(prefix="myagent-session-export-", suffix=".zip")
    os.close(fd)
    archive_path = Path(temp_name)
    try:
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as output:
            output.writestr(f"{archive_root}/", b"")
            for source in sorted(session_path.rglob("*"), key=lambda item: item.as_posix().lower()):
                if source.is_symlink():
                    continue
                resolved = source.resolve()
                try:
                    relative = resolved.relative_to(session_path)
                except ValueError as exc:
                    raise PermissionError("session export entry is outside the session directory") from exc
                archive_name = (Path(archive_root) / relative).as_posix()
                output.write(resolved, archive_name)
        return archive_path, download_name
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


@fastapi_app.get("/sessions/{session_id}/export")
async def export_session(session_id: str):
    try:
        archive_path, download_name = await run_in_threadpool(_build_session_export_archive, session_id)
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=400)
    except PermissionError:
        return JSONResponse(content={"status": "error", "error": "session export is not allowed"}, status_code=403)
    except FileNotFoundError:
        return JSONResponse(content={"status": "error", "error": "session not found"}, status_code=404)
    except Exception as exc:
        logger.exception("session export failed for %s: %s", session_id, exc)
        return JSONResponse(content={"status": "error", "error": "session export failed"}, status_code=500)
    return FileResponse(
        str(archive_path),
        media_type="application/zip",
        filename=download_name,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(_remove_session_export_archive, str(archive_path)),
    )


@fastapi_app.put("/sessions/{session_id}/archive")
async def archive_session(session_id: str, archived: bool = Form(...)):
    session_manager.set_session_archived(session_id, archived)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.put("/sessions/{session_id}/pin")
async def pin_session(session_id: str, pinned: bool = Form(...)):
    session_manager.set_session_pinned(session_id, pinned)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.put("/sessions/{session_id}/todo")
async def todo_session(session_id: str, todo: bool = Form(...)):
    session_manager.set_session_todo(session_id, todo)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/unread-result/clear")
async def clear_session_unread_result(session_id: str):
    session_manager.clear_session_unread_result(session_id)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/truncate")
async def truncate_session_events(
    session_id: str,
    before_index: int = Query(..., description="保留事件区间 [0, before_index)"),
    before_seq: Optional[int] = Query(None, ge=1, description="Runtime V2 visible event seq to truncate before"),
    backup: bool = Query(False, description="whether to create truncate_backups before truncating"),
):
    """
    仅保留 ui_events[0:before_index]（下标 before_index 及之后丢弃），
    并重建主对话/上下文。使用 query 传参，避免 form 解析失败。
    """
    try:
        if int(before_index) < 0:
            return JSONResponse(
                content={"ok": False, "error": "invalid before_index"},
                status_code=400,
            )
        # A history mutation must never hide the assistant turn that owns an
        # actionable ask_user request while leaving the durable request
        # pending forever. UI callers cancel the question first; this server
        # guard also protects alternate clients and stale browser builds.
        pending_human_count = await asyncio.to_thread(
            _session_pending_human_question_count,
            session_id,
        )
        if pending_human_count > 0:
            return JSONResponse(
                content={
                    "ok": False,
                    "error": "pending human interaction must be cancelled before history mutation",
                },
                status_code=409,
            )
        ok = await run_in_threadpool(
            _run_history_op_locked,
            session_id,
            session_manager.truncate_session_at_event_index,
            session_id,
            int(before_index),
            truncate_before_seq=before_seq,
            create_backup=bool(backup),
        )
    except (TypeError, ValueError):
        return JSONResponse(content={"ok": False, "error": "invalid before_index"}, status_code=400)
    if not ok:
        return JSONResponse(
            content={"ok": False, "error": "truncation failed"},
            status_code=400,
        )
    try:
        from workflow_extensions import session_workflows

        await asyncio.to_thread(session_workflows.call, "history_truncated", session_id)
    except Exception:
        logger.warning("history truncation plugin callback failed for %s", session_id, exc_info=True)
    # The user just deleted the tail of the history (typically the leftover of
    # an interrupted run). Acknowledge stale failed/interrupted runs so the
    # runtime indicator stops alerting on them.
    try:
        from runtime_observability import mark_runs_resolved

        await asyncio.to_thread(mark_runs_resolved, session_id)
    except Exception:
        logger.debug("runtime status resolve failed for session %s", session_id, exc_info=True)
    return JSONResponse(content={"ok": True})


@fastapi_app.post("/sessions/{session_id}/branch")
async def branch_session_events(
    session_id: str,
    before_index: int = Query(
        ...,
        description="新会话保留 ui_events[0:before_index]（与 truncate 语义一致）",
    ),
    after_seq: Optional[int] = Query(None, ge=1),
):
    """
    在当前会话的 event 下标处复制出分支会话，原会话不变。
    最终答案处分支时，前端应传 final 事件的 eventIndex + 1。
    """
    try:
        result = await run_in_threadpool(
            _run_history_op_locked,
            session_id,
            session_manager.branch_session_at_event_index,
            session_id,
            int(before_index),
            branch_after_seq=after_seq,
        )
    except (TypeError, ValueError):
        return JSONResponse(content={"ok": False, "error": "invalid before_index"}, status_code=400)
    if not result:
        return JSONResponse(
            content={"ok": False, "error": "branch failed"},
            status_code=400,
        )
    if result.get("ok") is False:
        return JSONResponse(
            content=result,
            status_code=400,
        )
    try:
        from workflow_extensions import session_workflows

        await asyncio.to_thread(
            session_workflows.call,
            "session_branched",
            session_id,
            str(result.get("session_id") or ""),
        )
    except Exception:
        logger.warning("branch plugin callback failed for %s", session_id, exc_info=True)
    return JSONResponse(content={"ok": True, **result})


@fastapi_app.post("/sessions/{session_id}/append_ui_events")
async def append_ui_events_tail(session_id: str, request: Request):
    """
    将一段已截断的 ui_events「尾段」接回当前会话（用于前端「改写」后的撤销）。
    body: { "events": [ ... ] }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"ok": False, "error": "invalid json"}, status_code=400)
    tail = body.get("events")
    if not isinstance(tail, list):
        return JSONResponse(content={"ok": False, "error": "events must be array"}, status_code=400)
    try:
        ok = await run_in_threadpool(
            _run_history_op_locked,
            session_id,
            session_manager.append_ui_events_tail,
            session_id,
            tail,
        )
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)
    if not ok:
        return JSONResponse(content={"ok": False, "error": "truncation failed"}, status_code=400)
    return JSONResponse(content={"ok": True})


# === Setup wizard (build_exe) ===
from pathlib import Path as _Path
from fastapi import Request as _Request
from fastapi.responses import HTMLResponse as _HTMLResponse
_TEMPLATES_DIR = _Path(__file__).resolve().parent / "templates"
_CONFIG_PATH = _TEMPLATES_DIR / "first_time_config.html"
_LEGACY_CONFIG_PATH = _TEMPLATES_DIR / "frist_time_config.html"


def _load_config_wizard_html() -> str:
    for path in (_CONFIG_PATH, _LEGACY_CONFIG_PATH):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    # 极简兜底（完整 UI：templates/first_time_config.html）
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>首次配置</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:2rem auto;padding:1rem;">
<h1>General Agent · 首次配置</h1>
<p>缺少 <code>templates/first_time_config.html</code>，使用简易表单。</p>
<form id="f"><label>API Key<input id="k" type="password" style="width:100%;margin:.5rem 0"></label>
<label>API Base URL<input id="u" type="text" placeholder="https://api.deepseek.com" style="width:100%;margin:.5rem 0"></label>
<label>模型 ID<input id="m" type="text" style="width:100%;margin:.5rem 0"></label>
<button type="submit">保存</button></form>
<pre id="e" style="color:red"></pre>
<script>
document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();document.getElementById('e').textContent='';
const r=await fetch('/api/save_config',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({api_key:document.getElementById('k').value,llm_base_url:document.getElementById('u').value,model_name:document.getElementById('m').value})});
const j=await r.json();if(j.ok)location.href='/?'+Date.now();else document.getElementById('e').textContent=j.error||'failed';};
</script></body></html>"""


_DOTENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_dotenv_key(raw: str) -> str:
    """去掉 UTF-8 BOM 等不可见前缀；BOM 在 .env 第一行最常见，会导致 key 正则误判缺失。"""
    return (raw or "").lstrip("\ufeff").strip()


def _parse_dotenv_rhs(raw_val: str) -> str:
    """还原 .env 行右侧值为字符串（支持外层双引号与 \\\" \\\\ 转义）。"""
    v = raw_val.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        inner = v[1:-1]
        out: list[str] = []
        i = 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    for idx, ch in enumerate(v):
        if ch == "#" and (idx == 0 or v[idx - 1].isspace()):
            return v[:idx].rstrip()
    return v


def _format_dotenv_value(val: str) -> str:
    """写入 .env 时：含空格、反斜杠、制表符、#、引号时加双引号并转义（避免 Windows 路径与 dotenv 转义歧义）。"""
    if val == "":
        return ""
    if "\n" in val or "\r" in val:
        raise ValueError("环境变量值不能包含换行")
    needs_quote = any(ch in val for ch in (' ', "\t", '"', "'", "#")) or "\\" in val
    if not needs_quote:
        return val
    inner = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{inner}"'


def _work_dir_restart_required(old_value: str, new_value: str) -> bool:
    old_raw = (old_value or "").strip()
    new_raw = (new_value or "").strip() or "./workspace"
    if old_raw == new_raw:
        return False
    try:
        old_path = _resolve_project_env_path(old_raw) if old_raw else WORK_DIR.resolve()
        new_path = _resolve_project_env_path(new_raw)
        return old_path != new_path
    except Exception:
        return old_raw != new_raw


def _resolve_project_env_path(raw: str) -> Path:
    path = Path((raw or "").strip()).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


_MODEL_ENV_KEYS = frozenset({
    "EXECUTOR_LLM",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "EXECUTOR_LLM_TYPE",
    "CONTEXT_WINDOW",
    "MAX_OUTPUT_TOKENS",
    "LLM_THINKING_MODE",
    "LLM_REASONING_EFFORT",
    "LLM_EXTRA_BODY_JSON",
    "EXECUTOR_TEMPERATURE",
    "LOCAL_LLM_HOST",
    "LOCAL_LLM",
})


def _has_complete_model_profile_for_main_ui() -> bool:
    try:
        profiles = model_profiles.sorted_profiles(PROJECT_ROOT)
    except Exception:
        return False
    return any(model_profiles.is_usable_profile(profile) for profile in profiles)


def _dotenv_last_assignments(path: Path) -> dict[str, str]:
    """解析 .env 中非注释的 KEY=value；重复 key 时后者覆盖。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = _normalize_dotenv_key(key)
        if not _DOTENV_KEY_RE.match(key):
            continue
        out[key] = _parse_dotenv_rhs(val)
    return out


def _is_configured():
    """The wizard gate depends only on the presence of a usable model profile."""
    return _has_complete_model_profile_for_main_ui()


@fastapi_app.get("/setup", response_class=_HTMLResponse)
async def setup_page():
    # 每次都从磁盘读，替换 templates/first_time_config.html 后立即生效；避免 stale 缓存
    body = _load_config_wizard_html()
    body = _html_with_path_picker_script(body)
    return _HTMLResponse(
        content=body,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


_ENV_ADVANCED_PATH = _Path(__file__).resolve().parent / "templates" / "advance_config.html"
_MCP_CONFIG_HTML_PATH = _Path(__file__).resolve().parent / "templates" / "mcp_config.html"
_EXTENSIONS_CONFIG_HTML_PATH = _Path(__file__).resolve().parent / "templates" / "extensions_config.html"


def _load_mcp_config_html() -> str:
    if _MCP_CONFIG_HTML_PATH.is_file():
        return _read_text_cached(_MCP_CONFIG_HTML_PATH, "")
    return "<!DOCTYPE html><html><body><p>缺少 templates/mcp_config.html</p><a href='/'>返回</a></body></html>"


def _load_extensions_config_html() -> str:
    if _EXTENSIONS_CONFIG_HTML_PATH.is_file():
        return _read_text_cached(_EXTENSIONS_CONFIG_HTML_PATH, "")
    return "<!DOCTYPE html><html><body><p>缺少 templates/extensions_config.html</p><a href='/'>返回</a></body></html>"


_ENV_GROUP_ORDER: list[tuple[str, str, list[str]]] = [
    (
        "search",
        "联网搜索",
        [
            "WEB_SEARCH_PROVIDER",
            "TAVILY_API_KEY",
            "BRAVE_API_KEY",
            "SEARXNG_BASE_URL",
            "JINA_API_KEY",
            "WEB_SEARCH_MAX_RESULTS",
        ],
    ),
    (
        "http",
        "HTTP / 代理 / 下载",
        [
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "WEB_DOWNLOAD_MAX_BYTES",
            "OPENAI_HTTP_TIMEOUT",
            "OPENAI_MAX_RETRIES",
            "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC",
            "OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES",
            "OPENAI_TOTAL_REQUEST_BUDGET",
            "OPENAI_TOTAL_DEADLINE_SEC",
            "OPENAI_MAX_INFLIGHT_REQUESTS",
            "NETWORK_RECONNECT_MAX_ATTEMPTS",
            "LOCAL_NETWORK_POLL_SECONDS",
            "OPENAI_RETRY_BASE_SEC",
        ],
    ),
    (
        "paths",
        "目录与运行环境",
        [
            "WORK_DIR",
            "SKILLS_DIR",
            "PLUGINS_DIR",
            "PLUGINS_DIRS",
            "PLUGINS_STATE_PATH",
            "HOOKS_PATH",
            "LOG_DIR",
            "NODE_HOME",
            "NVM_SYMLINK",
            "RUN_SHELL_USE_BASH",
            "RUN_CLI_BASH_LOGIN",
            "RUN_SHELL_BASH",
        ],
    ),
    (
        "agent",
        "Agent 行为",
        [
            "MAX_REACT_ITER",
            "VERBOSE_LOGGING",
            "TODO_MAX_ITEMS",
            "MAX_PARALLEL_TOOLS",
            "CPU_PRESSURE_ENABLED",
            "CPU_PRESSURE_BUSY_PERCENT",
            "CPU_PRESSURE_SEVERE_PERCENT",
            "CPU_PRESSURE_RECOVERY_PERCENT",
            "CPU_PRESSURE_SAMPLE_SECONDS",
            "CPU_PRESSURE_ENTER_SAMPLES",
            "CPU_PRESSURE_RECOVERY_SECONDS",
            "CPU_PRESSURE_TOOL_CONCURRENCY",
            "SECURITY_ENABLED",
            "EGRESS_HELPER_ENABLED",
            "EXTENSION_REGISTRATION_APPROVAL_ENABLED",
            "ASK_USER_ENABLED",
            "HOOKS_ENABLED",
            "PLUGINS_ENABLED",
        ],
    ),
    (
        "context",
        "上下文压缩与回顾",
        [
            "CONTEXT_TOKEN_MODE",
            "CONTEXT_TOKEN_ACCOUNTING_MODE",
            "CONTEXT_KEEP_RECENT_TURNS",
            "CONTEXT_MICRO_WORK_ROUNDS",
            "CONTEXT_COMPRESS_MAX_ROUNDS",
            "CONTEXT_COMPRESS_ROUND3_MAX_REACT",
            "CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES",
            "CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO",
            "CONTEXT_COMPRESS_TARGET_RATIO",
        ],
    ),
    (
        "repeat",
        "重复输出检测",
        [
            "REPEAT_DETECTION_THRESHOLD_SUMMARY",
            "REPEAT_DETECTION_THRESHOLD_ERROR",
        ],
    ),
    (
        "truncate",
        "日志与截断",
        [
            "LOG_TRUNCATE_KEEP_CHARS",
            "TOOL_RESULT_TRUNCATE_KEEP_CHARS",
            "GREP_MAX_MATCH_LINES",
            "GREP_USE_RIPGREP",
            "GREP_RIPGREP_PATH",
            "GREP_TIMEOUT_SEC",
            "GLOB_MAX_MATCHES",
            "GLOB_USE_WINDOWS_INDEX",
            "LS_MAX_ENTRIES",
            "LS_INCLUDE_LINE_COUNTS",
            "LS_LINE_COUNT_MAX_BYTES",
            "READ_FILE_RANGE_MAX_BYTES",
            "MICRO_SHRINK_REASONING_CHARS",
            "MICRO_SHRINK_ASSISTANT_CHARS",
            "MICRO_SHRINK_TOOL_CHARS",
            "MICRO_SHRINK_FAT_TOOL_FLOOR",
        ],
    ),
]

_ENV_KEY_GROUP: dict[str, str] = {}
_ENV_KEY_ORDER_IN_GROUP: dict[str, int] = {}
for _gid, _title, _keys in _ENV_GROUP_ORDER:
    for _i, _k in enumerate(_keys):
        _ENV_KEY_GROUP[_k] = _gid
        _ENV_KEY_ORDER_IN_GROUP[_k] = _i

_ENV_HINTS: dict[str, str] = {
    "EGRESS_HELPER_ENABLED": "1（默认）启用系统出站助手；设为 0 时完全跳过助手，仅保留命令识别和审批。修改后立即影响新命令。",
    "SECURITY_ENABLED": "1（默认）启用请求批准 / 替我审批 / 完全访问三档权限，并恢复此前保存的全局权限模式；设为 0 时强制使用完全访问并隐藏前端权限选择。保存后立即生效，页面刷新后更新界面。",
    "EXTENSION_REGISTRATION_APPROVAL_ENABLED": "0（默认）关闭统一扩展注册审批：MCP 与可执行插件工具、Hook、命令可直接注册；1/true/yes/on 启用首次注册或内容/配置摘要变化后的人工确认。保存后立即刷新扩展。",
    "ASK_USER_ENABLED": "1（默认）允许主 Agent 创建 ask_user 问题；0/false/no/off 禁止。已有待回答问题仍可处理，工具审批不受影响。保存后立即生效。",
    "HOOKS_ENABLED": "1（默认）启用生命周期 Hook；0/false/no/off 会跳过项目与插件 Hook。按次读取，保存后立即生效。",
    "PLUGINS_ENABLED": "1（默认）启用插件发现、组件合并与 Worker Runtime；0/false/no/off 会移除插件 Tool、Hook、Command、MCP、Skill、Agent 与 Prompt。",
    "HOOKS_PATH": "可选 hooks.json 路径；留空时使用 WORK_DIR/hooks.json。",
    "PLUGINS_DIR": "单个插件发现目录；留空时使用项目 plugins 与用户数据根目录下的 plugins（Windows: %LOCALAPPDATA%\\SugarAgent\\plugins；POSIX: ~/.local/state/sugaragent/plugins）。",
    "PLUGINS_DIRS": "多个插件发现目录，使用系统 PATH 分隔符（Windows 为分号）；优先于 PLUGINS_DIR。",
    "PLUGINS_STATE_PATH": "插件启用/禁用状态 JSON；采用原子替换写入。",
    "CONTEXT_TOKEN_MODE": "上下文 token 统计模式：hybrid=API usage + 本地计算混合；calculated=只使用本地计算。",
    "CONTEXT_TOKEN_ACCOUNTING_MODE": "同 CONTEXT_TOKEN_MODE；用于配置上下文 token 统计模式。",
    "WEB_SEARCH_PROVIDER": "网页搜索提供者：duckduckgo、tavily、brave、searxng、jina 等。",
    "TAVILY_API_KEY": "Tavily API Key（在 app.tavily.com 获取）。",
    "BRAVE_API_KEY": "Brave Search API Key。",
    "SEARXNG_BASE_URL": "自建 SearXNG 实例根 URL。",
    "JINA_API_KEY": "Jina 搜索 API Key。",
    "WEB_SEARCH_MAX_RESULTS": "单次搜索返回结果条数上限。",
    "HTTPS_PROXY": "HTTPS 代理，如 http://127.0.0.1:7890（可选）。",
    "HTTP_PROXY": "HTTP 代理（可选）。",
    "WEB_DOWNLOAD_MAX_BYTES": "web_download 单次下载字节上限。",
    "TOOL_UI_APPROVAL": "中央权限策略需要用户审批时在浏览器显示确认；该安全路径不可由环境变量关闭。",
    "TOOL_UI_APPROVAL_WAIT_SEC": "可选：留空或 0 表示工具审批不限时等待用户确认；设置正整数（秒）则超时视为拒绝。",
    "OPENAI_HTTP_TIMEOUT": "兼容 API 请求超时（秒）。",
    "OPENAI_MAX_RETRIES": "显式可重试错误路径允许的请求尝试数（含首次），默认 4；全部计入统一请求总预算。",
    "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC": "所有 Chat Completions 均在传输层等待首个有效 token；达到该秒数后追加一路并行 API 请求，首个有效 token 的连接胜出。非流式调用仅在上层缓冲赢家的完整结果；设为 0 关闭。",
    "OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES": "单次逻辑模型调用因慢响应最多追加的并行 API 请求数，默认 2；设为 0 关闭并行重试。",
    "OPENAI_TOTAL_REQUEST_BUDGET": "单次逻辑模型调用中 hedge、错误重试和模型回退共享的物理 API 请求总预算，默认 6。",
    "OPENAI_TOTAL_DEADLINE_SEC": "单次逻辑模型调用包含全部并行与串行尝试的总截止时间（秒），默认 600。",
    "OPENAI_MAX_INFLIGHT_REQUESTS": "单次逻辑模型调用同时存在的 API 请求连接上限，默认 3。",
    "NETWORK_RECONNECT_MAX_ATTEMPTS": "模型网络错误的快速重连次数；本机离线时等待网络恢复，其他错误达到上限后进入常规模型回退。",
    "LOCAL_NETWORK_POLL_SECONDS": "本机断网后 Agent 沉睡期间的网络状态检测间隔秒数，默认 5，最小 1。",
    "OPENAI_RETRY_BASE_SEC": "重试基础退避时间（秒）。",
    "WORK_DIR": "工作区根目录（文件工具沙箱）。",
    "SKILLS_DIR": "技能包目录（默认可于 WORK_DIR 下）。",
    "LOG_DIR": "日志输出目录。",
    "NODE_HOME": "可选：prepend 到子进程 PATH 的 Node 安装目录。",
    "NVM_SYMLINK": "nvm-windows 当前 node 的 symlink 目录（可选）。",
    "RUN_SHELL_USE_BASH": "Windows 下是否优先用 Git Bash 执行 shell（0=跳过 Git Bash，使用 PowerShell）。",
    "RUN_CLI_BASH_LOGIN": "是否使用 bash -l 登录 shell；默认 0 以减少每次命令的启动开销。",
    "RUN_SHELL_BASH": "bash.exe 路径（可选）。",
    "MAX_REACT_ITER": "ReAct 主循环最大迭代轮数。",
    "VERBOSE_LOGGING": "是否输出更详细的运行日志。",
    "TODO_MAX_ITEMS": "Todo 列表展示/跟踪条数上限。",
    "MAX_PARALLEL_TOOLS": "允许的并行工具调用数量上限。",
    "CPU_PRESSURE_ENABLED": "启用正常/繁忙/严重三级系统压力控制器；繁忙保持流式，严重才整段输出。修改后需重启 Agent。",
    "CPU_PRESSURE_BUSY_PERCENT": "CPU 滑动均值进入繁忙状态的阈值，默认 60%；不会关闭流式输出。",
    "CPU_PRESSURE_SEVERE_PERCENT": "CPU 滑动均值进入严重状态的阈值，默认 90%；需连续多轮确认。",
    "CPU_PRESSURE_RECOVERY_PERCENT": "恢复到正常状态所需的 CPU 上限，默认 65%。",
    "CPU_PRESSURE_SAMPLE_SECONDS": "CPU/内存/进程压力采样周期，默认 10 秒。",
    "CPU_PRESSURE_ENTER_SAMPLES": "升档前连续满足条件的采样次数，默认 12；即持续至少约 2 分钟。",
    "CPU_PRESSURE_RECOVERY_SECONDS": "降档前连续稳定时间，默认 120 秒；用于避免恢复后立即再次升档。",
    "CPU_PRESSURE_TOOL_CONCURRENCY": "严重压力下本地资源型只读工具的并发上限，默认 2；网络工具不受此限制。",
    "CONTEXT_KEEP_RECENT_TURNS": "第 1 轮摘要尾窗完整保留的 user 轮数；Phase E 微压范围为其 3 倍 user 轮之前的区间。",
    "CONTEXT_MICRO_WORK_ROUNDS": "每轮摘要重组时，紧挨 tail 边界之前的 legacy 块数（块=user 或 assistant+tools），做微压；随 prefix/tail 切点滑动。",
    "CONTEXT_COMPRESS_MAX_ROUNDS": "摘要 LLM 最多调用轮数（第 2/3 轮尾窗逐轮放宽）。",
    "CONTEXT_COMPRESS_ROUND3_MAX_REACT": "第 3 轮摘要：除最后 1 条 user 外，完整保留的 ReAct assistant 步数上限。",
    "CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES": "整包仍超 CONTEXT_WINDOW 时应急截尾重试次数。",
    "CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO": "压缩执行器请求相对 CONTEXT_WINDOW 的比例上限（约 ≤1.1），用于 token 预算裁剪。",
    "CONTEXT_COMPRESS_TARGET_RATIO": "压缩后 work 相对 CONTEXT_WINDOW 的目标比例（默认 0.6）。",
    "REPEAT_DETECTION_THRESHOLD_SUMMARY": "重复多少次输出后插入系统提示。",
    "REPEAT_DETECTION_THRESHOLD_ERROR": "重复多少次后中止并报错。",
    "LOG_TRUNCATE_KEEP_CHARS": "日志/终端单行展示时每端保留字符数。",
    "TOOL_RESULT_TRUNCATE_KEEP_CHARS": "单条工具结果触发落盘的字符阈值；超过该值时完整结果落盘，UI/LLM 保留头部一半字符并在截断结果首尾提示路径。",
    "LLM_CONTEXT_TRUNCATE_KEEP_CHARS": "旧变量名，仍兼容读取；建议改用 TOOL_RESULT_TRUNCATE_KEEP_CHARS。",
    "GREP_MAX_MATCH_LINES": "grep 最多返回的匹配行数（跨文件累计）。",
    "GREP_USE_RIPGREP": "优先使用 ripgrep（rg）加速搜索；不可用或正则不兼容时自动回退 Python。",
    "GREP_RIPGREP_PATH": "可选：rg/rg.exe 的完整路径；默认优先查找仓库内置 Python 和 bundled tools。",
    "GREP_TIMEOUT_SEC": "ripgrep 单次搜索超时秒数。",
    "GLOB_MAX_MATCHES": "glob 最多返回的路径条数。",
    "GLOB_USE_WINDOWS_INDEX": "Windows 文件名索引加速，默认 1；设为 0 关闭。无结果或不可用时回退文件系统。",
    "LS_MAX_ENTRIES": "ls/list_dir 单层目录最多列出的条目数。",
    "LS_INCLUDE_LINE_COUNTS": "是否读取可识别的文本/源码文件统计行数；默认 1，设为 0 可切换为轻量目录列表。",
    "LS_LINE_COUNT_MAX_BYTES": "ls 统计单个文本文件行数的大小上限，默认 5242880（5 MiB）；超过后跳过。",
    "READ_FILE_RANGE_MAX_BYTES": "使用 start_line/line_count 按行读取时的文件大小安全上限。",
    "MICRO_SHRINK_REASONING_CHARS": "微压：推理内容字符上限。",
    "MICRO_SHRINK_ASSISTANT_CHARS": "微压：助手正文字符上限。",
    "MICRO_SHRINK_TOOL_CHARS": "微压：工具返回字符上限。",
    "MICRO_SHRINK_FAT_TOOL_FLOOR": "大工具输出的字符下限保护与 MICRO_SHRINK_TOOL_CHARS 联用。",
}


_NON_SENSITIVE = frozenset({
    "CONTEXT_TOKEN_MODE",
    "CONTEXT_TOKEN_ACCOUNTING_MODE",
    "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC",
    "OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES",
})

_ENV_PATH_KIND_FILE = frozenset(
    {
        "RUN_SHELL_BASH",
        "MCP_SERVERS_JSON",
        "GREP_RIPGREP_PATH",
    }
)
_ENV_PATH_KIND_DIR = frozenset(
    {
        "WORK_DIR",
        "SKILLS_DIR",
        "LOG_DIR",
        "NODE_HOME",
        "NVM_SYMLINK",
    }
)


def _env_key_path_kind(key: str) -> Optional[str]:
    u = key.upper()
    if u in _ENV_PATH_KIND_FILE:
        return "file"
    if u in _ENV_PATH_KIND_DIR:
        return "directory"
    if u.endswith("_DIR") or u.endswith("_DIRECTORY"):
        return "directory"
    if u.endswith("_PATH") or u.endswith("_FILE"):
        return "file"
    return None


def _env_key_sensitive(name: str) -> bool:
    if name in _NON_SENSITIVE:
        return False
    u = name.upper()
    return any(x in u for x in ("API_KEY", "SECRET", "PASSWORD", "TOKEN", "PRIVATE"))


def _parse_env_entries(text: str) -> list[dict]:
    lines = text.splitlines()
    pending_hints: list[str] = []
    entries: list[dict] = []
    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            pending_hints.append(s[1:].strip())
            continue
        if "=" not in s:
            pending_hints.clear()
            continue
        key, _, val = s.partition("=")
        key = _normalize_dotenv_key(key)
        if not key_re.match(key):
            pending_hints.clear()
            continue
        merged_hint = "\n".join(pending_hints) if pending_hints else ""
        pending_hints.clear()
        if not merged_hint.strip():
            merged_hint = _ENV_HINTS.get(key, "")
        elif key in _ENV_HINTS:
            dk = _ENV_HINTS[key]
            if dk and dk not in merged_hint:
                merged_hint = f"{merged_hint}\n{dk}".strip()
        parsed_val = _parse_dotenv_rhs(val)
        sensitive = _env_key_sensitive(key)
        path_kind = _env_key_path_kind(key)
        entries.append(
            {
                "key": key,
                "value": parsed_val,
                "has_value": bool(parsed_val),
                "hint": merged_hint,
                "sensitive": sensitive,
                "path_kind": path_kind,
            }
        )
    return entries


def _apply_env_updates(text: str, updates: dict[str, str]) -> str:
    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    lines = text.split("\n") if text else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line)
            continue
        key, _, _ = s.partition("=")
        key = _normalize_dotenv_key(key)
        if not key_re.match(key):
            out.append(line)
            continue
        if key in updates:
            if key in seen:
                continue
            out.append(f"{key}={_format_dotenv_value(updates[key])}")
            seen.add(key)
        else:
            out.append(line)
    for key in sorted(updates):
        if key in seen:
            continue
        if not key_re.match(key):
            continue
        out.append(f"{key}={_format_dotenv_value(updates[key])}")
    result = "\n".join(out)
    if text.endswith("\n"):
        result = result.rstrip("\n") + "\n"
    return result


def _load_env_advanced_html() -> str:
    if _ENV_ADVANCED_PATH.is_file():
        return _read_text_cached(_ENV_ADVANCED_PATH, "")
    return "<!DOCTYPE html><html><body><p>缺少 templates/advance_config.html</p><a href='/'>返回</a></body></html>"


@fastapi_app.get("/setup/env", response_class=_HTMLResponse)
async def env_advanced_page():
    body = _html_with_path_picker_script(_load_env_advanced_html())
    return _HTMLResponse(
        content=body,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@fastapi_app.get("/setup/mcp", response_class=_HTMLResponse)
async def mcp_config_page():
    body = _html_with_path_picker_script(_load_mcp_config_html())
    return _HTMLResponse(
        content=body,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@fastapi_app.get("/setup/extensions", response_class=_HTMLResponse)
async def extensions_config_page():
    return _HTMLResponse(
        content=_load_extensions_config_html(),
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@fastapi_app.get("/api/extensions")
async def get_extensions_snapshot():
    try:
        from agent_extensions import extensions_snapshot

        data = await asyncio.to_thread(extensions_snapshot)
        return JSONResponse(content=data)
    except Exception as exc:
        logger.exception("Extension snapshot failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/extensions/session-ui")
async def get_plugin_session_ui(request: Request):
    """Batch-project only manifest-whitelisted plugin session state fields."""

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    raw_ids = body.get("session_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list) or len(raw_ids) > 200:
        return JSONResponse(
            {"ok": False, "error": "session_ids must be a list of at most 200 items"},
            status_code=400,
        )
    requested = []
    for value in raw_ids:
        session_id = str(value or "").strip()
        if not session_id or len(session_id) > 256 or session_id in requested:
            continue
        requested.append(session_id)
    get_summary = getattr(session_manager, "get_session_summary", None)
    if callable(get_summary):
        session_ids = [
            session_id for session_id in requested if get_summary(session_id) is not None
        ]
    else:
        known = {
            str(item.get("id") or "")
            for item in await asyncio.to_thread(
                session_manager.list_sessions,
                include_archived=True,
            )
            if isinstance(item, dict) and item.get("id")
        }
        session_ids = [session_id for session_id in requested if session_id in known]
    try:
        from agent_extensions import plugin_session_ui_snapshot

        data = await asyncio.to_thread(
            plugin_session_ui_snapshot,
            session_ids,
            snapshot_reader=_runtime_v2_extensions_snapshot,
        )
        return JSONResponse(content=data)
    except Exception as exc:
        logger.exception("Plugin session UI projection failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/extensions/session-run-grant")
async def create_plugin_session_run_grant(request: Request):
    """Authorize one plugin to start one explicit set of sessions once."""

    try:
        from plugin_web_gateway import validate_plugin_write_origin

        validate_plugin_write_origin(
            request.method,
            origin=str(request.headers.get("origin") or ""),
            scheme=request.url.scheme,
            host=str(request.headers.get("host") or request.url.netloc),
            fetch_site=str(request.headers.get("sec-fetch-site") or ""),
            require_origin=True,
        )
    except Exception as exc:
        return _plugin_web_error_response(exc)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    plugin_id = str(body.get("plugin_id") or "").strip() if isinstance(body, dict) else ""
    session_ids = body.get("session_ids") if isinstance(body, dict) else None
    if not plugin_id or not isinstance(session_ids, list):
        return JSONResponse(
            {"ok": False, "error": "plugin_id and session_ids are required"},
            status_code=400,
        )
    try:
        from agent_extensions import load_plugins
        from plugin_host_services import issue_session_run_grant

        plugin = next(
            (item for item in load_plugins(force=True).plugins if item.plugin_id == plugin_id),
            None,
        )
        if plugin is None:
            return JSONResponse({"ok": False, "error": "plugin not found"}, status_code=404)
        grant = await asyncio.to_thread(issue_session_run_grant, plugin, session_ids)
        return JSONResponse({"ok": True, **grant})
    except Exception as exc:
        return _plugin_web_error_response(exc)


@fastapi_app.post("/api/extensions/session-action")
async def invoke_plugin_session_action(request: Request):
    """Execute a fixed, manifest-declared state action for one session panel."""

    try:
        from plugin_web_gateway import validate_plugin_write_origin

        validate_plugin_write_origin(
            request.method,
            origin=str(request.headers.get("origin") or ""),
            scheme=request.url.scheme,
            host=str(request.headers.get("host") or request.url.netloc),
            fetch_site=str(request.headers.get("sec-fetch-site") or ""),
        )
    except Exception as exc:
        return _plugin_web_error_response(exc)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be an object"}, status_code=400)
    session_id = str(body.get("session_id") or "").strip()
    plugin_id = str(body.get("plugin_id") or "").strip()
    action_id = str(body.get("action_id") or "").strip()
    if not session_id or not plugin_id or not action_id:
        return JSONResponse(
            {"ok": False, "error": "session_id, plugin_id and action_id are required"},
            status_code=400,
        )
    known = {
        str(item.get("id") or "")
        for item in await asyncio.to_thread(
            session_manager.list_sessions,
            include_archived=True,
        )
        if isinstance(item, dict) and item.get("id")
    }
    if session_id not in known:
        return JSONResponse({"ok": False, "error": "unknown session"}, status_code=404)
    try:
        from agent_extensions import (
            invoke_plugin_tool,
            plugin_session_action,
            plugin_session_ui_snapshot,
        )
        from plugins.runtime import runtime_tool_name
        from plugins.ui import plugin_session_action_arguments
        from runtime_v2 import SessionExtensionStateStore

        definition = await asyncio.to_thread(
            plugin_session_action,
            plugin_id,
            action_id,
        )
        if not isinstance(definition, dict):
            return JSONResponse({"ok": False, "error": "unknown session action"}, status_code=404)
        operation = str(definition.get("operation") or "")
        revision = 0
        result = None
        if operation == "set_state":
            row = await asyncio.to_thread(
                SessionExtensionStateStore(
                    session_manager.repository.sessions_dir,
                    path_resolver=getattr(
                        session_manager,
                        "_resolve_session_path",
                        getattr(session_manager.repository, "_path_resolver", None),
                    ),
                ).set_latest,
                session_id,
                str(definition["plugin_id"]),
                str(definition["namespace"]),
                definition.get("state_value"),
            )
            revision = int(row.get("revision") or 0)
        elif operation == "invoke_tool":
            arguments = plugin_session_action_arguments(definition, body.get("inputs"))
            result = await invoke_plugin_tool(
                runtime_tool_name(plugin_id, str(definition.get("tool") or "")),
                arguments,
                session_id=session_id,
                run_id=f"ui-action-{uuid.uuid4().hex}",
            )
        else:
            return JSONResponse({"ok": False, "error": "unsupported session action"}, status_code=400)
        projected = await asyncio.to_thread(
            plugin_session_ui_snapshot,
            [session_id],
            snapshot_reader=_runtime_v2_extensions_snapshot,
        )
        return JSONResponse(
            {
                "ok": True,
                "plugin_id": plugin_id,
                "action_id": action_id,
                "revision": revision,
                "result": result,
                "session": (projected.get("sessions") or {}).get(session_id, {}),
            }
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Plugin session action failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/plugins/{plugin_id}/enabled")
async def set_plugin_enabled_api(plugin_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    enabled = data.get("enabled") if isinstance(data, dict) else None
    if not isinstance(enabled, bool):
        return JSONResponse({"ok": False, "error": "enabled must be boolean"}, status_code=400)
    try:
        from agent_extensions import set_plugin_enabled

        state = await asyncio.to_thread(set_plugin_enabled, plugin_id, enabled)
        await refresh_web_plugin_lifecycle()
        await agent_mcp.force_reload()
        session_id = str(data.get("session_id") or "").strip()
        if session_id:
            from agent_extensions import audit_plugin_inventory

            await asyncio.to_thread(
                audit_plugin_inventory,
                session_manager,
                session_id,
                "",
            )
        return JSONResponse({"ok": True, "plugin_id": plugin_id, "state": dict(state)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.get("/api/plugins/{plugin_id}/settings")
async def get_plugin_settings_api(plugin_id: str):
    try:
        from agent_extensions import plugin_settings_snapshot

        data = await asyncio.to_thread(plugin_settings_snapshot, plugin_id)
        return JSONResponse(content=data)
    except Exception as exc:
        from plugins import PluginStateError, PluginValidationError

        status = 404 if isinstance(exc, PluginValidationError) and "Unknown plugin" in str(exc) else 400
        if isinstance(exc, PluginStateError):
            status = 500
            logger.exception("Plugin settings read failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


@fastapi_app.patch("/api/plugins/{plugin_id}/settings")
async def update_plugin_settings_api(plugin_id: str, request: Request):
    try:
        from plugin_web_gateway import validate_plugin_write_origin

        validate_plugin_write_origin(
            request.method,
            origin=str(request.headers.get("origin") or ""),
            scheme=request.url.scheme,
            host=str(request.headers.get("host") or request.url.netloc),
            fetch_site=str(request.headers.get("sec-fetch-site") or ""),
        )
    except Exception as exc:
        return _plugin_web_error_response(exc)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    changes = body.get("values") if isinstance(body, dict) else None
    if not isinstance(changes, dict):
        return JSONResponse({"ok": False, "error": "values must be an object"}, status_code=400)
    try:
        from agent_extensions import update_plugin_settings

        data = await asyncio.to_thread(update_plugin_settings, plugin_id, changes)
        return JSONResponse(content=data)
    except Exception as exc:
        from plugins import PluginStateError, PluginValidationError

        status = 404 if isinstance(exc, PluginValidationError) and "Unknown plugin" in str(exc) else 400
        if isinstance(exc, PluginStateError):
            status = 500
            logger.exception("Plugin settings update failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)


@fastapi_app.post("/api/extensions/reload")
async def reload_extensions_api(request: Request):
    try:
        from agent_extensions import reload_extensions

        result = await asyncio.to_thread(reload_extensions)
        await refresh_web_plugin_lifecycle()
        await agent_mcp.force_reload()
        try:
            data = await request.json()
        except Exception:
            data = {}
        session_id = str(data.get("session_id") or "").strip() if isinstance(data, dict) else ""
        if session_id:
            from agent_extensions import audit_plugin_inventory

            await asyncio.to_thread(
                audit_plugin_inventory,
                session_manager,
                session_id,
                "",
                event_type="plugin_reloaded",
            )
        return JSONResponse({"ok": True, **result.to_dict()})
    except Exception as exc:
        logger.exception("Extension reload failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/plugins/install")
async def install_plugin_api(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, dict) or not str(data.get("source") or "").strip():
        return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
    try:
        from agent_extensions import install_plugin

        result = await asyncio.to_thread(
            install_plugin,
            str(data["source"]).strip(),
            replace=bool(data.get("replace", False)),
            ref=str(data.get("ref") or "").strip(),
            install_dependencies=bool(data.get("install_dependencies", False)),
        )
        await refresh_web_plugin_lifecycle()
        await agent_mcp.force_reload()
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        logger.exception("Plugin installation failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.delete("/api/plugins/{plugin_id}")
async def uninstall_plugin_api(plugin_id: str):
    try:
        from agent_extensions import uninstall_plugin

        result = await asyncio.to_thread(uninstall_plugin, plugin_id)
        await refresh_web_plugin_lifecycle()
        await agent_mcp.force_reload()
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        logger.exception("Plugin uninstall failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.post("/api/plugins/{plugin_id}/dependencies")
async def install_plugin_dependencies_api(plugin_id: str):
    try:
        from agent_extensions import install_plugin_dependencies

        result = await asyncio.to_thread(install_plugin_dependencies, plugin_id)
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        logger.exception("Plugin dependency installation failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@fastapi_app.get("/api/mcp/tools")
async def list_mcp_tools():
    try:
        await agent_mcp.ensure_started()
        tools = await asyncio.to_thread(agent_mcp.list_registered_tools)
        servers = await asyncio.to_thread(agent_mcp.list_configured_servers)
        return JSONResponse({"ok": True, "tools": tools, "servers": servers})
    except Exception as exc:
        logger.warning("MCP tools snapshot failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/mcp/servers/{server_name:path}/register")
async def register_mcp_server_api(server_name: str):
    try:
        server = await agent_mcp.register_server(server_name)
        return JSONResponse(
            {
                "ok": True,
                "registered": bool(server.get("discovered")),
                "server": server,
            }
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "unknown MCP server"}, status_code=404)
    except Exception as exc:
        logger.warning("MCP manual registration failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.post("/api/mcp/tools/{function_name}/enabled")
async def set_mcp_tool_enabled_api(function_name: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    enabled = data.get("enabled") if isinstance(data, dict) else None
    if not isinstance(enabled, bool):
        return JSONResponse({"ok": False, "error": "enabled must be boolean"}, status_code=400)
    try:
        ok = await asyncio.to_thread(
            agent_mcp.set_mcp_tool_enabled,
            str(function_name or "").strip(),
            enabled,
        )
        if not ok:
            return JSONResponse({"ok": False, "error": "unknown mcp tool"}, status_code=404)
        return JSONResponse({"ok": True, "function_name": function_name, "enabled": enabled})
    except Exception as exc:
        logger.warning("MCP tool enablement failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@fastapi_app.get("/api/mcp_config")
async def get_mcp_config_snapshot():
    from security.extensions import mcp_registration_candidates

    path = agent_mcp.get_config_path()
    exists = path.is_file()
    text = path.read_text(encoding="utf-8") if exists else ""
    return JSONResponse({
        "ok": True,
        "path": str(path.resolve()),
        "exists": exists,
        "text": text,
        "mcp_registrations": mcp_registration_candidates(),
    })


@fastapi_app.post("/api/mcp_config")
async def save_mcp_config_snapshot(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    text = data.get("text")
    if text is None:
        return JSONResponse({"ok": False, "error": "missing text"}, status_code=400)
    if not isinstance(text, str):
        return JSONResponse({"ok": False, "error": "text must be string"}, status_code=400)
    stripped = text.strip()
    if stripped:
        try:
            json.loads(stripped)
        except json.JSONDecodeError as e:
            return JSONResponse({"ok": False, "error": f"invalid JSON: {e}"}, status_code=400)
    path = agent_mcp.get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = text if text.endswith("\n") else text + "\n"
    path.write_text(out, encoding="utf-8")
    await agent_mcp.force_reload()
    await agent_mcp.ensure_started()
    from security.extensions import mcp_registration_candidates

    return JSONResponse({
        "ok": True,
        "path": str(path.resolve()),
        "mcp_registrations": mcp_registration_candidates(),
    })


@fastapi_app.get("/api/env")
async def get_env_snapshot():
    path = dotenv_file_path()
    raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    flat = [row for row in _parse_env_entries(raw) if row.get("key") not in _MODEL_ENV_KEYS]
    existing_keys = {str(row.get("key") or "") for row in flat}
    for key, default in {
        "SECURITY_ENABLED": "1",
        "EGRESS_HELPER_ENABLED": "1",
        "EXTENSION_REGISTRATION_APPROVAL_ENABLED": "0",
        "ASK_USER_ENABLED": "1",
        "HOOKS_ENABLED": "1",
        "PLUGINS_ENABLED": "1",
    }.items():
        if key in existing_keys:
            continue
        flat.append(
            {
                "key": key,
                "value": default,
                "has_value": True,
                "hint": _ENV_HINTS.get(key, ""),
                "sensitive": False,
                "path_kind": None,
            }
        )
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in flat:
        gid = _ENV_KEY_GROUP.get(row["key"], "other")
        by_group[gid].append(row)
    groups_out: list[dict] = []
    for gid, title, _ in _ENV_GROUP_ORDER:
        bucket = by_group.pop(gid, [])
        bucket.sort(key=lambda r: (_ENV_KEY_ORDER_IN_GROUP.get(r["key"], 9999), r["key"]))
        if bucket:
            groups_out.append({"id": gid, "title": title, "vars": bucket})
    if by_group.get("other"):
        other = sorted(by_group["other"], key=lambda r: r["key"])
        groups_out.append({"id": "other", "title": "其他变量", "vars": other})
    remaining = [(k, v) for k, v in by_group.items() if k != "other"]
    for gid in sorted(gid for gid, _ in remaining):
        arr = sorted(by_group[gid], key=lambda r: r["key"])
        title = next((t for x, t, _ in _ENV_GROUP_ORDER if x == gid), gid)
        groups_out.append({"id": gid, "title": title, "vars": arr})
    return JSONResponse(content={"ok": True, "path": str(path.resolve()), "groups": groups_out})


@fastapi_app.get("/api/features/ask-user")
async def get_ask_user_feature():
    """Return whether the main Agent may create structured questions."""

    return JSONResponse(
        {
            "ok": True,
            "enabled": ask_user_enabled(),
            "env_var": ASK_USER_ENV_VAR,
        }
    )


@fastapi_app.post("/api/features/ask-user")
async def set_ask_user_feature(req: _Request):
    """Persist and immediately apply the Ask User feature switch."""

    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return JSONResponse(
            {"ok": False, "error": "enabled must be boolean"},
            status_code=400,
        )
    env_path = dotenv_file_path()
    previous = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    tmp_path = env_path.with_suffix(env_path.suffix + ".ask-user.tmp")
    try:
        merged = _apply_env_updates(
            previous,
            {ASK_USER_ENV_VAR: "1" if enabled else "0"},
        )
        env_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(merged, encoding="utf-8")
        tmp_path.replace(env_path)
        os.environ[ASK_USER_ENV_VAR] = "1" if enabled else "0"
    except (OSError, ValueError) as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.exception("Failed to persist Ask User feature state")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "enabled": ask_user_enabled()})


@fastapi_app.post("/api/env")
async def save_env_snapshot(req: _Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    vals = data.get("values")
    if not isinstance(vals, dict):
        return JSONResponse({"ok": False, "error": "values must be object"}, status_code=400)
    key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    normalized: dict[str, str] = {}
    for k, v in vals.items():
        if not isinstance(k, str) or not key_re.match(k):
            continue
        if k in _MODEL_ENV_KEYS:
            return JSONResponse(
                {"ok": False, "error": f"{k} must be configured in a model profile"},
                status_code=400,
            )
        if v is None:
            normalized[k] = ""
        elif isinstance(v, bool):
            normalized[k] = str(v).lower()
        elif isinstance(v, (int, float)):
            normalized[k] = str(v)
        elif isinstance(v, str):
            normalized[k] = v
        else:
            return JSONResponse({"ok": False, "error": f"bad value type for {k}"}, status_code=400)
    env_path = dotenv_file_path()
    prev_vals = _dotenv_last_assignments(env_path)
    old_work_dir = (prev_vals.get("WORK_DIR") or "").strip()
    new_work_dir = (normalized.get("WORK_DIR") or "").strip()
    work_dir_changed = "WORK_DIR" in normalized and _work_dir_restart_required(old_work_dir, new_work_dir)
    prev = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    try:
        merged = _apply_env_updates(prev, normalized)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(merged, encoding="utf-8")
    refresh_executor_client_from_env()
    if "EXTENSION_REGISTRATION_APPROVAL_ENABLED" in normalized:
        os.environ["EXTENSION_REGISTRATION_APPROVAL_ENABLED"] = normalized[
            "EXTENSION_REGISTRATION_APPROVAL_ENABLED"
        ]
    extension_keys = {
        "HOOKS_ENABLED",
        "HOOKS_PATH",
        "PLUGINS_ENABLED",
        "PLUGINS_DIR",
        "PLUGINS_DIRS",
        "PLUGINS_STATE_PATH",
        "MCP_ENABLED",
        "EXTENSION_REGISTRATION_APPROVAL_ENABLED",
    }
    if extension_keys.intersection(normalized):
        try:
            from agent_extensions import invalidate_extension_caches
            from agent_tools import invalidate_skills_cache

            invalidate_extension_caches()
            invalidate_skills_cache()
            await agent_mcp.force_reload()
        except Exception:
            logger.exception("Failed to refresh extension registries after env update")
    return JSONResponse({"ok": True, "restart_required": work_dir_changed})


def _upsert_env_line(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    first_idx: Optional[int] = None
    duplicate_idxs: list[int] = []
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            if first_idx is None:
                first_idx = i
            else:
                duplicate_idxs.append(i)
    if first_idx is None:
        lines.append(prefix + value)
        return
    lines[first_idx] = prefix + value
    for i in reversed(duplicate_idxs):
        del lines[i]


@fastapi_app.post("/api/save_config")
async def save_config(req: _Request):
    try:
        data = await req.json()
        env_path = dotenv_file_path()
        prev_vals = _dotenv_last_assignments(env_path)
        updates: dict[str, str] = {}

        api_key = str(data.get("api_key", "") or "").strip()
        url = str(data.get("llm_base_url", "") or "").strip()
        mn = str(data.get("model_name", "") or "").strip()
        _validate_model_name_in_discovered_list(data, mn)
        prov = str(data.get("llm_provider", "") or "").strip().lower()
        provider_aliases = {
            "local": "local",
            "openai": "openai",
            "responses": "openai-responses",
            "@ai-sdk/openai": "openai-responses",
            "openai-responses": "openai-responses",
            "openai-compatible": "openai-compatible",
            "compatible": "openai-compatible",
            "anthropic": "anthropic",
            "@ai-sdk/anthropic": "anthropic",
            "auto": "auto",
        }
        exec_type = provider_aliases.get(prov, "auto")

        if not mn:
            raise ValueError("missing model")
        if not url:
            raise ValueError("missing base_url")
        if exec_type != "local" and not api_key:
            raise ValueError("missing api_key")

        work_dir_changed = False
        if "work_dir" in data:
            work_dir = str(data.get("work_dir", "") or "").strip() or "./workspace"
            work_dir_changed = _work_dir_restart_required(prev_vals.get("WORK_DIR", ""), work_dir)
            updates["WORK_DIR"] = work_dir

        ctx_raw = data.get("context_window", "")
        try:
            ctx_w = int(str(ctx_raw).strip()) if str(ctx_raw).strip() != "" else 119808
        except ValueError:
            ctx_w = 119808
        if ctx_w <= 0:
            ctx_w = 119808
        mot_raw = data.get("max_output_tokens", "")
        try:
            max_out = int(str(mot_raw).strip()) if str(mot_raw).strip() != "" else 8192
        except ValueError:
            max_out = 8192
        if max_out <= 0:
            max_out = 8192
        if "search_provider" in data:
            sp_raw = data.get("search_provider") or "duckduckgo"
            sp = sp_raw.strip().lower() if isinstance(sp_raw, str) else "duckduckgo"
            if sp not in ("duckduckgo", "tavily"):
                sp = "duckduckgo"
            updates["WEB_SEARCH_PROVIDER"] = sp

            sk_raw = data.get("search_api_key", "")
            sk = sk_raw.strip() if isinstance(sk_raw, str) else ""
            if sp == "tavily" and (sk or "TAVILY_API_KEY" not in prev_vals):
                updates["TAVILY_API_KEY"] = sk

        env_path.parent.mkdir(parents=True, exist_ok=True)
        prev = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        env_path.write_text(_apply_env_updates(prev, updates), encoding="utf-8")
        profile = model_profiles.upsert_profile(
            PROJECT_ROOT,
            {
                "name": str(data.get("profile_name") or mn).strip(),
                "model": mn,
                "llm_type": exec_type,
                "base_url": url,
                "api_key": api_key,
                "context_window": ctx_w,
                "max_output_tokens": max_out,
                "model_context_window": max(ctx_w + max_out, ctx_w),
            },
        )
        if not model_profiles.is_usable_profile(profile):
            raise ValueError("saved model profile is not usable")
        _invalidate_executor_config_cache()
        refresh_executor_client_from_env()
        return {
            "ok": True,
            "restart_required": work_dir_changed,
            "profile": model_profiles.public_profile(profile),
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Remote Control is isolated behind an explicit feature flag and its own
# authenticated, versioned router. Keeping registration here avoids circular
# imports while allowing the transport to reuse the existing run coordinator.
from remote_control.config import RemoteControlConfig as _RemoteControlConfig
from remote_control.gateway import register_remote_control as _register_remote_control
from remote_control.service import ControlDependencies as _RemoteControlDependencies

_remote_control_config = _RemoteControlConfig.from_env(PROJECT_ROOT)
_control_dependencies = _RemoteControlDependencies(
    session_manager=session_manager,
    astream_events=astream_events,
    reserve_start=_reserve_session_chat_start,
    release_start=_release_session_chat_start,
    is_stream_active=_is_session_stream_active,
)
_remote_control_gateway = _register_remote_control(
    fastapi_app,
    _remote_control_config,
    _control_dependencies,
)

from fastapi.responses import RedirectResponse as _RedirectResponse
@fastapi_app.middleware("http")
async def _config_check(req: _Request, call_next):
    p = req.url.path
    if p == "/api/upload-chat-files":
        try:
            content_length = int(req.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            content_length = 0
        request_limit = CHAT_UPLOAD_MAX_TOTAL_BYTES + _CHAT_UPLOAD_MULTIPART_OVERHEAD_BYTES
        if content_length > request_limit:
            return JSONResponse(
                {"ok": False, "error": "本次上传总大小超过 200 MB 限制。"},
                status_code=413,
            )
    if p in (
        "/setup",
        "/setup/env",
        "/setup/mcp",
        "/setup/extensions",
        "/api/save_config",
        "/api/env",
        "/api/mcp_config",
        "/api/extensions",
        "/api/model_profiles",
        "/api/model_profiles/discover",
        "/api/pick-path",
        "/api/upload-chat-files",
        "/api/workspace-files",
    ) or p.startswith("/static/") or p.startswith("/assets/") or p.startswith("/api/model_profiles/") or p.startswith("/api/plugins/") or p.startswith("/api/extensions/") or p.startswith("/api/remote/v1/"):
        return await call_next(req)
    if not _is_configured():
        return _RedirectResponse(url="/setup")
    return await call_next(req)


def _plugin_web_error_response(exc: Exception) -> JSONResponse:
    from plugin_web_gateway import PluginWebError

    if isinstance(exc, PluginWebError):
        return JSONResponse(
            {"ok": False, "error": exc.code, "message": str(exc)},
            status_code=exc.status,
        )
    logger.warning("Plugin Web gateway failed", exc_info=True)
    return JSONResponse(
        {"ok": False, "error": "plugin_gateway_error"},
        status_code=500,
    )


@fastapi_app.get("/plugins/{plugin_id}")
async def plugin_web_page(plugin_id: str):
    from plugin_web_gateway import plugin_page

    try:
        path = await run_in_threadpool(plugin_page, plugin_id)
    except Exception as exc:
        return _plugin_web_error_response(exc)
    return FileResponse(
        str(path),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": (
                "default-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "connect-src 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@fastapi_app.get("/plugin-assets/{plugin_id}/{asset_path:path}")
async def plugin_web_asset(plugin_id: str, asset_path: str):
    from plugin_web_gateway import plugin_asset

    try:
        path = await run_in_threadpool(plugin_asset, plugin_id, asset_path)
    except Exception as exc:
        return _plugin_web_error_response(exc)
    return FileResponse(
        str(path),
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@fastapi_app.api_route(
    "/api/plugins/{plugin_id}/{api_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def plugin_web_api(plugin_id: str, api_path: str, req: _Request):
    from plugin_web_gateway import (
        MAX_PLUGIN_REQUEST_BODY,
        invoke_plugin_http,
        validate_plugin_write_origin,
    )

    try:
        validate_plugin_write_origin(
            req.method,
            origin=str(req.headers.get("origin") or ""),
            scheme=req.url.scheme,
            host=str(req.headers.get("host") or req.url.netloc),
            fetch_site=str(req.headers.get("sec-fetch-site") or ""),
        )
    except Exception as exc:
        return _plugin_web_error_response(exc)

    try:
        content_length = int(req.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > MAX_PLUGIN_REQUEST_BODY:
        return JSONResponse(
            {"ok": False, "error": "request_too_large"},
            status_code=413,
        )
    body = await req.body()
    query: Dict[str, Any] = {}
    for key, value in req.query_params.multi_items():
        previous = query.get(key)
        if previous is None:
            query[key] = value
        elif isinstance(previous, list):
            previous.append(value)
        else:
            query[key] = [previous, value]
    try:
        result = await run_in_threadpool(
            invoke_plugin_http,
            plugin_id,
            method=req.method,
            path="/" + str(api_path or "").lstrip("/"),
            query=query,
            headers=dict(req.headers),
            body=body,
            session_run_grant=str(req.headers.get("x-plugin-session-run-grant") or ""),
        )
    except Exception as exc:
        return _plugin_web_error_response(exc)
    return Response(
        content=result.body,
        status_code=result.status,
        headers=dict(result.headers),
    )


@fastapi_app.get("/{legacy_plugin_id}", include_in_schema=False)
async def legacy_plugin_web_page(legacy_plugin_id: str):
    """Keep pre-migration top-level plugin page URLs working.

    Existing application routes are registered before this fallback.  A
    remaining single-segment path is redirected only when it names an enabled
    plugin with a valid Web entry, so disabling or uninstalling the plugin
    still removes both its canonical page and its compatibility alias.
    """

    from plugin_web_gateway import plugin_page

    try:
        await run_in_threadpool(plugin_page, legacy_plugin_id)
    except Exception as exc:
        return _plugin_web_error_response(exc)
    return _RedirectResponse(
        url=f"/plugins/{legacy_plugin_id}",
        status_code=307,
        headers={"Cache-Control": "no-store"},
    )





def _warm_ui_caches() -> None:
    """Prime slow first-hit caches in the background.

    Skills discovery walks a large project skill tree, the extensions snapshot
    loads the plugin/hook registry, and the Runtime V2 history projection
    builds per-session UI pages. Warming them a few seconds after startup moves
    that cost out of the first page/skill-popover open.
    """
    import time as _warm_time

    def _run() -> None:
        # Delay inside the worker.  Sleeping before Thread.start() blocks the
        # FastAPI startup event and adds a fixed two seconds to service
        # readiness, which defeats the purpose of background warm-up.
        try:
            _warm_time.sleep(2.0)
        except Exception:
            return
        try:
            from agent_tools import discover_skills

            discover_skills(include_disabled=True)
        except Exception:
            logger.debug("Skills cache warm-up failed", exc_info=True)
        try:
            from agent_extensions import extensions_snapshot

            extensions_snapshot()
        except Exception:
            logger.debug("Extensions snapshot warm-up failed", exc_info=True)
        try:
            from runtime_v2 import runtime_v2_primary

            if not runtime_v2_primary():
                return
            from runtime_v2.ui_projection import RuntimeUiProjection

            projection = RuntimeUiProjection(
                session_manager.repository.sessions_dir,
                path_resolver=session_manager._resolve_session_path,
            )
            for row in session_manager.list_sessions(include_archived=False)[:12]:
                sid = str((row or {}).get("id") or "").strip()
                if not sid:
                    continue
                try:
                    projection.read_ui_page(sid, turns=5)
                except Exception:
                    logger.debug("History warm-up skipped session=%s", sid, exc_info=True)
        except Exception:
            logger.debug("History cache warm-up failed", exc_info=True)

    threading.Thread(target=_run, name="ui-cache-warmup", daemon=True).start()


def initialize_ui_attention_notifications() -> None:
    """Bind attention notifications to the running ASGI server loop.

    The production entrypoint installs a custom FastAPI lifespan. Keeping this
    explicit lets both lifespans initialize the event-bus notification bridge.
    """

    global _UI_ATTENTION_MAIN_LOOP
    _UI_ATTENTION_MAIN_LOOP = asyncio.get_running_loop()


async def start_webui_lifecycle() -> None:
    """Start Web UI services shared by the default and production lifespans."""
    _warm_ui_caches()
    initialize_ui_attention_notifications()
    await start_react_recovery_runner()
    try:
        from agent_extensions import start_plugin_background_services
        from plugins.host import start_bundled_host_extensions

        await start_plugin_background_services()
        await start_bundled_host_extensions(
            load_plugins(force=True).plugins,
            {"session_manager": session_manager, "project_root": PROJECT_ROOT},
        )
    except Exception:
        logger.warning("Plugin background service startup failed", exc_info=True)
    # Warm the hook pipeline off the critical path: the first user message
    # must not pay the plugin scan + hook manager build (observed 11s on a
    # busy disk inside the request).
    async def _warm_hook_pipeline_task() -> None:
        await asyncio.sleep(3.0)
        try:
            from agent_extensions import warm_hook_pipeline

            await warm_hook_pipeline()
            logger.info("hook pipeline warmed at startup")
        except Exception:
            logger.warning("hook pipeline warmup failed", exc_info=True)

    global _hook_pipeline_warmup_task
    _hook_pipeline_warmup_task = asyncio.create_task(_warm_hook_pipeline_task())


async def refresh_web_plugin_lifecycle() -> None:
    """Reconcile generic plugin routes, workers, and trusted lifecycles."""

    from agent_extensions import load_plugins, start_plugin_background_services
    from plugins.host import (
        install_bundled_host_extensions,
        start_bundled_host_extensions,
    )

    loaded = load_plugins(force=True).plugins
    context = {"session_manager": session_manager, "project_root": PROJECT_ROOT}
    install_bundled_host_extensions(fastapi_app, loaded, context)
    from workflow_extensions import activate_bundled_workflow_callbacks

    activate_bundled_workflow_callbacks(sys.modules["agent_loop"], force=True)
    await start_plugin_background_services()
    await start_bundled_host_extensions(loaded, context)


async def stop_webui_lifecycle() -> None:
    """Stop Web UI services shared by the default and production lifespans."""
    try:
        from agent_extensions import stop_plugin_runtime
        from plugins.host import stop_bundled_host_extensions

        await stop_bundled_host_extensions(
            {"session_manager": session_manager, "project_root": PROJECT_ROOT}
        )
        await stop_plugin_runtime()
    finally:
        await stop_react_recovery_runner()
