"""Runtime V2 sidecar components.

This package is intentionally not wired into the existing MyAgent request path
yet. It provides a tested event-log and run-state core that can be mirrored
from the old runtime before taking over selected endpoints.
"""

from .event_schema import RuntimeEvent, now_iso
from .event_log import SessionEventLog
from .run_registry import RunRegistry
from .stream_publisher import StreamPublisher
from .gateway import RuntimeGateway
from .projector import RuntimeProjector
from .snapshot_store import SnapshotStore
from .mirror import RuntimeMirror
from .history_ops import RuntimeHistoryOps
from .blob_store import BlobStore
from .subagent_store import RuntimeSubagentStore

__all__ = [
    "RuntimeEvent",
    "now_iso",
    "SessionEventLog",
    "RunRegistry",
    "StreamPublisher",
    "RuntimeGateway",
    "RuntimeProjector",
    "SnapshotStore",
    "RuntimeMirror",
    "RuntimeHistoryOps",
    "BlobStore",
    "RuntimeSubagentStore",
]
