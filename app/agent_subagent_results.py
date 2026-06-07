"""
agent_subagent_results — subagent 结果/通知的共享格式。
"""

from __future__ import annotations

from typing import Any, Dict


TERMINAL_SUBAGENT_STATUSES = frozenset({"completed", "failed", "interrupted"})


def format_pending_subagent_notification(item: Dict[str, Any]) -> str:
    """将 pending_subagent_results.json 的一行格式化为父 Agent 可读通知。"""
    if not isinstance(item, dict):
        return ""
    status = str(item.get("status") or "").strip()
    if status not in TERMINAL_SUBAGENT_STATUSES:
        return ""
    aid = str(item.get("agent_id") or "")
    desc = str(item.get("description") or "")
    result = str(item.get("result") or "").strip()
    error = str(item.get("error") or "").strip()
    body = result or (f"Error: {error}" if error else "")
    if not body:
        return ""
    output_file = str(item.get("output_file") or "").strip()
    if output_file:
        body = f"{body}\nOutput file: {output_file}"
    label = "Subagent" if status == "completed" else f"Subagent {status}"
    return f"- {label} {aid} ({desc}): {body[:4000]}"
