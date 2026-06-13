from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .run_registry import RunRegistry, RunState


def parse_iso_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HealthMonitor:
    def __init__(self, registry: RunRegistry):
        self.registry = registry

    def stale_runs(self, max_age_seconds: float) -> List[RunState]:
        now = datetime.now(timezone.utc)
        stale: List[RunState] = []
        for run in self.registry.active_runs():
            try:
                age = (now - parse_iso_z(run.heartbeat_at)).total_seconds()
            except Exception:
                age = max_age_seconds + 1
            if age > max_age_seconds:
                stale.append(run)
        return stale
