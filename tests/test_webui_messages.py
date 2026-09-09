import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class _FakeRepository:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir


class _FakeSessionManager:
    def __init__(self, sessions_dir: Path, events: list[dict]):
        self.repository = _FakeRepository(sessions_dir)
        self.events = list(events)
        self.llm_history: list[dict] = []
        self.saved_llm_history: list[list[dict]] = []
        self.saved_ui_events: list[list[dict]] = []
        self.page_calls: list[dict] = []
        self.display_calls = 0
        self.count_calls = 0
        self.truncate_calls: list[dict] = []
        self.branch_calls: list[dict] = []
        self.list_calls: list[dict] = []

    def _resolve_session_path(self, session_id: str) -> Path:
        return self.repository.sessions_dir / session_id

    def get_ui_event_count(self, session_id: str) -> int:
        self.count_calls += 1
        return len(self.events)

    def get_ui_events_for_display(self, session_id: str) -> list[dict]:
        self.display_calls += 1
        return [dict(event) for event in self.events]

    def _save_ui_events(self, session_id: str, events: list[dict]) -> None:
        self.saved_ui_events.append([dict(event) for event in events])
        self.events = [dict(event) for event in events]

    def _load_llm_history(self, session_id: str) -> list[dict]:
        return [dict(item) for item in self.llm_history]

    def _save_llm_history(self, session_id: str, llm_history: list[dict]) -> None:
        saved = [dict(item) for item in llm_history]
        self.saved_llm_history.append(saved)
        self.llm_history = saved

    def get_ui_events_page(self, session_id: str, limit: int = 200, before_index=None, turns=None) -> dict:
        self.page_calls.append({
            "session_id": session_id,
            "limit": limit,
            "before_index": before_index,
            "turns": turns,
        })
        return {
            "events": [dict(event) for event in self.events[-2:]],
            "total": len(self.events),
            "range_start": max(0, len(self.events) - 2),
            "range_end": len(self.events),
            "has_older": len(self.events) > 2,
            "has_newer": False,
            "source": "fake_legacy_page",
        }

    def truncate_session_at_event_index(
        self,
        session_id: str,
        before_index: int,
        *,
        truncate_before_seq=None,
        create_backup: bool = True,
    ) -> bool:
        self.truncate_calls.append({
            "session_id": session_id,
            "before_index": before_index,
            "truncate_before_seq": truncate_before_seq,
            "create_backup": create_backup,
        })
        return True

    def branch_session_at_event_index(
        self,
        session_id: str,
        before_index: int,
        *,
        branch_after_seq=None,
    ) -> dict:
        self.branch_calls.append({
            "session_id": session_id,
            "before_index": before_index,
            "branch_after_seq": branch_after_seq,
        })
        return {"ok": True, "session_id": "branch-1", "name": "branch"}

    def list_sessions(self, include_archived: bool = False) -> list[dict]:
        self.list_calls.append({"include_archived": include_archived})
        return [{
            "id": "s1",
            "name": "Session 1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "archived": False,
        }]

    def archived_session_count(self) -> int:
        return 0


class _NoLegacyUiSessionManager(_FakeSessionManager):
    def get_ui_event_count(self, session_id: str) -> int:
        raise AssertionError("Runtime V2 messages path must not read legacy UI count")

    def get_ui_events_for_display(self, session_id: str) -> list[dict]:
        raise AssertionError("Runtime V2 messages path must not read legacy UI events")

    def get_ui_events_page(self, session_id: str, limit: int = 200, before_index=None, turns=None) -> dict:
        raise AssertionError("Runtime V2 messages path must not read legacy UI page")

    def get_ui_user_turns_for_toc(self, session_id: str) -> list[dict]:
        raise AssertionError("Runtime V2 user turns must not read legacy TOC data")

    def get_todo_plan_snapshot(self, session_id: str) -> dict:
        raise AssertionError("Runtime V2 todo plan path must not read legacy todo/key_context files")

    def list_subagents_flat(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("Runtime V2 subagent list path must not read legacy subagent sessions")

    def list_subagent_tasks(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("Runtime V2 subagent list path must not read legacy subagent task index")


def _json_response_payload(response) -> dict | list:
    return json.loads(response.body.decode("utf-8"))


def test_create_session_moves_filesystem_work_off_event_loop(monkeypatch):
    import webui

    caller_thread = threading.get_ident()
    observed = {}

    class _CreateManager:
        def get_or_create_session(self):
            observed["thread_id"] = threading.get_ident()
            metadata = {
                "name": "新会话",
                "created_at": "2026-09-08T12:00:00",
                "updated_at": "2026-09-08T12:00:00",
            }
            return "new-session", [], [], [], "", metadata

    monkeypatch.setattr(webui, "session_manager", _CreateManager())
    invalidations = []
    monkeypatch.setattr(webui, "_invalidate_sessions_state_cache", lambda: invalidations.append(True))

    response = asyncio.run(webui.create_session())
    payload = _json_response_payload(response)

    assert observed["thread_id"] != caller_thread
    assert payload["session_id"] == "new-session"
    assert payload["session"]["id"] == "new-session"
    assert response.headers["server-timing"].startswith("session-create;dur=")
    assert invalidations == [True]


def test_clipboard_upload_returns_insertable_workspace_path(monkeypatch, tmp_path):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    import webui

    monkeypatch.setattr(webui, "WORK_DIR", tmp_path)
    upload = UploadFile(filename="clipboard-image.png", file=BytesIO(b"\x89PNG\r\nclipboard"))

    response = asyncio.run(webui.upload_chat_files([upload]))
    payload = _json_response_payload(response)

    assert payload["ok"] is True
    assert len(payload["files"]) == 1
    saved = payload["files"][0]
    assert saved["name"] == "clipboard-image.png"
    assert saved["rel"].replace("\\", "/").startswith("uploads/chat/")
    path = Path(saved["path"])
    assert path.is_file()
    assert path.read_bytes() == b"\x89PNG\r\nclipboard"


def test_clipboard_upload_rejects_oversized_file_and_removes_partial_output(monkeypatch, tmp_path):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    import webui

    monkeypatch.setattr(webui, "WORK_DIR", tmp_path)
    monkeypatch.setattr(webui, "CHAT_UPLOAD_MAX_FILE_BYTES", 8)
    monkeypatch.setattr(webui, "CHAT_UPLOAD_MAX_TOTAL_BYTES", 32)
    upload = UploadFile(filename="too-large.bin", file=BytesIO(b"123456789"))

    response = asyncio.run(webui.upload_chat_files([upload]))
    payload = _json_response_payload(response)

    assert response.status_code == 413
    assert payload["ok"] is False
    assert not list((tmp_path / "uploads").rglob("too-large.bin"))


def test_clipboard_upload_rejects_oversized_batch_and_cleans_prior_files(monkeypatch, tmp_path):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    import webui

    monkeypatch.setattr(webui, "WORK_DIR", tmp_path)
    monkeypatch.setattr(webui, "CHAT_UPLOAD_MAX_FILE_BYTES", 16)
    monkeypatch.setattr(webui, "CHAT_UPLOAD_MAX_TOTAL_BYTES", 10)
    uploads = [
        UploadFile(filename="first.bin", file=BytesIO(b"123456")),
        UploadFile(filename="second.bin", file=BytesIO(b"abcdef")),
    ]

    response = asyncio.run(webui.upload_chat_files(uploads))
    payload = _json_response_payload(response)

    assert response.status_code == 413
    assert payload["ok"] is False
    assert not list((tmp_path / "uploads").rglob("*.bin"))


def test_clipboard_upload_middleware_rejects_oversized_request_before_routing(monkeypatch):
    from types import SimpleNamespace
    import webui

    monkeypatch.setattr(webui, "CHAT_UPLOAD_MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(webui, "_CHAT_UPLOAD_MULTIPART_OVERHEAD_BYTES", 2)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/upload-chat-files"),
        headers={"content-length": "13"},
    )
    routed = False

    async def call_next(_request):
        nonlocal routed
        routed = True
        raise AssertionError("oversized request should not reach routing")

    response = asyncio.run(webui._config_check(request, call_next))
    payload = _json_response_payload(response)

    assert response.status_code == 413
    assert payload["ok"] is False
    assert routed is False


def test_messages_turn_page_prefers_runtime_v2_projection(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    mirror = RuntimeMirror(tmp_path)
    for event in [
        {"type": "user", "content": "u0"},
        {"type": "final", "content": "a0"},
        {"type": "user", "content": "u1"},
        {"type": "final", "content": "a1"},
    ]:
        mirror.mirror_ui_event("s1", event)
    fake = _FakeSessionManager(tmp_path, [
        {"type": "user", "content": "legacy"},
    ])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=5,
    ))
    payload = _json_response_payload(response)

    assert payload["source"] == "runtime_v2_seq_index"
    assert payload["total"] == 4
    assert [event["content"] for event in payload["events"]] == ["u0", "a0", "u1", "a1"]
    assert fake.page_calls == []
    assert fake.display_calls == 0


def test_messages_full_read_prefers_runtime_v2_projection(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u0"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "a0"})
    fake = _FakeSessionManager(tmp_path, [
        {"type": "user", "content": "legacy"},
    ])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=None,
    ))
    payload = _json_response_payload(response)

    assert [event["content"] for event in payload] == ["u0", "a0"]
    assert fake.display_calls == 0
    assert fake.page_calls == []


def test_messages_open_does_not_auto_sync_in_runtime_v2(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setenv("RUNTIME_SYNC_ON_MESSAGES_OPEN", "1")
    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(
        webui,
        "_enqueue_runtime_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Runtime V2 messages open must not auto-sync legacy history")
        ),
    )
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u0"})
    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=None,
    ))
    payload = _json_response_payload(response)

    assert [event["content"] for event in payload] == ["u0"]


def test_message_count_prefers_runtime_v2_projection(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u0"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "a0"})
    fake = _FakeSessionManager(tmp_path, [
        {"type": "user", "content": "legacy"},
    ])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_message_count("s1"))
    payload = _json_response_payload(response)

    assert payload == {"count": 2, "source": "runtime_v2"}
    assert fake.count_calls == 0


def test_runtime_v2_message_projection_failure_is_not_an_empty_session(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _BrokenProjection:
        def __init__(self, *args, **kwargs):
            pass

        def read_ui_page(self, *_args, **_kwargs):
            raise ValueError("duplicate runtime sequence")

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _BrokenProjection)
    monkeypatch.setattr(webui, "session_manager", _NoLegacyUiSessionManager(tmp_path, []))

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=20,
        before_index=None,
        after_index=None,
        target_index=None,
        turns=5,
    ))
    payload = _json_response_payload(response)

    assert response.status_code == 500
    assert payload["error"] == "runtime_v2_projection_failed"
    assert payload["repair_required"] is True
    assert "duplicate runtime sequence" in payload["detail"]
    assert "events" not in payload


def test_runtime_v2_message_count_failure_is_not_zero(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _BrokenProjection:
        def __init__(self, *args, **kwargs):
            pass

        def count_ui_events_light(self, *_args, **_kwargs):
            raise ValueError("non-monotonic runtime sequence")

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _BrokenProjection)
    monkeypatch.setattr(webui, "session_manager", _NoLegacyUiSessionManager(tmp_path, []))

    response = asyncio.run(webui.get_session_message_count("s1"))
    payload = _json_response_payload(response)

    assert response.status_code == 500
    assert payload["error"] == "runtime_v2_projection_failed"
    assert payload["repair_required"] is True
    assert "count" not in payload


def test_user_turns_prefers_runtime_v2_projection(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "first question"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "answer"})
    mirror.mirror_ui_event("s1", {"type": "user", "content": "second question"})
    fake = _NoLegacyUiSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_user_turns("s1"))
    payload = _json_response_payload(response)

    assert payload == [
        {"event_index": 0, "preview": "first question"},
        {"event_index": 2, "preview": "second question"},
    ]


def test_history_snapshot_combines_v2_messages_count_and_toc(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    mirror = RuntimeMirror(tmp_path)
    for event in [
        {"type": "user", "content": "first question"},
        {"type": "final", "content": "first answer"},
        {
            "type": "todo_plan",
            "has_plan": True,
            "items": [{"id": "1", "text": "task", "status": "pending"}],
            "done": 0,
            "total": 1,
        },
        {"type": "user", "content": "second question"},
        {"type": "final", "content": "second answer"},
    ]:
        mirror.mirror_ui_event("s1", event)
    mirror.mirror_ui_event("s1", {
        "type": "context_tokens",
        "estimated": 1234,
        "threshold": 4096,
        "token_source": "provider_exact",
    })
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(
        webui,
        "_session_run_state_fields_light",
        lambda _sid: {
            "stream_active": True,
            "run_active": True,
            "run_started_at": "2026-09-09T00:00:00Z",
            "active_run": {
                "session_id": "s1",
                "run_id": "run-history",
                "run_active": True,
                "phase": "finalizing",
                "runtime_v2": True,
            },
        },
    )

    response = asyncio.run(webui.get_session_history_snapshot(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=5,
    ))
    payload = _json_response_payload(response)

    assert payload["ok"] is True
    assert payload["source"] == "runtime_v2_snapshot"
    assert payload["count"] == 5
    assert payload["count_source"] == "runtime_v2_page"
    assert payload["elapsed_ms"] >= 0
    assert set(payload["timing"]) == {"read_page", "count", "user_turns", "context_tokens", "todo_plan", "total"}
    assert payload["timing"]["total"] >= 0
    assert payload["messages"]["source"] == "runtime_v2_seq_index"
    assert [event["content"] for event in payload["messages"]["events"] if event.get("content")] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ]
    assert payload["user_turns"] == [
        {"event_index": 0, "preview": "first question"},
        {"event_index": 2, "preview": "second question"},
    ]
    assert payload["todo_plan"]["source"] == "extension_state"
    assert payload["todo_plan"]["items"][0]["text"] == "task"
    assert payload["context_tokens"]["estimated"] == 1234
    assert payload["context_tokens"]["token_source"] == "provider_exact"
    assert payload["active_run"]["run_id"] == "run-history"
    assert payload["active_run"]["phase"] == "finalizing"


def test_history_snapshot_uses_lightweight_user_turns(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _Projection:
        def __init__(self, *args, **kwargs):
            pass

        def read_ui_page(self, session_id, **kwargs):
            return {
                "events": [{"type": "user", "content": "u"}],
                "total": 1,
                "range_start": 0,
                "range_end": 1,
                "has_older": False,
                "has_newer": False,
                "source": "test",
            }

        def count_ui_events_light(self, session_id):
            raise AssertionError("history snapshot should reuse read_ui_page total when available")

        def read_user_turns_light(self, session_id):
            return [{"event_index": 0, "preview": "u"}]

        def read_ui_events_fast(self, session_id):
            raise AssertionError("history snapshot must not read full UI events for TOC")

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _Projection)
    fake = _NoLegacyUiSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_history_snapshot(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=5,
    ))
    payload = _json_response_payload(response)

    assert payload["ok"] is True
    assert payload["user_turns"] == [{"event_index": 0, "preview": "u"}]
    assert set(payload["timing"]) == {"read_page", "count", "user_turns", "context_tokens", "todo_plan", "total"}


def test_history_snapshot_can_defer_auxiliary_snapshot_until_after_first_paint(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _Projection:
        def __init__(self, *args, **kwargs):
            pass

        def read_ui_page(self, session_id, **kwargs):
            return {
                "events": [{"type": "user", "content": "u"}],
                "total": 1,
                "range_start": 0,
                "range_end": 1,
                "has_older": False,
                "has_newer": False,
                "source": "test",
            }

        def read_user_turns_light(self, session_id):
            return [{"event_index": 0, "preview": "u"}]

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _Projection)
    monkeypatch.setattr(webui, "session_manager", _NoLegacyUiSessionManager(tmp_path, []))
    monkeypatch.setattr(
        webui,
        "_runtime_v2_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("auxiliary snapshot must stay off the initial history path")
        ),
    )

    response = asyncio.run(webui.get_session_history_snapshot(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=5,
        event_budget=500,
        include_aux=False,
    ))
    payload = _json_response_payload(response)

    assert payload["ok"] is True
    assert payload["context_tokens"] is None
    assert payload["todo_plan"] is None
    assert payload["timing"]["context_tokens"] == 0
    assert payload["timing"]["todo_plan"] == 0


def test_context_tokens_snapshot_miss_uses_runtime_v2_compute_not_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    class _NoLegacyContextSessionManager:
        def get_or_create_session(self, session_id):
            raise AssertionError("/context_tokens V2 fallback must not read legacy session history")

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(webui, "session_manager", _NoLegacyContextSessionManager())
    monkeypatch.setattr(webui, "_runtime_v2_context_snapshot", lambda _sid: {})
    monkeypatch.setattr(webui, "get_context_token_mode", lambda: "hybrid")
    tools = [{"type": "function", "function": {"name": "demo"}}]
    monkeypatch.setattr(
        webui,
        "build_combined_tool_definitions_for_session",
        lambda _sid: asyncio.sleep(0, result=tools),
    )
    monkeypatch.setattr(webui, "compute_context_tokens_for_session", lambda sid, defs: {
        "ok": True,
        "estimated": 321,
        "threshold": 4096,
        "model": "m",
        "source": "runtime_v2_projection",
        "tools_matched": defs == tools,
    })

    response = asyncio.run(webui.get_session_context_tokens("s1"))
    payload = _json_response_payload(response)

    assert payload == {
        "ok": True,
        "estimated": 321,
        "threshold": 4096,
        "model": "m",
        "source": "runtime_v2_projection",
        "tools_matched": True,
        "token_mode": "hybrid",
    }


def test_context_tokens_stale_provider_value_does_not_switch_to_local_scale(monkeypatch):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(webui, "get_context_token_mode", lambda: "hybrid")
    monkeypatch.setattr(webui, "_runtime_v2_context_snapshot", lambda _sid: {
        "tokens": {
            "estimated": 4321,
            "threshold": 8192,
            "token_source": "provider_exact",
            "stale": True,
            "stale_reason": "message_rewritten",
        }
    })
    monkeypatch.setattr(
        webui,
        "compute_context_tokens_for_session",
        lambda _sid: (_ for _ in ()).throw(AssertionError("must preserve provider scale")),
    )

    payload = _json_response_payload(asyncio.run(webui.get_session_context_tokens("s1")))

    assert payload["estimated"] == 4321
    assert payload["source"] == "runtime_v2_snapshot_stale"
    assert payload["pending_recalculation"] is True


def test_context_tokens_projection_failure_does_not_fall_back_to_local(monkeypatch):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(webui, "get_context_token_mode", lambda: "hybrid")
    monkeypatch.setattr(
        webui,
        "_runtime_v2_context_snapshot",
        lambda _sid: (_ for _ in ()).throw(OSError("corrupt projection")),
    )
    monkeypatch.setattr(
        webui,
        "compute_context_tokens_for_session",
        lambda _sid: (_ for _ in ()).throw(AssertionError("must fail closed")),
    )

    response = asyncio.run(webui.get_session_context_tokens("s1"))
    payload = _json_response_payload(response)

    assert response.status_code == 500
    assert payload["error"] == "runtime_v2_projection_failed"
    assert payload["repair_required"] is True


def test_user_turns_uses_lightweight_projection_index(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _Projection:
        def __init__(self, *args, **kwargs):
            pass

        def read_user_turns_light(self, session_id):
            return [{"event_index": 7, "preview": "cached"}]

        def read_ui_events_fast(self, session_id):
            raise AssertionError("/user_turns must not read full UI events")

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _Projection)
    fake = _NoLegacyUiSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_user_turns("s1"))
    payload = _json_response_payload(response)

    assert payload == [{"event_index": 7, "preview": "cached"}]


def test_todo_plan_prefers_runtime_v2_snapshot(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    RuntimeMirror(tmp_path).mirror_ui_event("s1", {
        "type": "todo_plan",
        "has_plan": True,
        "items": [{"id": "1", "text": "task", "status": "pending"}],
        "done": 0,
        "total": 1,
    })
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_todo_plan("s1"))
    payload = _json_response_payload(response)

    assert payload["source"] == "extension_state"
    assert payload["has_plan"] is True
    assert payload["items"][0]["text"] == "task"


def test_todo_plan_empty_runtime_v2_snapshot_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_todo_plan("s1"))
    payload = _json_response_payload(response)

    assert payload == {
        "has_plan": False,
        "items": [],
        "done": 0,
        "total": 0,
        "source": "runtime_v2_snapshot",
    }


def test_subagent_list_prefers_runtime_v2_store(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeMirror, RuntimeSubagentStore
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    store = RuntimeSubagentStore(tmp_path)
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("agent1", {"type": "user", "content": "task"})
    mirror.mirror_ui_event("agent1", {"type": "final", "content": "done"})
    output_path = store.write_task_output("s1", "agent1", "final text")
    store.upsert_task("s1", "agent1", {
        "status": "completed",
        "description": "worker",
        "subagent_type": "generalPurpose",
        "result_preview": "done",
    })
    fake = _NoLegacyUiSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = webui._build_session_subagents_response("s1", lite=True)
    payload = _json_response_payload(response)

    assert payload["source"] == "runtime_v2_subagents"
    assert len(payload["subagents"]) == 1
    node = payload["subagents"][0]
    assert node["id"] == "agent1"
    assert node["description"] == "worker"
    assert node["status"] == "completed"
    assert node["ok"] is True
    assert node["has_final"] is True
    assert node["event_count"] == 2
    assert node["output_file"] == output_path
    assert node["virtual_task"] is False


def test_subagent_list_marks_output_only_runtime_v2_task_virtual(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeSubagentStore
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    store = RuntimeSubagentStore(tmp_path)
    output_path = store.write_task_output("s1", "runner1", "combined result")
    store.upsert_task("s1", "runner1", {
        "status": "completed",
        "description": "best result",
        "subagent_type": "best-of-n-runner",
    })
    fake = _NoLegacyUiSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = webui._build_session_subagents_response("s1", lite=True)
    node = _json_response_payload(response)["subagents"][0]

    assert node["id"] == "runner1"
    assert node["output_file"] == output_path
    assert node["event_count"] == 0
    assert node["virtual_task"] is True


def test_empty_subagent_list_runtime_v2_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = webui._build_session_subagents_response("s1", lite=True)
    payload = _json_response_payload(response)

    assert payload == {
        "session_id": "s1",
        "subagents": [],
        "source": "runtime_v2_subagents",
    }


def test_manual_runtime_sync_exports_v2_model_projection_to_legacy(monkeypatch, tmp_path):
    import runtime_v2
    from runtime_v2 import RuntimeHistoryOps, RuntimeMirror
    import webui

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "visible"})
    RuntimeHistoryOps(tmp_path).replace_model_history(
        "s1",
        [
            {"type": "user", "content": "hello"},
            {"type": "assistant", "content": "answer"},
        ],
        reason="test",
    )
    fake = _FakeSessionManager(tmp_path, [{"type": "user", "content": "visible"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    result = webui._sync_runtime_session("s1")

    assert result["model_v2_to_v1"]["action"] == "skipped"
    assert fake.saved_llm_history == []

    result = webui._sync_runtime_session("s1", export_legacy=True)

    assert result["model_v2_to_v1"]["action"] == "replace"
    assert result["model_v2_to_v1"]["written"] == 2
    assert fake.saved_llm_history == [[
        {"type": "user", "content": "hello"},
        {"type": "assistant", "content": "answer"},
    ]]


def test_manual_runtime_sync_refuses_active_run(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: sid == "s1")

    with pytest.raises(webui.RuntimeSyncBusyError, match="active run"):
        webui._sync_runtime_session("s1")

    response = asyncio.run(webui.sync_session_runtime("s1", export_legacy=False))
    payload = _json_response_payload(response)
    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["busy"] is True


def test_runtime_sync_needed_skips_unchanged_verified_or_blocked_manifest(monkeypatch):
    import webui

    fingerprints = {
        "legacy_ui": {"exists": True, "mtime_ns": 1, "size": 10},
        "legacy_model": {"exists": True, "mtime_ns": 2, "size": 20},
        "legacy_context": {"exists": False, "mtime_ns": 0, "size": 0},
        "legacy_todo": {"exists": False, "mtime_ns": 0, "size": 0},
        "runtime_events": {"exists": True, "mtime_ns": 3, "size": 30},
    }
    monkeypatch.setattr(webui, "_runtime_sync_fingerprints", lambda _sid: dict(fingerprints))
    monkeypatch.setattr(webui, "_read_runtime_migration_manifest", lambda _sid: {
        "manifest_version": 2,
        "status": "completed",
        "file_fingerprints": dict(fingerprints),
    })

    assert webui._runtime_sync_needed("s1")[:2] == (False, "verified_unchanged")

    monkeypatch.setattr(webui, "_read_runtime_migration_manifest", lambda _sid: {
        "manifest_version": 2,
        "status": "blocked",
        "file_fingerprints": dict(fingerprints),
    })
    assert webui._runtime_sync_needed("s1")[:2] == (False, "blocked_unchanged")


def test_legacy_only_gate_enqueues_background_migration(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "_runtime_sync_fingerprints", lambda _sid: {
        "legacy_ui": {"exists": True, "mtime_ns": 1, "size": 10},
        "legacy_model": {"exists": False, "mtime_ns": 0, "size": 0},
        "legacy_context": {"exists": False, "mtime_ns": 0, "size": 0},
        "legacy_todo": {"exists": False, "mtime_ns": 0, "size": 0},
        "runtime_events": {"exists": False, "mtime_ns": 0, "size": 0},
    })
    calls = []
    monkeypatch.setattr(webui, "_enqueue_runtime_sync", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or {"ok": True, "queued": True, "reason": "runtime_missing"}
    ))

    result = webui._runtime_v2_legacy_only_migration_pending("s1")

    assert result["pending"] is True
    assert result["queued"] is True
    assert result["retry_after_ms"] == 250
    assert calls == [(('s1', 'auto_on_open'), {'check_needed': True})]


def test_runtime_sync_uses_session_history_operation_lock(monkeypatch):
    import webui

    entered = threading.Event()
    finished = threading.Event()

    def unlocked(session_id, *, export_legacy=False):
        entered.set()
        return {"ok": True, "session_id": session_id, "export_legacy": export_legacy}

    monkeypatch.setattr(webui, "_sync_runtime_session_unlocked", unlocked)
    lock = webui._history_op_lock("s-lock")
    lock.acquire()
    result: list[dict] = []

    def invoke():
        try:
            result.append(webui._sync_runtime_session("s-lock", export_legacy=True))
        finally:
            finished.set()

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    try:
        assert entered.wait(0.1) is False
        assert finished.is_set() is False
    finally:
        lock.release()
    assert entered.wait(1.0) is True
    assert finished.wait(1.0) is True
    thread.join(timeout=1.0)
    assert result == [{"ok": True, "session_id": "s-lock", "export_legacy": True}]


def test_runtime_sync_all_reports_active_sessions_as_busy(monkeypatch, tmp_path):
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: sid == "s1")

    result = webui._sync_all_runtime_sessions()

    assert result["ok"] is False
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
    assert result["busy_count"] == 1
    assert result["results"][0]["busy"] is True


def test_subagent_storage_repair_counts_refused_and_pending_archive(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    fake._load_subagent_index = lambda: {"child-refused": "parent", "child-pending": "parent"}
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(webui, "_has_local_run_activity", lambda _sid: False)
    monkeypatch.setattr(webui, "_has_running_subagent_activity", lambda _sid: False)

    class FakeRepairService:
        def __init__(self, *_args, **_kwargs):
            pass

        def repair(self, _parent_id, child_id, **_kwargs):
            if child_id == "child-refused":
                return {
                    "ok": False,
                    "split_brain": True,
                    "applied": False,
                    "action": "refused",
                }
            return {
                "ok": False,
                "split_brain": True,
                "applied": True,
                "action": "committed_pending_archive",
            }

    monkeypatch.setattr(runtime_v2, "RuntimeV2SubagentRepairService", FakeRepairService)

    result = webui._repair_runtime_v2_subagent_storage(apply=True)

    assert result["ok"] is False
    assert result["refused"] == 1
    assert result["committed_pending_archive"] == 1
    assert result["applied"] == 1
    assert result["repaired"] == 0


def test_subagent_storage_repair_http_status_distinguishes_conflict_and_failure(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "_repair_runtime_v2_subagent_storage", lambda **_kwargs: {
        "ok": False,
        "busy": 0,
        "refused": 1,
        "committed_pending_archive": 0,
    })
    conflict = asyncio.run(webui.repair_runtime_v2_subagent_storage(
        apply=True,
        child_session_id="child",
        limit=0,
    ))
    assert conflict.status_code == 409

    monkeypatch.setattr(webui, "_repair_runtime_v2_subagent_storage", lambda **_kwargs: {
        "ok": False,
        "busy": 0,
        "refused": 0,
        "committed_pending_archive": 1,
    })
    incomplete = asyncio.run(webui.repair_runtime_v2_subagent_storage(
        apply=True,
        child_session_id="child",
        limit=0,
    ))
    assert incomplete.status_code == 500


def test_messages_empty_runtime_v2_projection_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=None,
    ))
    payload = _json_response_payload(response)

    assert response.status_code == 200
    assert payload == []


def test_message_count_empty_runtime_v2_projection_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import webui

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_message_count("s1"))
    payload = _json_response_payload(response)

    assert payload == {"count": 0, "source": "runtime_v2"}


def test_messages_projection_error_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _BrokenProjection:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("projection unavailable")

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _BrokenProjection)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_messages(
        "s1",
        limit=None,
        before_index=None,
        after_index=None,
        turns=None,
    ))
    payload = _json_response_payload(response)

    assert response.status_code == 500
    assert payload["error"] == "runtime_v2_projection_failed"
    assert payload["repair_required"] is True
    assert "projection unavailable" in payload["detail"]


def test_message_count_projection_error_does_not_fallback_legacy(monkeypatch, tmp_path):
    import runtime_v2
    import runtime_v2.ui_projection
    import webui

    class _BrokenProjection:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("projection unavailable")

    monkeypatch.setattr(runtime_v2, "runtime_v1_primary", lambda: False)
    monkeypatch.setattr(runtime_v2.ui_projection, "RuntimeUiProjection", _BrokenProjection)
    fake = _NoLegacyUiSessionManager(tmp_path, [{"type": "user", "content": "legacy"}])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.get_session_message_count("s1"))
    payload = _json_response_payload(response)

    assert response.status_code == 500
    assert payload["error"] == "runtime_v2_projection_failed"
    assert payload["repair_required"] is True
    assert "count" not in payload


def test_truncate_route_passes_runtime_seq_boundary(monkeypatch, tmp_path):
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.truncate_session_events(
        "s1",
        before_index=10,
        before_seq=99,
        backup=True,
    ))
    payload = _json_response_payload(response)

    assert payload == {"ok": True}
    assert fake.truncate_calls == [{
        "session_id": "s1",
        "before_index": 10,
        "truncate_before_seq": 99,
        "create_backup": True,
    }]


def test_truncate_route_allows_missing_runtime_seq_boundary(monkeypatch, tmp_path):
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.truncate_session_events(
        "s1",
        before_index=10,
        before_seq=None,
        backup=False,
    ))
    payload = _json_response_payload(response)

    assert payload == {"ok": True}
    assert fake.truncate_calls == [{
        "session_id": "s1",
        "before_index": 10,
        "truncate_before_seq": None,
        "create_backup": False,
    }]


def test_truncate_route_resolves_runtime_alerts(monkeypatch, tmp_path):
    import runtime_observability
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    resolved_sessions = []
    monkeypatch.setattr(
        runtime_observability,
        "mark_runs_resolved",
        lambda session_id: (resolved_sessions.append(session_id), 1)[1],
    )

    response = asyncio.run(webui.truncate_session_events(
        "s1",
        before_index=10,
        before_seq=None,
        backup=False,
    ))
    payload = _json_response_payload(response)

    assert payload == {"ok": True}
    assert resolved_sessions == ["s1"]


def test_truncate_route_rejects_pending_ask_user(monkeypatch, tmp_path):
    import human_interaction
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    class _PendingQuestionService:
        def pending_counts(self, _session_id):
            return {"questions": 1, "approvals": 0, "total": 1}

    monkeypatch.setattr(
        human_interaction,
        "get_human_interaction_service",
        lambda: _PendingQuestionService(),
    )
    response = asyncio.run(webui.truncate_session_events(
        "s1",
        before_index=10,
        before_seq=99,
        backup=False,
    ))
    payload = _json_response_payload(response)

    assert response.status_code == 409
    assert payload == {
        "ok": False,
        "error": "pending human interaction must be cancelled before history mutation",
    }
    assert fake.truncate_calls == []


def test_branch_route_passes_runtime_seq_boundary(monkeypatch, tmp_path):
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)

    response = asyncio.run(webui.branch_session_events(
        "s1",
        before_index=10,
        after_seq=123,
    ))
    payload = _json_response_payload(response)

    assert payload == {"ok": True, "session_id": "branch-1", "name": "branch"}
    assert fake.branch_calls == [{
        "session_id": "s1",
        "before_index": 10,
        "branch_after_seq": 123,
    }]


def test_sessions_state_uses_lightweight_run_status(monkeypatch, tmp_path):
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(webui, "is_run_active", lambda sid: sid == "s1")
    monkeypatch.setattr(webui, "get_run_started_at", lambda sid: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(
        webui,
        "get_active_run_info",
        lambda sid: {
            "session_id": sid,
            "run_id": "run-1",
            "run_active": True,
            "started_at": "2026-01-01T00:00:00Z",
            "phase": "running",
        } if sid == "s1" else None,
    )
    monkeypatch.setattr(webui, "_active_chat_by_session", {})

    def fail_snapshot(_sid):
        raise AssertionError("/sessions/state must not read Runtime V2 snapshots")

    monkeypatch.setattr(webui, "_runtime_v2_snapshot", fail_snapshot)

    payload = webui._build_sessions_state_snapshot(include_archived=False)

    assert fake.list_calls == [{"include_archived": False}]
    assert payload["sessions"][0]["id"] == "s1"
    assert payload["sessions"][0]["stream_active"] is True
    assert payload["sessions"][0]["run_active"] is True
    assert payload["sessions"][0]["pending_human_interactions"] == {
        "questions": 0,
        "approvals": 0,
        "total": 0,
    }
    assert payload["active_runs"][0]["session_id"] == "s1"
    assert payload["active_runs"][0]["run_id"] == "run-1"
    assert payload["active_runs"][0]["lightweight"] is True


def test_lightweight_status_does_not_treat_terminal_transport_as_active(monkeypatch):
    import webui

    monkeypatch.setattr(
        webui,
        "get_active_run_info",
        lambda _sid: {
            "session_id": "s1",
            "run_id": "r1",
            "run_active": False,
            "phase": "terminal",
        },
    )
    monkeypatch.setattr(webui, "is_run_active", lambda _sid: True)
    monkeypatch.setattr(webui, "_active_chat_by_session", {"s1": 1})
    monkeypatch.setattr(webui, "_chat_starting_by_session", {"s1": (1.0, "r1")})

    state = webui._session_run_state_fields_light("s1")

    assert state["run_active"] is False
    assert state["stream_active"] is False
    assert state["active_run"] is None
    assert state["stream_connections"] == 1


def test_sessions_state_cache_serves_stale_value_during_one_background_refresh(monkeypatch):
    import webui

    calls = []
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def build(include_archived=False):
        calls.append(bool(include_archived))
        if len(calls) > 1:
            refresh_started.set()
            assert release_refresh.wait(2)
        return {"seq": len(calls), "sessions": []}

    monkeypatch.setattr(webui, "_build_sessions_state_snapshot", build)
    monkeypatch.setattr(webui, "_SESSIONS_STATE_TTL_SEC", 0.001)
    with webui._sessions_state_cache_lock:
        webui._sessions_state_cache[False] = {"ts": 0.0, "payload": None}
        webui._sessions_state_refreshing.discard(False)

    try:
        first = webui._build_sessions_state_snapshot_cached(False)
        time.sleep(0.01)
        second = webui._build_sessions_state_snapshot_cached(False)
        assert refresh_started.wait(1)
        third = webui._build_sessions_state_snapshot_cached(False)

        assert first["seq"] == 1
        assert second is first
        assert third is first
        assert calls == [False, False]

        release_refresh.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with webui._sessions_state_cache_lock:
                refreshed = webui._sessions_state_cache[False]["payload"]
            if refreshed and refreshed.get("seq") == 2:
                break
            time.sleep(0.01)
        assert refreshed["seq"] == 2
    finally:
        release_refresh.set()
        with webui._sessions_state_cache_lock:
            webui._sessions_state_cache[False] = {"ts": 0.0, "payload": None}
            webui._sessions_state_refreshing.discard(False)


def test_sessions_state_invalidation_keeps_stale_value_and_rejects_older_refresh(monkeypatch):
    import webui

    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def build(include_archived=False):
        refresh_started.set()
        assert release_refresh.wait(2)
        return {"seq": 99, "sessions": [{"id": "stale"}]}

    monkeypatch.setattr(webui, "_build_sessions_state_snapshot", build)
    monkeypatch.setattr(webui, "_SESSIONS_STATE_TTL_SEC", 0.001)
    with webui._sessions_state_cache_lock:
        webui._sessions_state_cache[False] = {
            "ts": 0.0,
            "payload": {"seq": 1, "sessions": []},
        }
        webui._sessions_state_refreshing.discard(False)
        webui._sessions_state_refresh_generation.pop(False, None)

    try:
        served = webui._build_sessions_state_snapshot_cached(False)
        assert served["seq"] == 1
        assert refresh_started.wait(1)

        webui._invalidate_sessions_state_cache()
        release_refresh.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with webui._sessions_state_cache_lock:
                refreshing = False in webui._sessions_state_refreshing
            if not refreshing:
                break
            time.sleep(0.01)

        with webui._sessions_state_cache_lock:
            payload = webui._sessions_state_cache[False]["payload"]
            assert payload["seq"] == 1
            assert payload["sessions"] == []
    finally:
        release_refresh.set()
        with webui._sessions_state_cache_lock:
            webui._sessions_state_cache[False] = {"ts": 0.0, "payload": None}
            webui._sessions_state_refreshing.discard(False)
            webui._sessions_state_refresh_generation.pop(False, None)


def test_sessions_state_includes_pending_human_interaction_counts(monkeypatch, tmp_path):
    import human_interaction
    import webui

    fake = _FakeSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(webui, "_cleanup_stale_active_chat", lambda: None)
    monkeypatch.setattr(webui, "_session_run_state_fields_light", lambda _sid: {
        "stream_active": False,
        "run_active": False,
        "run_started_at": None,
    })
    monkeypatch.setattr(webui, "is_session_title_generation_pending", lambda _sid: False)

    class _PendingService:
        def pending_counts(self, session_id):
            assert session_id == "s1"
            return {"questions": 2, "approvals": 1, "total": 3}

    monkeypatch.setattr(human_interaction, "get_human_interaction_service", lambda: _PendingService())

    payload = webui._build_sessions_state_snapshot(include_archived=False)

    assert payload["sessions"][0]["pending_human_interactions"] == {
        "questions": 2,
        "approvals": 1,
        "total": 3,
    }


def test_archived_sessions_endpoint_returns_requested_prefetch_window(monkeypatch, tmp_path):
    import webui

    class _ArchivedSessionManager(_FakeSessionManager):
        def list_sessions(self, include_archived: bool = False) -> list[dict]:
            self.list_calls.append({"include_archived": include_archived})
            normal = [{"id": "normal", "name": "Normal", "archived": False}]
            archived = [
                {"id": f"archived-{index}", "name": f"Archived {index}", "archived": True}
                for index in range(55)
            ]
            return normal + archived if include_archived else normal

        def archived_session_count(self) -> int:
            return 55

    fake = _ArchivedSessionManager(tmp_path, [])
    monkeypatch.setattr(webui, "session_manager", fake)
    monkeypatch.setattr(webui, "_cleanup_stale_active_chat", lambda: None)
    monkeypatch.setattr(webui, "_session_run_state_fields_light", lambda _sid: {
        "stream_active": False,
        "run_active": False,
        "run_started_at": None,
    })
    monkeypatch.setattr(webui, "is_session_title_generation_pending", lambda _sid: False)

    response = asyncio.run(webui.list_sessions(
        include_archived=True,
        archived_only=True,
        offset=20,
        limit=20,
    ))
    payload = _json_response_payload(response)

    assert fake.list_calls == [{"include_archived": True}]
    assert len(payload) == 20
    assert payload[0]["id"] == "archived-20"
    assert payload[-1]["id"] == "archived-39"
    assert response.headers["x-archived-count"] == "55"


def test_observer_stream_does_not_count_as_local_run_activity(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "is_run_active", lambda _sid: False)
    monkeypatch.setattr(webui, "_active_chat_by_session", {})
    monkeypatch.setattr(webui, "_observer_streams_by_session", {"s1": 2})
    monkeypatch.setattr(webui, "_chat_starting_by_session", {})

    assert webui._has_local_run_activity("s1") is False
    assert webui._has_local_worker_activity("s1") is False


def test_auto_resume_only_for_orphan_interruption(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "_runtime_v2_snapshot", lambda _sid: {
        "runs": {
            "r1": {
                "run_id": "r1",
                "status": "interrupted",
                "reason": "no_local_activity",
                "finished_seq": 10,
            }
        }
    })
    assert webui._runtime_v2_auto_resume_pending("s1") is True

    monkeypatch.setattr(webui, "_runtime_v2_snapshot", lambda _sid: {
        "runs": {
            "r2": {
                "run_id": "r2",
                "status": "interrupted",
                "reason": "user",
                "finished_seq": 11,
            }
        }
    })
    assert webui._runtime_v2_auto_resume_pending("s1") is False

    monkeypatch.setattr(webui, "_runtime_v2_snapshot", lambda _sid: {
        "runs": {
            "r3": {
                "run_id": "r3",
                "status": "interrupted",
                "reason": "cancelled",
                "finished_seq": 12,
            }
        }
    })
    assert webui._runtime_v2_auto_resume_pending("s1") is True

    monkeypatch.setattr(webui, "_runtime_v2_snapshot", lambda _sid: {
        "runs": {
            "r4": {
                "run_id": "r4",
                "status": "interrupted",
                "reason": "user_button",
                "finished_seq": 13,
            }
        }
    })
    assert webui._runtime_v2_auto_resume_pending("s1") is False
