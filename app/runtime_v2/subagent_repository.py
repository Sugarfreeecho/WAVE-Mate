from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .event_schema import now_iso


@dataclass
class SubagentState:
    agent_id: str
    parent_session_id: str
    session_id: str
    status: str
    has_final: bool = False
    result_consumed: bool = False
    started_at: str = ""
    finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "parent_session_id": self.parent_session_id,
            "session_id": self.session_id,
            "status": self.status,
            "has_final": self.has_final,
            "result_consumed": self.result_consumed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SubagentRepository:
    def __init__(self):
        self._items: Dict[str, SubagentState] = {}

    def start(self, agent_id: str, parent_session_id: str, session_id: str) -> SubagentState:
        state = SubagentState(
            agent_id=agent_id,
            parent_session_id=parent_session_id,
            session_id=session_id,
            status="running",
            started_at=now_iso(),
        )
        self._items[agent_id] = state
        return state

    def finish(self, agent_id: str, has_final: bool) -> Optional[SubagentState]:
        state = self._items.get(agent_id)
        if not state:
            return None
        state.status = "finished" if has_final else "failed"
        state.has_final = bool(has_final)
        state.finished_at = now_iso()
        return state

    def consume_result(self, agent_id: str) -> Optional[SubagentState]:
        state = self._items.get(agent_id)
        if not state:
            return None
        state.result_consumed = True
        return state
