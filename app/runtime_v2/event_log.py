from __future__ import annotations

import json
import os
import threading
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .event_schema import RuntimeEvent


class SessionEventLog:
    """Append-only per-session JSONL event log.

    The log is the V2 fact source. Metadata, snapshots, and indexes should be
    treated as rebuildable projections.
    """

    def __init__(self, root: os.PathLike[str] | str):
        self.root = Path(root)
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def session_dir(self, session_id: str) -> Path:
        safe_id = self._validate_session_id(session_id)
        return self.root / safe_id

    def event_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "events.jsonl"

    def append(self, session_id: str, event_type: str, payload: Optional[dict] = None, run_id: Optional[str] = None) -> RuntimeEvent:
        with self._lock_for(session_id):
            seq = self.next_seq(session_id)
            event = RuntimeEvent(
                seq=seq,
                type=event_type,
                session_id=session_id,
                run_id=run_id,
                payload=payload or {},
            )
            path = self.event_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")
                fh.flush()
            return event

    def append_event(self, event: RuntimeEvent) -> RuntimeEvent:
        with self._lock_for(event.session_id):
            expected = self.next_seq(event.session_id)
            if event.seq != expected:
                event = RuntimeEvent(
                    seq=expected,
                    type=event.type,
                    session_id=event.session_id,
                    timestamp=event.timestamp,
                    run_id=event.run_id,
                    payload=event.payload,
                )
            path = self.event_path(event.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")
                fh.flush()
            return event

    def read_all(self, session_id: str) -> List[RuntimeEvent]:
        return list(self.iter_events(session_id))

    def read_after_seq(self, session_id: str, after_seq: int) -> List[RuntimeEvent]:
        return [ev for ev in self.iter_events(session_id) if ev.seq > after_seq]

    def read_latest(self, session_id: str, limit: int) -> List[RuntimeEvent]:
        limit = max(0, int(limit))
        if limit <= 0:
            return []
        rows = deque(maxlen=limit)
        for ev in self.iter_events(session_id):
            rows.append(ev)
        return list(rows)

    def read_before_seq(self, session_id: str, before_seq: int, limit: int) -> List[RuntimeEvent]:
        before = int(before_seq)
        limit = max(0, int(limit))
        if limit <= 0:
            return []
        rows = deque(maxlen=limit)
        for ev in self.iter_events(session_id):
            if ev.seq < before:
                rows.append(ev)
        return list(rows)

    def iter_events(self, session_id: str) -> Iterable[RuntimeEvent]:
        path = self.event_path(session_id)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield RuntimeEvent.from_dict(json.loads(line))

    def next_seq(self, session_id: str) -> int:
        last = 0
        path = self.event_path(session_id)
        if not path.exists():
            return 1
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = RuntimeEvent.from_dict(json.loads(line))
                except Exception:
                    continue
                if event.seq > last:
                    last = event.seq
        return last + 1

    def repair(self, session_id: str) -> Dict[str, int]:
        """Rewrite the log with valid, monotonic events and skip bad lines."""
        with self._lock_for(session_id):
            path = self.event_path(session_id)
            if not path.exists():
                return {"kept": 0, "dropped": 0}
            kept: List[RuntimeEvent] = []
            dropped = 0
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = RuntimeEvent.from_dict(json.loads(line))
                    except Exception:
                        dropped += 1
                        continue
                    kept.append(ev)
            repaired: List[RuntimeEvent] = []
            for index, ev in enumerate(kept, start=1):
                repaired.append(RuntimeEvent(
                    seq=index,
                    type=ev.type,
                    session_id=ev.session_id,
                    timestamp=ev.timestamp,
                    run_id=ev.run_id,
                    payload=ev.payload,
                ))
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                for ev in repaired:
                    fh.write(json.dumps(ev.to_dict(), ensure_ascii=False, separators=(",", ":")))
                    fh.write("\n")
            tmp.replace(path)
            return {"kept": len(repaired), "dropped": dropped}

    def _lock_for(self, session_id: str) -> threading.Lock:
        safe_id = self._validate_session_id(session_id)
        with self._locks_guard:
            lock = self._locks.get(safe_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[safe_id] = lock
            return lock

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        safe_id = str(session_id or "").strip()
        if not safe_id:
            raise ValueError("session_id is required")
        if any(part in safe_id for part in ("/", "\\", "..")):
            raise ValueError("session_id contains invalid path characters")
        return safe_id
