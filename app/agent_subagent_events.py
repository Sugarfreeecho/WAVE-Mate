"""
agent_subagent_events — subagent/UI 事件持久化与转发口径。

这里集中处理“哪些事件属于当前 session 的可回放 UI 历史”，避免
agent_loop 与 agent_subagent 各自维护一份相似但不完全一致的过滤规则。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def is_low_value_subagent_ui_event(ev: Dict[str, Any]) -> bool:
    """子会话 UI 不持久化空白/循环标记状态，避免卡片里出现噪声。"""
    if not isinstance(ev, dict):
        return False
    et = str(ev.get("type") or "")
    content = str(ev.get("content") or "").strip()
    if et == "status" and (
        not content
        or content == "New Agent Loop Start"
        or content == "Loop finished"
        or content == "Subagent Continuation Start"
    ):
        return True
    if et in ("warning", "error") and not content:
        return True
    return False


def should_persist_ui_event(
    ev: Any,
    *,
    session_meta: Optional[Dict[str, Any]] = None,
    low_value_subagent_events: bool = False,
) -> bool:
    """是否把事件写入当前 session 的 ui_events。"""
    if not ev or not isinstance(ev, dict):
        return False
    if ev.get("ephemeral"):
        return False
    if ev.get("_subagent_forward"):
        return False
    meta = session_meta if isinstance(session_meta, dict) else {}
    if (
        low_value_subagent_events
        or bool(meta.get("is_subagent"))
    ) and is_low_value_subagent_ui_event(ev):
        return False
    return True


def tag_subagent_forward_event(ev: Dict[str, Any], *, agent_id: str) -> Dict[str, Any]:
    """将子会话事件标记为向父级实时转发的事件。"""
    tagged = dict(ev)
    if not (tagged.get("_subagent_forward") and tagged.get("agent_id")):
        tagged["agent_id"] = agent_id
    tagged["_subagent_forward"] = True
    return tagged
