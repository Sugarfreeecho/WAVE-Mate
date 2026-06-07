"""
Web UI 对工作区放宽类工具的前端确认闸门（run_shell restrict_to_workspace=False、web_download）。

/chat SSE 在服务侧 await 此处，直到浏览器 POST /sessions/{id}/tool-approval，
或会话 interrupt、超时。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Dict, Tuple

from agent_harness import session_manager

_PENDING: Dict[Tuple[str, str], asyncio.Future] = {}

_WAIT_SEC = float(os.getenv("TOOL_UI_APPROVAL_WAIT_SEC", "3600"))


def new_approval_id() -> str:
    return uuid.uuid4().hex


def reject_pending_approvals_for_sessions(session_ids) -> None:
    ids = {(s or "").strip() for s in session_ids if (s or "").strip()}
    if not ids:
        return
    for (sid, _aid), fut in list(_PENDING.items()):
        if sid in ids and not fut.done():
            fut.set_result(False)


def resolve_tool_approval(session_id: str, approval_id: str, approved: bool) -> bool:
    """由 HTTP 路由调用：释放等待中的 Future。"""
    sid = str(session_id or "").strip()
    aid = str(approval_id or "").strip()
    if not sid or not aid:
        return False
    fut = _PENDING.get((sid, aid))
    if not fut or fut.done():
        return False
    fut.set_result(bool(approved))
    return True


async def _interrupt_poll_until_done(session_id: str, fut: asyncio.Future) -> None:
    try:
        while not fut.done():
            await asyncio.sleep(0.25)
            try:
                if session_manager.is_interrupt_requested(session_id):
                    if not fut.done():
                        fut.set_result(False)
                    return
            except Exception:
                pass
    except asyncio.CancelledError:
        return


async def wait_tool_ui_approval_after_emit(
    session_id: str,
    approval_id: str,
    emit_coro,
) -> bool:
    """先登记 Future，再执行 emit_coro（发送 SSE），避免客户端极快 POST 时未命中 pending。"""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    key = (str(session_id or "").strip(), str(approval_id or "").strip())
    if not key[0] or not key[1]:
        return False
    _PENDING[key] = fut
    poll = asyncio.create_task(_interrupt_poll_until_done(session_id, fut))
    try:
        await emit_coro()
        return await asyncio.wait_for(fut, timeout=max(30.0, _WAIT_SEC))
    except asyncio.TimeoutError:
        if not fut.done():
            fut.set_result(False)
        return False
    finally:
        poll.cancel()
        try:
            await poll
        except asyncio.CancelledError:
            pass
        _PENDING.pop(key, None)