"""
基于 FastAPI + SSE 的 Web 壳：聊天流、会话 CRUD、历史加载。

大事件在 agent_loop.astream_events 中产生；本模块负责 JSON 行协议与分块刷出（sleep(0)）。
"""

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from typing import Optional

from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from agent import astream_events, astream_events_continuation, session_manager
from agent_harness import PROJECT_ROOT, WORK_DIR, dotenv_file_path, refresh_executor_client_from_env
from agent_loop import compute_context_tokens_for_session
from session_lifecycle import is_run_active
from session_event_bus import subscribe_session_events
import agent_mcp
from path_picker_util import pick_native_path

_PATH_PICKER_JS_PATH = Path(__file__).resolve().parent / "templates" / "static" / "myagent_path_picker.js"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_DIST_INDEX = _TEMPLATES_DIR / "dist" / "index.html"
_DIST_ASSETS = _TEMPLATES_DIR / "dist" / "assets"

# SSE 响应头：降低反向代理/浏览器对小块的缓冲
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

fastapi_app = FastAPI()

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

_CHAT_ACTIVE_TIMEOUT_SEC = int(os.getenv("CHAT_ACTIVE_TIMEOUT_SEC", "300"))
logger = logging.getLogger(__name__)

def _cleanup_stale_active_chat():
    import time as _t
    now = _t.time()
    stale = [sid for sid, ts in list(_active_chat_last_seen.items()) if now - ts > _CHAT_ACTIVE_TIMEOUT_SEC]
    for sid in stale:
        _active_chat_by_session.pop(sid, None)
        _active_chat_last_seen.pop(sid, None)


def _is_session_stream_active(sid: str) -> bool:
    x = str(sid or "").strip()
    return bool(x) and (_active_chat_by_session.get(x, 0) > 0 or is_run_active(x))

def get_index_html():
    """读取并返回 Vite 构建产物 templates/dist/index.html。"""
    import agent_harness as _ui_ah

    work_dir = str(WORK_DIR.resolve())
    sessions_dir = str((WORK_DIR / "sessions").resolve())
    app_dotenv = str(dotenv_file_path().resolve())
    ctx_thr = _ui_ah.CONTEXT_WINDOW
    inject = (
        "<script>"
        f"window.__UI_LOG_TRUNCATE_KEEP_LINES__={UI_LOG_TRUNCATE_KEEP_LINES};"
        f"window.__CONTEXT_WINDOW__={ctx_thr};"
        f"window.__WORK_DIR__={json.dumps(work_dir)};"
        f"window.__SESSIONS_DIR__={json.dumps(sessions_dir)};"
        f"window.__APP_DOTENV_PATH__={json.dumps(app_dotenv)};"
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
    await run_in_threadpool(session_manager.refresh_sessions_index_from_disk)
    return HTMLResponse(
        content=get_index_html(),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@fastapi_app.get("/api/open-workspace-file")
async def open_workspace_file(
    rel: str = Query("", description="工作区相对路径、虚拟 /path 或位于工作区内的 Windows 绝对路径"),
):
    """
    用系统默认应用打开工作区内的文件。浏览器禁止从 http(s) 页面跳转 file://，故通过后端 os.startfile / open / xdg-open 打开。
    """
    import os
    import platform
    import subprocess

    raw = unquote(rel or "").strip().strip('"').strip("'")
    if not raw:
        return JSONResponse({"ok": False, "error": "路径为空"}, status_code=400)
    wd = WORK_DIR.resolve()
    app_root = Path(__file__).resolve().parent.resolve()
    if len(raw) >= 2 and raw[1] == ":":
        cand = Path(raw)
    elif raw.startswith("\\\\"):
        cand = Path(raw)
    else:
        cand = (WORK_DIR / raw.lstrip("/")).resolve()
    try:
        cand = cand.resolve()
    except OSError:
        return JSONResponse({"ok": False, "error": "无效路径"}, status_code=400)
    allowed = False
    for root in (wd, app_root):
        try:
            cand.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if (len(raw) >= 2 and raw[1] == ":") or raw.startswith("\\\\"):
        allowed = True
    if not allowed:
        return JSONResponse({"ok": False, "error": "仅限工作区或应用目录内的文件/文件夹"}, status_code=403)
    if not cand.exists() or not (cand.is_file() or cand.is_dir()):
        return JSONResponse({"ok": False, "error": "文件或文件夹不存在"}, status_code=404)

    def _open() -> None:
        p = str(cand)
        sysname = platform.system()
        if sysname == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        elif sysname == "Darwin":
            subprocess.Popen(["open", p], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", p], close_fds=True)

    await run_in_threadpool(_open)
    return JSONResponse({"ok": True, "path": str(cand)})


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
    try:
        content = _PATH_PICKER_JS_PATH.read_text(encoding="utf-8")
    except OSError:
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=content, media_type="application/javascript")


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
    except Exception:
        s["subagent_pending_continue"] = 0
        s["subagent_can_continue"] = False
    try:
        s["react_can_continue"] = session_manager.can_continue_react_session(str(session_id))
    except Exception:
        s["react_can_continue"] = False


@fastapi_app.get("/sessions")
async def list_sessions(include_archived: bool = Query(False)):
    sessions = session_manager.list_sessions(include_archived=include_archived)
    _cleanup_stale_active_chat()
    for s in sessions:
        sid = s.get("id")
        if sid:
            s["stream_active"] = _is_session_stream_active(str(sid))
        else:
            s["stream_active"] = False
    return JSONResponse(content=sessions)

@fastapi_app.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    include_subagents: bool = Query(False, description="涓?true 鏃惰绠?subagent 渚ф爮鐘舵€佸瓧娈?"),
):
    """单条会话摘要（与列表项结构一致），供侧栏增量更新。"""
    s = session_manager.get_session_summary(session_id)
    _cleanup_stale_active_chat()
    if not s:
        return JSONResponse(content={"error": "not found"}, status_code=404)
    sid = s.get("id")
    if sid:
        s["stream_active"] = _is_session_stream_active(str(sid))
        if include_subagents:
            _attach_subagent_sidebar_fields(s, str(sid))
    else:
        s["stream_active"] = False
    return JSONResponse(content=s)


@fastapi_app.get("/sessions/{session_id}/subagents")
async def list_session_subagents(
    session_id: str,
    lite: bool = Query(False, description="为 true 时不加载 dialogue_turns，减轻列表刷新开销"),
):
    """返回当前会话下 subagent 扁平列表（含嵌套），供 UI 树展示。"""
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
                    "running": str(task.get("status") or "") == "running",
                    "ok": False if task.get("error") else None,
                    "status": str(task.get("status") or ""),
                    "task_status": str(task.get("status") or ""),
                    "error": str(task.get("error") or ""),
                    "result_preview": str(task.get("result_preview") or "")[:1200],
                    "output_file": str(task.get("output_file") or ""),
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
    session_id, _, _, _, _, _ = session_manager.get_or_create_session()
    session = session_manager.get_session_summary(session_id) or {"id": session_id}
    return JSONResponse(content={"session_id": session_id, "session": session})

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
        session_manager.delete_session(sid)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/interrupt")
async def interrupt_session(session_id: str):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    session_manager.request_interrupt(sid)
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

    from tool_approval_gate import resolve_tool_approval

    matched = resolve_tool_approval(session_id, aid, approve)
    return JSONResponse(content={"ok": matched})


@fastapi_app.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(None),
):
    sid = (session_id or "").strip() or None
    if sid:
        session_manager.clear_interrupt(sid)

    def should_stop(sid_: str) -> bool:
        return session_manager.is_interrupt_requested(sid_)

    async def event_generator():
        if sid:
            _active_chat_by_session[sid] = _active_chat_by_session.get(sid, 0) + 1
            import time as _time_stamp; _active_chat_last_seen[sid] = _time_stamp.time()
        try:
            try:
                async for event in astream_events(
                    message,
                    session_id=sid,
                    should_stop=should_stop,
                ):
                    if sid and await request.is_disconnected():
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)  # 让 ASGI/uvicorn 尽快把分块刷到客户端，利于工具/LLM 分条显示
                if not await request.is_disconnected():
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                # 浏览器主动断开 SSE 连接属于正常情况，避免打印冗长异常栈
                return
        finally:
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
async def stream_session_events(session_id: str, request: Request):
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)

    async def event_generator():
        _active_chat_by_session[sid] = _active_chat_by_session.get(sid, 0) + 1
        import time as _time_stamp
        _active_chat_last_seen[sid] = _time_stamp.time()
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


@fastapi_app.post("/sessions/{session_id}/continue")
async def continue_react_session(session_id: str, request: Request):
    """Continue a parent ReAct loop that has no final answer yet."""
    sid = (session_id or "").strip()
    if not sid:
        return JSONResponse(content={"error": "missing session_id"}, status_code=400)
    if not session_manager.can_continue_react_session(sid):
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
                    require_pending_subagents=False,
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
    turns: Optional[int] = Query(None, ge=1, le=50),
):
    """
    与 SSE 同源：默认返回完整 ui_events 数组（兼容旧前端）。
    传入 limit 或 turns 时返回分页对象。
    turns：按「用户提问」轮次分页（每页若干完整对话）；优先于 limit。
    """
    if limit is None and turns is None:
        return JSONResponse(content=session_manager.get_ui_events_for_display(session_id))
    lim = int(limit) if limit is not None else 200
    tv = int(turns) if turns is not None else None
    payload = session_manager.get_ui_events_page(
        session_id, limit=lim, before_index=before_index, turns=tv
    )
    return JSONResponse(content=payload)


@fastapi_app.get("/sessions/{session_id}/messages/count")
async def get_session_message_count(session_id: str):
    """仅返回 ui_events 条数，供发送前对齐 eventIndex，避免下载整份 JSON。"""
    return JSONResponse(content={"count": session_manager.get_ui_event_count(session_id)})


@fastapi_app.get("/sessions/{session_id}/user_turns")
async def get_session_user_turns(session_id: str):
    """列出会话内全部用户消息的 event_index 与预览（供右侧「历史记录」目录，与消息是否分页加载无关）。"""
    return JSONResponse(content=session_manager.get_ui_user_turns_for_toc(session_id))


@fastapi_app.get("/sessions/{session_id}/todo_plan")
async def get_session_todo_plan(session_id: str):
    """当前会话 Todo 计划快照（todo_plan.md），供左侧「当前计划」面板。"""
    return JSONResponse(content=session_manager.get_todo_plan_snapshot(session_id))


@fastapi_app.delete("/sessions/{session_id}/todo_plan")
async def clear_session_todo_plan(session_id: str):
    """用户手动清除当前会话的 Todo 计划。"""
    ok = session_manager.clear_todo_plan(session_id)
    return JSONResponse(content={"ok": ok})


@fastapi_app.get("/sessions/{session_id}/context_tokens")
async def get_session_context_tokens(session_id: str):
    """
    按当前落盘 llm_history / key_context 现算整包输入 token 估算（与主循环一致）。
    在线程池执行，避免阻塞事件循环；CPU 重计算不挡其它轻量 API。
    """
    out = await run_in_threadpool(compute_context_tokens_for_session, session_id)
    if not out.get("ok"):
        return JSONResponse(content=out, status_code=400)
    return JSONResponse(content=out)


@fastapi_app.put("/sessions/{session_id}/name")
async def rename_session(session_id: str, name: str = Form(...)):
    session_manager.set_session_name(session_id, name)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.put("/sessions/{session_id}/archive")
async def archive_session(session_id: str, archived: bool = Form(...)):
    session_manager.set_session_archived(session_id, archived)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.put("/sessions/{session_id}/pin")
async def pin_session(session_id: str, pinned: bool = Form(...)):
    session_manager.set_session_pinned(session_id, pinned)
    return JSONResponse(content={"status": "ok"})


@fastapi_app.post("/sessions/{session_id}/truncate")
async def truncate_session_events(
    session_id: str,
    before_index: int = Query(..., description="保留事件区间 [0, before_index)"),
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
        ok = session_manager.truncate_session_at_event_index(session_id, int(before_index))
    except (TypeError, ValueError):
        return JSONResponse(content={"ok": False, "error": "invalid before_index"}, status_code=400)
    if not ok:
        return JSONResponse(
            content={"ok": False, "error": "truncation failed"},
            status_code=400,
        )
    return JSONResponse(content={"ok": True})


@fastapi_app.post("/sessions/{session_id}/branch")
async def branch_session_events(
    session_id: str,
    before_index: int = Query(
        ...,
        description="新会话保留 ui_events[0:before_index]（与 truncate 语义一致）",
    ),
):
    """
    在当前会话的 event 下标处复制出分支会话，原会话不变。
    最终答案处分支时，前端应传 final 事件的 eventIndex + 1。
    """
    try:
        result = session_manager.branch_session_at_event_index(
            session_id, int(before_index)
        )
    except (TypeError, ValueError):
        return JSONResponse(content={"ok": False, "error": "invalid before_index"}, status_code=400)
    if not result:
        return JSONResponse(
            content={"ok": False, "error": "branch failed"},
            status_code=400,
        )
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
        ok = session_manager.append_ui_events_tail(session_id, tail)
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)
    if not ok:
        return JSONResponse(content={"ok": False, "error": "append failed"}, status_code=400)
    return JSONResponse(content={"ok": True})


# === Setup wizard (build_exe) ===
from pathlib import Path as _Path
from fastapi import Request as _Request
from fastapi.responses import HTMLResponse as _HTMLResponse
_CONFIG_PATH = _Path(__file__).resolve().parent / "templates" / "frist_time_config.html"


def _load_config_wizard_html() -> str:
    if _CONFIG_PATH.is_file():
        return _CONFIG_PATH.read_text(encoding="utf-8")
    # 极简兜底（完整 UI：templates/frist_time_config.html）
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>首次配置</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:2rem auto;padding:1rem;">
<h1>General Agent · 首次配置</h1>
<p>缺少 <code>templates/frist_time_config.html</code>，使用简易表单。</p>
<form id="f"><label>OPENAI_API_KEY<input id="k" type="password" style="width:100%;margin:.5rem 0"></label>
<label>OPENAI_BASE_URL<input id="u" type="text" placeholder="https://api.deepseek.com" style="width:100%;margin:.5rem 0"></label>
<button type="submit">保存</button></form>
<pre id="e" style="color:red"></pre>
<script>
document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();document.getElementById('e').textContent='';
const r=await fetch('/api/save_config',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({api_key:document.getElementById('k').value,llm_base_url:document.getElementById('u').value})});
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


# 首页放行前：下列变量须在 .env 中均有非空取值（strip 后）；缺文件、缺 key、值为空或占位密钥 → 走向导
_REQUIRED_ENV_FOR_MAIN_UI = (
    "EXECUTOR_LLM",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "EXECUTOR_LLM_TYPE",
    "CONTEXT_WINDOW",
    "MAX_OUTPUT_TOKENS",
    "WORK_DIR",
)


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


def _dotenv_last_non_empty_assignments(path: Path) -> dict[str, str]:
    """解析 .env 中每个 key 的最后一个非空值，用于配置向导容错重复空行。"""
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
        parsed = _parse_dotenv_rhs(val).strip()
        if parsed:
            out[key] = parsed
    return out


def _is_configured():
    env_path = dotenv_file_path()
    vals = _dotenv_last_assignments(env_path)
    fallback_vals = _dotenv_last_non_empty_assignments(env_path)
    
    # 尝试加载加密配置作为补充
    encrypted_config = {}
    try:
        from secret_loader import load_encrypted_config
        encrypted_config = load_encrypted_config()
    except Exception:
        pass
    
    for req in _REQUIRED_ENV_FOR_MAIN_UI:
        v = vals.get(req)
        if (v is None or v.strip() == "") and fallback_vals.get(req):
            v = fallback_vals[req]
        # 如果.env中没有，检查加密配置
        if (v is None or str(v).strip() == "") and encrypted_config.get(req):
            v = encrypted_config[req]
        if v is None or str(v).strip() == "":
            return False
    
    api_key_for_check = (vals.get("OPENAI_API_KEY") or "").strip() or fallback_vals.get("OPENAI_API_KEY", "")
    if not api_key_for_check:
        api_key_for_check = encrypted_config.get("OPENAI_API_KEY", "")
    if "YOUR_API_KEY" in api_key_for_check:
        return False
    return True


def _infer_llm_provider_for_wizard(vals: dict[str, str]) -> str:
    """向导三选一：deepseek / openai / local（与 EXECUTOR_LLM_TYPE + BASE_URL 对齐）。"""
    et = (vals.get("EXECUTOR_LLM_TYPE") or "").strip().lower()
    if et == "local":
        return "local"
    url = (vals.get("OPENAI_BASE_URL") or "").lower()
    if "deepseek" in url:
        return "deepseek"
    return "openai"


def _wizard_prefill_from_dotenv() -> dict[str, str]:
    """从 .env 最后一遍赋值生成向导预填（原始字符串，不经进程环境默认值改写）。"""
    vals = _dotenv_last_assignments(dotenv_file_path())
    if not vals:
        return {}
    out: dict[str, str] = {}
    pairs = (
        ("WORK_DIR", "work_dir"),
        ("CONTEXT_WINDOW", "context_window"),
        ("MAX_OUTPUT_TOKENS", "max_output_tokens"),
        ("EXECUTOR_LLM", "model_name"),
        ("OPENAI_BASE_URL", "llm_base_url"),
        ("WEB_SEARCH_PROVIDER", "search_provider"),
    )
    for env_k, js_k in pairs:
        v = vals.get(env_k)
        if v is not None and str(v).strip() != "":
            out[js_k] = str(v).strip()
    if vals.get("OPENAI_API_KEY"):
        out["api_key_set"] = "1"
    if vals.get("TAVILY_API_KEY"):
        out["search_api_key_set"] = "1"
    out["llm_provider"] = _infer_llm_provider_for_wizard(vals)
    return out


@fastapi_app.get("/setup", response_class=_HTMLResponse)
async def setup_page():
    # 每次都从磁盘读，替换 templates/frist_time_config.html 后立即生效；避免 stale 缓存
    body = _load_config_wizard_html()
    prefill = _wizard_prefill_from_dotenv()
    inject = (
        "<script>"
        f"window.__DEFAULT_WORK_DIR__={json.dumps(str(WORK_DIR.resolve()))};"
        f"window.__WIZARD_PREFILL__={json.dumps(prefill)};"
        "</script>"
    )
    if "</head>" in body:
        body = body.replace("</head>", inject + "</head>", 1)
    body = _html_with_path_picker_script(body)
    return _HTMLResponse(
        content=body,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


_ENV_ADVANCED_PATH = _Path(__file__).resolve().parent / "templates" / "advance_config.html"
_MCP_CONFIG_HTML_PATH = _Path(__file__).resolve().parent / "templates" / "mcp_config.html"


def _load_mcp_config_html() -> str:
    if _MCP_CONFIG_HTML_PATH.is_file():
        return _MCP_CONFIG_HTML_PATH.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><p>缺少 templates/mcp_config.html</p><a href='/'>返回</a></body></html>"


_ENV_GROUP_ORDER: list[tuple[str, str, list[str]]] = [
    (
        "llm",
        "模型与 OpenAI 兼容 API",
        [
            "EXECUTOR_LLM",
            "EXECUTOR_LLM_TYPE",
            "CONTEXT_WINDOW",
            "MAX_OUTPUT_TOKENS",
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "LLM_THINKING_MODE",
            "LLM_REASONING_EFFORT",
            "LLM_EXTRA_BODY_JSON",
            "EXECUTOR_TEMPERATURE",
            "LOCAL_LLM_HOST",
            "LOCAL_LLM",
        ],
    ),
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
            "OPENAI_RETRY_BASE_SEC",
        ],
    ),
    (
        "paths",
        "目录与运行环境",
        [
            "WORK_DIR",
            "SKILLS_DIR",
            "LOG_DIR",
            "NODE_HOME",
            "NVM_SYMLINK",
            "RUN_SHELL_USE_BASH",
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
        ],
    ),
    (
        "context",
        "上下文压缩与回顾",
        [
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
            "LLM_CONTEXT_TRUNCATE_KEEP_CHARS",
            "GREP_MAX_MATCH_LINES",
            "GLOB_MAX_MATCHES",
            "LS_MAX_ENTRIES",
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
    "EXECUTOR_LLM": "执行器使用的模型名（须与所选服务商一致）。",
    "EXECUTOR_LLM_TYPE": "local = 本地 OpenAI 兼容（如 Ollama）；openai = 使用 OPENAI_* 远端 API。",
    "CONTEXT_WINDOW": "预估上下文 token 超过该门限时触发压缩摘要等策略。",
    "MAX_OUTPUT_TOKENS": "模型单次输出的 token 上限（依服务商与实际模型调整）。",
    "OPENAI_BASE_URL": "OpenAI 兼容 API 根地址（DeepSeek/OpenAI 等）。",
    "OPENAI_API_KEY": "远端 API 密钥（仅保存在本机 .env）。",
    "LLM_THINKING_MODE": "思考扩展：enabled（默认）或 disabled。disabled 时不使用 LLM_REASONING_EFFORT。DeepSeek 基准 URL 在 disabled 时会显式请求 thinking.disabled；可用 LLM_EXTRA_BODY_JSON 整段覆盖。",
    "LLM_REASONING_EFFORT": "仅在思考开启时下发 reasoning_effort；未设置时默认 high；可按服务商填写 low/medium/high/max 等（原样传给 API）。",
    "LLM_EXTRA_BODY_JSON": "可选：JSON 字符串，合并进请求 extra_body（覆盖自动生成）。",
    "EXECUTOR_TEMPERATURE": "采样温度（若模型支持）。",
    "LOCAL_LLM_HOST": "本地兼容服务地址，如 http://localhost:11434。",
    "LOCAL_LLM": "本地使用的模型标识（Ollama 模型名等）。",
    "WEB_SEARCH_PROVIDER": "网页搜索提供者：duckduckgo、tavily、brave、searxng、jina 等。",
    "TAVILY_API_KEY": "Tavily API Key（在 app.tavily.com 获取）。",
    "BRAVE_API_KEY": "Brave Search API Key。",
    "SEARXNG_BASE_URL": "自建 SearXNG 实例根 URL。",
    "JINA_API_KEY": "Jina 搜索 API Key。",
    "WEB_SEARCH_MAX_RESULTS": "单次搜索返回结果条数上限。",
    "HTTPS_PROXY": "HTTPS 代理，如 http://127.0.0.1:7890（可选）。",
    "HTTP_PROXY": "HTTP 代理（可选）。",
    "WEB_DOWNLOAD_MAX_BYTES": "web_download 单次下载字节上限。",
    "TOOL_UI_APPROVAL": "1（默认）时对 web_download 及 restrict_to_workspace=false 的 Shell 在浏览器弹窗确认后才执行；0 关闭。",
    "TOOL_UI_APPROVAL_WAIT_SEC": "等待用户在 UI 内确认的最长时间（秒），超时视为拒绝。",
    "OPENAI_HTTP_TIMEOUT": "兼容 API 请求超时（秒）。",
    "OPENAI_MAX_RETRIES": "可重试错误时的最大重试次数。",
    "OPENAI_RETRY_BASE_SEC": "重试基础退避时间（秒）。",
    "WORK_DIR": "工作区根目录（文件工具沙箱）。",
    "SKILLS_DIR": "技能包目录（默认可于 WORK_DIR 下）。",
    "LOG_DIR": "日志输出目录。",
    "NODE_HOME": "可选：prepend 到子进程 PATH 的 Node 安装目录。",
    "NVM_SYMLINK": "nvm-windows 当前 node 的 symlink 目录（可选）。",
    "RUN_SHELL_USE_BASH": "Windows 下是否优先用 Git Bash 执行 shell（0=强制 cmd）。",
    "RUN_SHELL_BASH": "bash.exe 路径（可选）。",
    "MAX_REACT_ITER": "ReAct 主循环最大迭代轮数。",
    "VERBOSE_LOGGING": "是否输出更详细的运行日志。",
    "TODO_MAX_ITEMS": "Todo 列表展示/跟踪条数上限。",
    "MAX_PARALLEL_TOOLS": "允许的并行工具调用数量上限。",
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
    "LLM_CONTEXT_TRUNCATE_KEEP_CHARS": "写入 LLM 上下文的单条工具结果首尾各保留字符数（过长时中间省略并在开头提示分块阅读）。",
    "GREP_MAX_MATCH_LINES": "grep 最多返回的匹配行数（跨文件累计）。",
    "GLOB_MAX_MATCHES": "glob 最多返回的路径条数。",
    "LS_MAX_ENTRIES": "ls/list_dir 单层目录最多列出的条目数。",
    "READ_FILE_RANGE_MAX_BYTES": "使用 start_line/end_line 按行读取时，文件超过该字节则拒绝（避免 readlines 一次性载入巨型文件）。",
    "MICRO_SHRINK_REASONING_CHARS": "微压：推理内容字符上限。",
    "MICRO_SHRINK_ASSISTANT_CHARS": "微压：助手正文字符上限。",
    "MICRO_SHRINK_TOOL_CHARS": "微压：工具返回字符上限。",
    "MICRO_SHRINK_FAT_TOOL_FLOOR": "大工具输出的字符下限保护与 MICRO_SHRINK_TOOL_CHARS 联用。",
}


_NON_SENSITIVE = frozenset({"MAX_OUTPUT_TOKENS", "CONTEXT_WINDOW"})

_ENV_PATH_KIND_FILE = frozenset(
    {
        "RUN_SHELL_BASH",
        "MCP_SERVERS_JSON",
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
                "value": "" if sensitive else parsed_val,
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
        return _ENV_ADVANCED_PATH.read_text(encoding="utf-8")
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


@fastapi_app.get("/api/mcp_config")
async def get_mcp_config_snapshot():
    path = agent_mcp.get_config_path()
    exists = path.is_file()
    text = path.read_text(encoding="utf-8") if exists else ""
    return JSONResponse({"ok": True, "path": str(path.resolve()), "exists": exists, "text": text})


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
    return JSONResponse({"ok": True, "path": str(path.resolve())})


@fastapi_app.get("/api/env")
async def get_env_snapshot():
    path = dotenv_file_path()
    raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    flat = _parse_env_entries(raw)
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
        if "api_key" in data:  # 前端传了api_key字段就更新（支持清空）
            updates["OPENAI_API_KEY"] = api_key

        url = str(data.get("llm_base_url", "") or "").strip()
        if url:
            updates["OPENAI_BASE_URL"] = url

        mn = str(data.get("model_name", "") or "").strip()
        if mn:
            updates["EXECUTOR_LLM"] = mn

        prov = str(data.get("llm_provider", "") or "").strip().lower()
        exec_type = "local" if prov == "local" else "openai"
        updates["EXECUTOR_LLM_TYPE"] = exec_type

        work_dir = str(data.get("work_dir", "") or "").strip()
        if not work_dir:
            work_dir = "./workspace"
        work_dir_changed = _work_dir_restart_required(prev_vals.get("WORK_DIR", ""), work_dir)
        updates["WORK_DIR"] = work_dir

        ctx_raw = data.get("context_window", "")
        try:
            ctx_w = int(str(ctx_raw).strip()) if str(ctx_raw).strip() != "" else 128000
        except ValueError:
            ctx_w = 128000
        if ctx_w <= 0:
            ctx_w = 128000
        updates["CONTEXT_WINDOW"] = str(ctx_w)

        mot_raw = data.get("max_output_tokens", "")
        try:
            max_out = int(str(mot_raw).strip()) if str(mot_raw).strip() != "" else 8192
        except ValueError:
            max_out = 8192
        if max_out <= 0:
            max_out = 8192
        updates["MAX_OUTPUT_TOKENS"] = str(max_out)

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
        refresh_executor_client_from_env()
        return {"ok": True, "restart_required": work_dir_changed}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
from fastapi.responses import RedirectResponse as _RedirectResponse
@fastapi_app.middleware("http")
async def _config_check(req: _Request, call_next):
    p = req.url.path
    if p in (
        "/setup",
        "/setup/env",
        "/setup/mcp",
        "/api/save_config",
        "/api/env",
        "/api/mcp_config",
        "/api/pick-path",
    ) or p.startswith("/static/"):
        return await call_next(req)
    if not _is_configured():
        return _RedirectResponse(url="/setup")
    return await call_next(req)

