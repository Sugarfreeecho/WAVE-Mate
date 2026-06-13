from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class PermissionDecision:
    action: str
    reason: str = ""


class PermissionManager:
    """Minimal allow/deny/ask rule table inspired by Claude Code permissions."""

    def __init__(self):
        self._rules: Dict[Tuple[str, str], str] = {}

    def set_rule(self, scope: str, tool_name: str, action: str) -> None:
        if action not in {"allow", "deny", "ask"}:
            raise ValueError("permission action must be allow, deny, or ask")
        self._rules[(scope, tool_name)] = action

    def check(self, scope: str, tool_name: str) -> PermissionDecision:
        action = self._rules.get((scope, tool_name)) or self._rules.get(("*", tool_name)) or "ask"
        return PermissionDecision(action=action, reason=f"{scope}:{tool_name}")
