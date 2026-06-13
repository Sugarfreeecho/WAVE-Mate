from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from .event_schema import now_iso


TERMINAL_STATUSES = {"finished", "failed", "interrupted"}


@dataclass
class RunState:
    run_id: str
    session_id: str
    status: str
    started_at: str
    heartbeat_at: str
    finished_at: Optional[str] = None
    error: Optional[str] = None
    interrupt_requested: bool = False

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "interrupt_requested": self.interrupt_requested,
            "run_active": self.status not in TERMINAL_STATUSES,
        }


class RunRegistry:
    def __init__(self):
        self._runs: Dict[str, RunState] = {}
        self._session_current: Dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, session_id: str, run_id: str) -> RunState:
        ts = now_iso()
        with self._lock:
            state = RunState(
                run_id=run_id,
                session_id=session_id,
                status="running",
                started_at=ts,
                heartbeat_at=ts,
            )
            self._runs[run_id] = state
            self._session_current[session_id] = run_id
            return state

    def heartbeat(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return None
            state.heartbeat_at = now_iso()
            return state

    def request_interrupt(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return None
            state.interrupt_requested = True
            return state

    def finish(self, run_id: str) -> Optional[RunState]:
        return self._terminal(run_id, "finished")

    def fail(self, run_id: str, error: str) -> Optional[RunState]:
        return self._terminal(run_id, "failed", error=error)

    def interrupt(self, run_id: str) -> Optional[RunState]:
        return self._terminal(run_id, "interrupted")

    def get(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            return self._runs.get(run_id)

    def current_for_session(self, session_id: str) -> Optional[RunState]:
        with self._lock:
            run_id = self._session_current.get(session_id)
            if not run_id:
                return None
            return self._runs.get(run_id)

    def active_runs(self) -> List[RunState]:
        with self._lock:
            return [state for state in self._runs.values() if state.status not in TERMINAL_STATUSES]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active_runs": [state.to_dict() for state in self._runs.values() if state.status not in TERMINAL_STATUSES],
                "runs": [state.to_dict() for state in self._runs.values()],
            }

    def _terminal(self, run_id: str, status: str, error: Optional[str] = None) -> Optional[RunState]:
        ts = now_iso()
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return None
            state.status = status
            state.finished_at = ts
            state.heartbeat_at = ts
            state.error = error
            if self._session_current.get(state.session_id) == run_id:
                self._session_current.pop(state.session_id, None)
            return state
