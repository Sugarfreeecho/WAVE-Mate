from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, AsyncGenerator, Deque, Dict, Set


_subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
_recent_ephemeral: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=400))
_lock = asyncio.Lock()


def _sid(session_id: str) -> str:
    return str(session_id or "").strip()


async def publish_session_event(session_id: str, event: Dict[str, Any]) -> None:
    sid = _sid(session_id)
    if not sid or not isinstance(event, dict):
        return
    async with _lock:
        if event.get("ephemeral"):
            _recent_ephemeral[sid].append(dict(event))
        elif event.get("type") == "tool_call" and str(event.get("tool_call_id") or "").strip():
            _prune_recent_ephemeral_unlocked(
                sid,
                types={"tool_pending", "tool_call_delta", "tool_command_delta"},
                react_iter=event.get("react_iter"),
                tool_call_id=event.get("tool_call_id"),
            )
        subscribers = list(_subscribers.get(sid, ()))
    for q in subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _prune_recent_ephemeral_unlocked(
    sid: str,
    *,
    types: set[str] | None = None,
    react_iter: Any = None,
    tool_call_id: Any = None,
) -> None:
    bucket = _recent_ephemeral.get(sid)
    if not bucket:
        return
    wanted_types = set(types or ())
    iter_filter = None
    try:
        if react_iter is not None:
            iter_filter = int(react_iter)
    except (TypeError, ValueError):
        iter_filter = None
    tool_filter = str(tool_call_id or "").strip()

    kept = []
    for ev in bucket:
        ev_type = str(ev.get("type") or "")
        if wanted_types and ev_type not in wanted_types:
            kept.append(ev)
            continue
        if iter_filter is not None:
            try:
                if int(ev.get("react_iter")) != iter_filter:
                    kept.append(ev)
                    continue
            except (TypeError, ValueError):
                kept.append(ev)
                continue
        if tool_filter:
            ev_tool_id = str(ev.get("tool_call_id") or ev.get("id") or "").strip()
            if ev_tool_id and ev_tool_id != tool_filter:
                kept.append(ev)
                continue
        continue

    bucket.clear()
    bucket.extend(kept)


async def prune_session_ephemeral(
    session_id: str,
    *,
    types: set[str] | None = None,
    react_iter: Any = None,
    tool_call_id: Any = None,
) -> None:
    sid = _sid(session_id)
    if not sid:
        return
    async with _lock:
        _prune_recent_ephemeral_unlocked(
            sid,
            types=types,
            react_iter=react_iter,
            tool_call_id=tool_call_id,
        )


async def close_session_stream(session_id: str) -> None:
    sid = _sid(session_id)
    if not sid:
        return
    async with _lock:
        subscribers = list(_subscribers.get(sid, ()))
        _recent_ephemeral.pop(sid, None)
    for q in subscribers:
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass


async def subscribe_session_events(
    session_id: str,
    replay_recent: bool = True,
) -> AsyncGenerator[dict | None, None]:
    sid = _sid(session_id)
    if not sid:
        return
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    async with _lock:
        if replay_recent:
            for ev in list(_recent_ephemeral.get(sid, ())):
                q.put_nowait(ev)
        _subscribers[sid].add(q)
    try:
        while True:
            item = await q.get()
            yield item
            if item is None:
                break
    finally:
        async with _lock:
            bucket = _subscribers.get(sid)
            if bucket:
                bucket.discard(q)
                if not bucket:
                    _subscribers.pop(sid, None)
