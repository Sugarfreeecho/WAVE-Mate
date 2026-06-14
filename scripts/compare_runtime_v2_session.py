from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from runtime_v2 import SessionEventLog, RuntimeProjector  # noqa: E402


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def legacy_chain(events: list[dict]) -> list[dict]:
    rows = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "user":
            rows.append({"source": "legacy", "index": index, "role": "user", "content": str(event.get("content") or "")})
        elif event_type == "final":
            rows.append({"source": "legacy", "index": index, "role": "assistant", "content": str(event.get("content") or "")})
    return rows


def v2_chain(session_root: Path, session_id: str) -> list[dict]:
    event_log = SessionEventLog(session_root)
    snapshot = RuntimeProjector().project(event_log.read_all(session_id))
    rows = []
    for message in snapshot.get("visible_messages") or []:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        payload = message.get("payload") or {}
        rows.append({
            "source": "runtime_v2",
            "seq": message.get("seq"),
            "role": role,
            "content": str(payload.get("content") or ""),
            "rewritten": bool(message.get("rewritten")),
        })
    return rows


def signature(row: dict) -> tuple[str, str]:
    return (str(row.get("role") or ""), str(row.get("content") or ""))


def compare(legacy_rows: list[dict], v2_rows: list[dict]) -> dict:
    legacy_sigs = [signature(row) for row in legacy_rows]
    v2_sigs = [signature(row) for row in v2_rows]
    legacy_counter = Counter(legacy_sigs)
    v2_counter = Counter(v2_sigs)
    missing_in_v2 = list((legacy_counter - v2_counter).elements())
    extra_in_v2 = list((v2_counter - legacy_counter).elements())
    order_matches = legacy_sigs == v2_sigs
    duplicates_legacy = [sig for sig, count in legacy_counter.items() if count > 1]
    duplicates_v2 = [sig for sig, count in v2_counter.items() if count > 1]
    return {
        "legacy_count": len(legacy_rows),
        "runtime_v2_count": len(v2_rows),
        "order_matches": order_matches,
        "missing_in_runtime_v2": [{"role": r, "content": c} for r, c in missing_in_v2],
        "extra_in_runtime_v2": [{"role": r, "content": c} for r, c in extra_in_v2],
        "duplicates_legacy": [{"role": r, "content": c} for r, c in duplicates_legacy],
        "duplicates_runtime_v2": [{"role": r, "content": c} for r, c in duplicates_v2],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare legacy ui_events.json with Runtime V2 projection.")
    parser.add_argument("session_id")
    parser.add_argument("--sessions-dir", default=str(ROOT / "workspace" / "sessions"))
    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir)
    session_dir = sessions_dir / args.session_id
    legacy_rows = legacy_chain(load_json_list(session_dir / "ui_events.json"))
    v2_rows = v2_chain(sessions_dir, args.session_id)
    result = compare(legacy_rows, v2_rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
