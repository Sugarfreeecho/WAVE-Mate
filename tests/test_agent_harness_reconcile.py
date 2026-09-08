import sys
import tempfile
import threading
import uuid
from contextlib import nullcontext
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _manager_with(**attrs):
    import agent_harness

    mgr = agent_harness.SessionManager.__new__(agent_harness.SessionManager)
    for name, value in attrs.items():
        setattr(mgr, name, value)
    return mgr


class _Repository:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir


def test_runtime_v2_create_session_does_not_initialize_legacy_files(monkeypatch, tmp_path):
    import agent_harness

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")

    session_id, dialogue, work, model, key_context, _metadata = mgr.get_or_create_session()
    session_dir = tmp_path / session_id

    assert dialogue == []
    assert work == []
    assert model == []
    assert key_context == ""
    assert (session_dir / "metadata.json").is_file()
    assert (session_dir / "events.jsonl").is_file()
    assert (session_dir / "snapshots" / "latest.json").is_file()
    for legacy_name in (
        "work_messages.json",
        "llm_history.json",
        "key_context.md",
        "key_context_history.jsonl",
        "todo_plan.md",
        "ui_events.json",
        "dialogue_history.json",
    ):
        assert not (session_dir / legacy_name).exists(), legacy_name


def test_runtime_v2_list_preview_never_calls_legacy_ui_loader(monkeypatch, tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    session_id, *_ = mgr.get_or_create_session()
    RuntimeMirror(tmp_path, path_resolver=mgr._resolve_session_path).mirror_ui_event(
        session_id,
        {"type": "user", "content": "projection-only preview"},
    )

    def fail_legacy(*_args, **_kwargs):
        raise AssertionError("Runtime V2 session list preview must not read legacy ui_events")

    monkeypatch.setattr(mgr, "_load_ui_events", fail_legacy)
    rows = mgr.list_sessions()

    assert len(rows) == 1
    assert rows[0]["last_user_preview"] == "projection-only preview"


def test_session_summary_uses_index_timestamp_without_touching_disk():
    class _NoDisk:
        def __truediv__(self, _name):
            raise AssertionError("timestamped session rows must not touch the filesystem")

    mgr = _manager_with(
        sessions_dir=_NoDisk(),
        _runtime_v2_primary=lambda: True,
    )
    row = mgr._session_entry_with_activity({
        "id": str(uuid.uuid4()),
        "created_at": "2026-09-08T12:00:00+00:00",
        "updated_at": "2026-09-08T12:30:00+00:00",
        "last_user_preview": "cached preview",
    })

    assert row["last_activity_at"] == "2026-09-08T12:30:00Z"
    assert row["last_user_preview"] == "cached preview"


def test_session_list_throttles_repeated_auto_archive_scans(monkeypatch, tmp_path):
    import agent_harness

    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    session_id = str(uuid.uuid4())
    now = agent_harness.datetime.now(agent_harness.timezone.utc).isoformat()
    mgr.index = [{
        "id": session_id,
        "created_at": now,
        "updated_at": now,
        "last_user_preview": "cached preview",
    }]
    calls = []
    original = mgr._session_entry_with_activity

    def counted(row):
        calls.append(row["id"])
        return original(row)

    monkeypatch.setattr(mgr, "_session_entry_with_activity", counted)

    mgr.list_sessions()
    first_call_count = len(calls)
    mgr.list_sessions()

    assert first_call_count == 2  # maintenance pass + list materialization
    assert len(calls) == first_call_count + 1  # only list materialization repeats


def test_reconcile_does_not_rebuild_when_llm_user_count_matches_ui():
    import agent_harness

    ui_events = [
        {"type": "user", "content": "u1"},
        {"type": "final", "content": "a1"},
        {"type": "user", "content": "u2"},
    ]
    llm_history = [
        {"type": "user", "content": "u1"},
        {
            "type": "assistant",
            "content": "",
            "tool_calls": [{"name": "search", "args": {}, "id": "call_1"}],
        },
        {"type": "tool", "content": "tool result", "tool_call_id": "call_1"},
        {"type": "assistant", "content": "a1", "metadata": {"is_final": True}},
        {"type": "user", "content": "u2"},
        {"type": "assistant", "content": "a2", "metadata": {"is_final": True}},
    ]
    saved_llm = []

    def fail_rebuild(*args, **kwargs):
        raise AssertionError("reconcile must not rebuild llm_history when user counts match")

    mgr = _manager_with(
        _runtime_v2_primary=lambda: False,
        _load_ui_events=lambda sid: ui_events,
        _load_llm_history=lambda sid: llm_history,
        _load_work_messages=lambda sid: [],
        _save_llm_history=lambda sid, msgs: saved_llm.append(msgs),
        _save_work_messages=lambda sid, msgs: None,
        _rebuild_llm_work_from_ui=fail_rebuild,
        _observe_runtime_v2_history=lambda *args, **kwargs: None,
    )

    changed = agent_harness.SessionManager.reconcile_llm_work_to_ui_user_count(
        mgr,
        "s1",
        include_work=False,
    )

    assert changed is False
    assert saved_llm == []


def test_reconcile_trims_only_extra_tail_turn_and_preserves_tools():
    import agent_harness

    ui_events = [
        {"type": "user", "content": "u1"},
        {"type": "final", "content": "a1"},
        {"type": "user", "content": "u2"},
    ]
    llm_history = [
        {"type": "user", "content": "u1"},
        {
            "type": "assistant",
            "content": "",
            "tool_calls": [{"name": "search", "args": {}, "id": "call_1"}],
        },
        {"type": "tool", "content": "tool result", "tool_call_id": "call_1"},
        {"type": "assistant", "content": "a1", "metadata": {"is_final": True}},
        {"type": "user", "content": "u2"},
        {"type": "assistant", "content": "a2", "metadata": {"is_final": True}},
        {"type": "user", "content": "extra"},
        {"type": "system", "content": "New Agent Loop Start"},
    ]
    saved_llm = []
    observed = []

    mgr = _manager_with(
        _runtime_v2_primary=lambda: False,
        _load_ui_events=lambda sid: ui_events,
        _load_llm_history=lambda sid: llm_history,
        _load_work_messages=lambda sid: [],
        _save_llm_history=lambda sid, msgs: saved_llm.append(msgs),
        _save_work_messages=lambda sid, msgs: None,
        _observe_runtime_v2_history=lambda *args, **kwargs: observed.append((args, kwargs)),
    )

    changed = agent_harness.SessionManager.reconcile_llm_work_to_ui_user_count(
        mgr,
        "s1",
        include_work=False,
    )

    assert changed is True
    assert len(saved_llm) == 1
    assert saved_llm[0] == llm_history[:6]
    assert any(m.get("type") == "tool" for m in saved_llm[0])
    assert observed == []


def test_legacy_rebuild_paths_do_not_replace_runtime_v2_model_history():
    source = (ROOT / "app" / "agent_harness.py").read_text(encoding="utf-8")
    forbidden_reasons = [
        "legacy_truncate",
        "legacy_branch",
        "legacy_repair",
        "legacy_reconcile",
        "legacy_tail_restored",
    ]
    for reason in forbidden_reasons:
        assert f'reason="{reason}"' not in source


def test_runtime_v2_active_ui_events_read_projection_without_legacy(monkeypatch, tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u1"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "a1"})

    def fail_legacy(_sid):
        raise AssertionError("Runtime V2 active UI history must not read legacy ui_events")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _load_ui_events=fail_legacy,
        _resolve_session_path=lambda sid: tmp_path / sid,
    )

    events = agent_harness.SessionManager._load_ui_events_for_active_runtime(mgr, "s1")

    assert [event["content"] for event in events] == ["u1", "a1"]


def test_runtime_v2_active_ui_events_empty_projection_does_not_fallback_legacy(tmp_path):
    import agent_harness

    def fail_legacy(_sid):
        raise AssertionError("Runtime V2 active UI history must not fallback to legacy ui_events")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _load_ui_events=fail_legacy,
        _resolve_session_path=lambda sid: tmp_path / sid,
    )

    events = agent_harness.SessionManager._load_ui_events_for_active_runtime(mgr, "s1")

    assert events == []


def test_runtime_v2_truncate_falls_back_to_ui_index_when_runtime_seq_missing(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeUiProjection

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u1"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "a1"})
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u2"})
    mirror.mirror_ui_event("s1", {"type": "status", "content": "interrupted"})

    projection = RuntimeUiProjection(tmp_path)
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_ui_events_for_active_runtime=lambda sid: projection.read_ui_events_fast(sid),
    )

    changed = agent_harness.SessionManager.truncate_session_at_event_index(
        mgr,
        "s1",
        2,
        truncate_before_seq=None,
        create_backup=False,
    )

    assert changed is True
    assert [event["content"] for event in RuntimeUiProjection(tmp_path).read_ui_events("s1")] == ["u1", "a1"]


def test_runtime_v2_react_continue_uses_active_ui_projection(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "unfinished"})

    def fail_legacy(_sid):
        raise AssertionError("Runtime V2 continue check must not read legacy ui_events")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _load_ui_events=fail_legacy,
        _resolve_session_path=lambda sid: tmp_path / sid,
    )

    assert agent_harness.SessionManager.can_continue_react_session(mgr, "s1") is True


def test_runtime_v2_react_continue_false_after_final_uses_projection(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("s1", {"type": "user", "content": "u"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "done"})

    def fail_legacy(_sid):
        raise AssertionError("Runtime V2 continue check must not read legacy ui_events")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _load_ui_events=fail_legacy,
        _resolve_session_path=lambda sid: tmp_path / sid,
    )

    assert agent_harness.SessionManager.can_continue_react_session(mgr, "s1") is False


def test_runtime_v2_truncate_only_changes_visible_range_without_legacy_rebuild(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeUiProjection

    observed = []
    mirror = RuntimeMirror(tmp_path)
    e1 = mirror.mirror_ui_event("s1", {"type": "user", "content": "u1"})
    e2 = mirror.mirror_ui_event("s1", {"type": "final", "content": "a1"})
    e3 = mirror.mirror_ui_event("s1", {"type": "user", "content": "u2"})
    mirror.mirror_ui_event("s1", {"type": "final", "content": "a2"})
    projection = RuntimeUiProjection(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 truncate must not write or rebuild legacy history")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_ui_events_for_active_runtime=lambda sid: projection.read_ui_events_fast(sid),
        _backup_session_before_truncate=fail_legacy,
        _save_ui_events=fail_legacy,
        _rebuild_llm_work_from_ui=fail_legacy,
        _save_llm_history=fail_legacy,
        _save_work_messages=fail_legacy,
        _save_dialogue_history=fail_legacy,
        remove_llm_compress_prefix_backup=fail_legacy,
        _observe_runtime_v2_history=lambda *args, **kwargs: observed.append((args, kwargs)),
    )

    changed = agent_harness.SessionManager.truncate_session_at_event_index(
        mgr,
        "s1",
        2,
        create_backup=True,
    )

    assert changed is True
    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3
    assert observed == [
        (
            ("truncate_visible_history_before_seq", "s1"),
            {"target_seq": 3, "keep_to_seq": 2, "reason": "runtime_v2_truncate"},
        )
    ]


def test_runtime_v2_branch_creates_v2_branch_without_legacy_rebuild(tmp_path):
    import agent_harness

    source_id = "11111111-1111-4111-8111-111111111111"
    source_dir = tmp_path / source_id
    source_dir.mkdir(parents=True)
    observed = []
    saved_meta = []
    saved_index = []

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 branch must not read/write or rebuild legacy history")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        index=[],
        _runtime_v2_primary=lambda: True,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_ui_events_for_active_runtime=lambda sid: [
            {"type": "user", "content": "u1"},
            {"type": "final", "content": "a1"},
        ],
        _load_metadata=lambda sid: {"name": "Source"},
        _save_metadata=lambda sid, meta: saved_meta.append((sid, dict(meta))),
        _save_index=lambda: saved_index.append(True),
        _copy_branch_sidecar_files=fail_legacy,
        _observe_runtime_v2_history=lambda *args, **kwargs: observed.append((args, kwargs)),
        _load_llm_history=fail_legacy,
        _load_work_messages=fail_legacy,
        _rebuild_llm_work_from_ui=fail_legacy,
        _save_ui_events=fail_legacy,
        _save_llm_history=fail_legacy,
        _save_work_messages=fail_legacy,
        _save_dialogue_history=fail_legacy,
    )

    result = agent_harness.SessionManager.branch_session_at_event_index(
        mgr,
        source_id,
        2,
    )

    assert result["ok"] is True
    new_id = result["session_id"]
    assert (tmp_path / new_id).is_dir()
    assert saved_meta and saved_meta[0][0] == new_id
    assert saved_meta[0][1]["branched_from"] == source_id
    assert saved_index == [True]
    assert observed == [
        (
            ("create_branch", new_id),
            {"source_session_id": source_id, "branch_from_seq": 2, "name": "(1)Source"},
        )
    ]


def test_delete_session_removes_subagent_descendants_and_index(tmp_path, monkeypatch):
    import json
    import agent_harness
    from runtime_v2 import SnapshotStore

    monkeypatch.setattr(
        agent_harness.SessionManager,
        "_normalize_session_id",
        staticmethod(lambda session_id: str(session_id or "").strip()),
    )
    root_id = "root"
    child_id = "child"
    grandchild_id = "grandchild"
    other_id = "other"
    sessions_dir = tmp_path / "s"
    sessions_dir.mkdir()
    index_file = sessions_dir / "sessions_index.json"
    index_file.write_text(
        json.dumps({
            "sessions": [
                {"id": root_id, "name": "root"},
                {"id": child_id, "name": "child"},
                {"id": other_id, "name": "other"},
            ],
        }),
        encoding="utf-8",
    )
    for sid in (root_id, other_id):
        path = sessions_dir / sid
        path.mkdir(parents=True)
        (path / "metadata.json").write_text(
            json.dumps({"name": sid}),
            encoding="utf-8",
        )
    nested_child = sessions_dir / root_id / "subagents" / child_id
    nested_grandchild = nested_child / "subagents" / grandchild_id
    nested_grandchild.mkdir(parents=True)
    (nested_child / "metadata.json").write_text("{}", encoding="utf-8")
    (nested_grandchild / "metadata.json").write_text("{}", encoding="utf-8")
    (sessions_dir / "subagent_index.json").write_text(
        json.dumps({child_id: root_id, grandchild_id: child_id}),
        encoding="utf-8",
    )

    mgr = agent_harness.SessionManager(sessions_dir, index_file)
    cancelled = []
    original_cancel = SnapshotStore.cancel_checkpoint

    def record_cancel(store, session_id, timeout_seconds=0.5):
        cancelled.append(session_id)
        return original_cancel(store, session_id, timeout_seconds)

    monkeypatch.setattr(SnapshotStore, "cancel_checkpoint", record_cancel)
    mgr.delete_session(root_id)

    assert not (sessions_dir / root_id).exists()
    assert not nested_child.exists()
    assert not nested_grandchild.exists()
    assert (sessions_dir / other_id).exists()
    assert json.loads((sessions_dir / "subagent_index.json").read_text(encoding="utf-8")) == {}
    rows = json.loads(index_file.read_text(encoding="utf-8"))["sessions"]
    assert [row["id"] for row in rows] == [other_id]
    assert set(cancelled) == {root_id, child_id, grandchild_id}


def test_delete_tombstone_prevents_failed_rmtree_from_resurrecting_session(tmp_path, monkeypatch):
    import agent_harness
    import json

    session_id = "session"
    sessions_dir = tmp_path / "s"
    session_path = sessions_dir / session_id
    session_path.mkdir(parents=True)
    (session_path / "metadata.json").write_text(
        json.dumps({"id": session_id, "name": "deleted"}),
        encoding="utf-8",
    )
    (session_path / "events.jsonl").write_text("{}\n", encoding="utf-8")
    index_file = sessions_dir / "sessions_index.json"
    index_file.write_text(
        json.dumps({"sessions": [{"id": session_id, "name": "deleted"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        agent_harness.SessionManager,
        "_normalize_session_id",
        staticmethod(lambda value: str(value or "").strip()),
    )
    manager = agent_harness.SessionManager(sessions_dir, index_file)

    def fail_rmtree(_path):
        raise PermissionError("busy")

    monkeypatch.setattr(agent_harness.shutil, "rmtree", fail_rmtree)

    manager.delete_session(session_id)

    assert session_path.exists()
    assert json.loads((session_path / "metadata.json").read_text(encoding="utf-8"))["deleted"] is True
    manager.refresh_sessions_index_from_disk()
    assert manager.index == []


def test_runtime_v2_clear_todo_writes_empty_snapshot(tmp_path):
    import agent_harness
    from runtime_v2 import SnapshotStore
    from session_todo_extension import write_todo_extension

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
    )
    (tmp_path / "s1").mkdir()
    write_todo_extension(
        mgr,
        "s1",
        [{"id": "1", "text": "todo", "status": "pending"}],
    )

    changed = agent_harness.SessionManager.clear_todo_plan(mgr, "s1")
    snapshot = SnapshotStore(tmp_path).read("s1")

    assert changed is True
    extension = snapshot["extensions"]["session-todo"]["plan"]
    assert extension["revision"] == 2
    assert extension["value"]["items"] == []
    assert extension["value"]["cleared"] is True


def test_runtime_v2_repair_and_reconcile_skip_legacy_rebuilds():
    import agent_harness

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 repair/reconcile must not touch legacy history")

    mgr = _manager_with(
        _runtime_v2_primary=lambda: True,
        _load_ui_events=fail_legacy,
        _load_llm_history=fail_legacy,
        _load_work_messages=fail_legacy,
        _rebuild_llm_work_from_ui=fail_legacy,
        _save_llm_history=fail_legacy,
        _save_work_messages=fail_legacy,
    )

    repaired = agent_harness.SessionManager.repair_compacted_llm_history_from_ui(mgr, "s1")
    reconciled = agent_harness.SessionManager.reconcile_llm_work_to_ui_user_count(mgr, "s1")

    assert repaired is False
    assert reconciled is False


def test_runtime_v2_append_tail_replaces_projected_visible_history(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeUiProjection

    observed = []

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 append tail must not touch legacy history")

    RuntimeMirror(tmp_path).mirror_ui_event("s1", {"type": "user", "content": "existing"})

    mgr = _manager_with(
        _runtime_v2_primary=lambda: True,
        _observe_runtime_v2_history=lambda *args, **kwargs: observed.append((args, kwargs)),
        repository=_Repository(tmp_path),
        _resolve_session_path=lambda sid: tmp_path / str(sid),
        _load_ui_events=fail_legacy,
        _save_ui_events=fail_legacy,
        _rebuild_llm_work_from_ui=fail_legacy,
        _save_llm_history=fail_legacy,
        _save_work_messages=fail_legacy,
        _save_dialogue_history=fail_legacy,
    )

    changed = agent_harness.SessionManager.append_ui_events_tail(
        mgr,
        "s1",
        [
            {"type": "user", "content": "u1"},
            {"type": "final", "content": "a1"},
        ],
    )

    assert changed is True
    assert [event["content"] for event in RuntimeUiProjection(tmp_path).read_ui_events("s1")] == [
        "existing",
        "u1",
        "a1",
    ]
    assert observed == []


def test_runtime_v2_create_subagent_does_not_initialize_legacy_histories(monkeypatch, tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    parent_id = str(uuid.uuid4())
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    mgr._save_metadata(parent_id, {"name": "parent"})

    child_id = mgr.create_subagent_session(parent_id, "desc", "generalPurpose", 1)

    child_path = tmp_path / parent_id / "subagents" / child_id
    assert (child_path / "metadata.json").is_file()
    assert not (child_path / "work_messages.json").exists()
    assert not (child_path / "llm_history.json").exists()
    assert not (child_path / "ui_events.json").exists()
    assert not (child_path / "dialogue_history.json").exists()
    assert not (child_path / "key_context.md").exists()

    tasks = RuntimeSubagentStore(tmp_path).list_tasks(parent_id)
    assert any(row.get("task_id") == child_id and row.get("status") == "pending" for row in tasks)


def test_create_subagent_persists_selected_model_profile(monkeypatch, tmp_path):
    import agent_harness

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    parent_id = str(uuid.uuid4())
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    mgr._save_metadata(parent_id, {"name": "parent"})

    child_id = mgr.create_subagent_session(
        parent_id,
        "profile-bound child",
        "generalPurpose",
        1,
        model_profile_id="profile-deep",
    )

    metadata = mgr._load_metadata(child_id)
    assert metadata["model_profile_id"] == "profile-deep"
    assert "executor_model" not in metadata


def test_switch_subagent_model_profile_releases_frozen_runtime(monkeypatch, tmp_path):
    import agent_harness

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    parent_id = str(uuid.uuid4())
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    mgr._save_metadata(parent_id, {"name": "parent"})
    child_id = mgr.create_subagent_session(
        parent_id,
        "profile-bound child",
        "generalPurpose",
        1,
        model_profile_id="profile-fast",
        executor_model="model-fast",
    )
    mgr.patch_subagent_metadata(
        child_id,
        {
            "fork_model_runtime": {"model": "model-fast"},
            "fork_runtime_config": {
                "system_segments": ["inherited system"],
                "tools": [{"name": "task"}],
                "model_runtime": {"model": "model-fast"},
            },
        },
    )
    invalidated = []
    monkeypatch.setattr(agent_harness, "_invalidate_executor_config_cache", invalidated.append)

    record = mgr.switch_subagent_model_profile(
        child_id,
        "profile-deep",
        executor_model="model-deep",
        switch_id="switch-1",
        requested_by="user",
    )

    metadata = mgr._load_metadata(child_id)
    assert record["from_profile_id"] == "profile-fast"
    assert record["to_profile_id"] == "profile-deep"
    assert metadata["model_profile_id"] == "profile-deep"
    assert metadata["executor_model"] == "model-deep"
    assert metadata["fork_model_runtime"] == {}
    assert "model_runtime" not in metadata["fork_runtime_config"]
    assert metadata["fork_runtime_config"]["system_segments"] == ["inherited system"]
    assert metadata["last_model_switch"] == record
    assert metadata["model_switch_history"][-1] == record
    assert invalidated == [child_id]


def test_runtime_v2_fork_subagent_uses_v2_projection_not_legacy(monkeypatch, tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeHistoryOps, RuntimeModelProjection, SnapshotStore

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    parent_id = str(uuid.uuid4())
    mgr = agent_harness.SessionManager(tmp_path, tmp_path / "sessions.json")
    mgr._save_metadata(parent_id, {"name": "parent"})
    RuntimeHistoryOps(tmp_path).append_model_message(parent_id, "user", "from v2")
    RuntimeHistoryOps(tmp_path).commit_context_summary(parent_id, "v2 summary")

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent fork must not read/write legacy histories")

    monkeypatch.setattr(mgr, "_load_llm_history", fail_legacy)
    monkeypatch.setattr(mgr, "_load_work_messages", fail_legacy)
    monkeypatch.setattr(mgr, "_load_key_context", fail_legacy)
    monkeypatch.setattr(mgr, "_save_llm_history", fail_legacy)
    monkeypatch.setattr(mgr, "_save_work_messages", fail_legacy)
    monkeypatch.setattr(mgr, "_save_key_context", fail_legacy)

    child_id = mgr.fork_subagent_from_parent(parent_id, "desc", "generalPurpose", 1)

    child_messages = RuntimeModelProjection(
        tmp_path,
        path_resolver=mgr._resolve_session_path,
    ).read_message_dicts(child_id)
    child_summary = SnapshotStore(
        tmp_path,
        path_resolver=mgr._resolve_session_path,
    ).read(child_id).get("context", {}).get("summary", {})
    assert child_messages == [{"type": "user", "content": "from v2"}]
    assert child_summary.get("summary") == "v2 summary"
    assert not (tmp_path / child_id).exists()


def test_runtime_v2_subagent_and_grandchild_share_one_nested_event_log(monkeypatch):
    import agent_harness
    from runtime_v2 import RuntimeHistoryOps, RuntimeModelProjection, RuntimeMirror

    monkeypatch.setenv("RUNTIME_VERSION", "2")
    # Keep the base short enough for a two-level UUID tree on Windows.
    with tempfile.TemporaryDirectory(prefix="rt2-nested-") as raw_root:
        root = Path(raw_root)
        parent_id = str(uuid.uuid4())
        mgr = agent_harness.SessionManager(root, root / "sessions.json")
        mgr._save_metadata(parent_id, {"name": "parent"})
        child_id = mgr.create_subagent_session(parent_id, "child", "generalPurpose", 1)
        grandchild_id = mgr.create_subagent_session(child_id, "grandchild", "generalPurpose", 2)
        resolver = mgr._resolve_session_path

        for sid, text in ((child_id, "child model"), (grandchild_id, "grandchild model")):
            RuntimeHistoryOps(root, path_resolver=resolver).append_model_message(sid, "user", text)
            RuntimeMirror(root, path_resolver=resolver).mirror_ui_event(
                sid, {"type": "user", "content": text}
            )
            assert RuntimeModelProjection(root, path_resolver=resolver).read_message_dicts(sid) == [
                {"type": "user", "content": text}
            ]
            assert not (root / sid).exists()

        child_path = root / parent_id / "subagents" / child_id
        grandchild_path = child_path / "subagents" / grandchild_id
        assert resolver(child_id).samefile(child_path)
        assert resolver(grandchild_id).samefile(grandchild_path)
        assert (child_path / "events.jsonl").is_file()
        assert (grandchild_path / "events.jsonl").is_file()


def test_runtime_v2_subagent_output_does_not_fallback_legacy(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)
    store.upsert_task("parent", "agent1", {"status": "completed"})

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent output must not read legacy subagent files")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        list_subagent_tasks=fail_legacy,
        validate_subagent_resume=fail_legacy,
        _get_session_path=fail_legacy,
    )

    result = agent_harness.SessionManager.read_subagent_task_output(mgr, "parent", "agent1")

    assert result == {"ok": False, "error": "output not found"}


def test_runtime_v2_subagent_output_reads_v2_store(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)
    store.write_task_output("parent", "agent1", "final text")

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent output must not read legacy subagent files")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        list_subagent_tasks=fail_legacy,
        validate_subagent_resume=fail_legacy,
        _get_session_path=fail_legacy,
    )

    result = agent_harness.SessionManager.read_subagent_task_output(mgr, "parent", "agent1")

    assert result["ok"] is True
    assert result["content"] == "final text"


def test_runtime_v2_subagent_task_list_does_not_fallback_legacy(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent task list must not read legacy task index")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _list_subagent_tasks_v1=fail_legacy,
    )

    assert agent_harness.SessionManager.list_subagent_tasks(mgr, "parent") == []


def test_runtime_v2_upsert_subagent_task_writes_only_v2_store(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent task upsert must not write legacy task index")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _upsert_subagent_task_v1=fail_legacy,
    )

    agent_harness.SessionManager.upsert_subagent_task(mgr, "parent", "agent1", {"status": "running"})

    tasks = store.list_tasks("parent")
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "agent1"
    assert tasks[0]["status"] == "running"


def test_runtime_v2_write_subagent_task_output_writes_only_v2_store(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent task output write must not touch legacy output path")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _get_session_path=fail_legacy,
    )

    output_path = agent_harness.SessionManager.write_subagent_task_output(
        mgr,
        "parent",
        "agent1",
        "final text",
    )

    assert output_path.endswith("output.md")
    assert store.read_task_output("parent", "agent1")["content"] == "final text"


def test_runtime_v2_append_ui_event_writes_projection_not_legacy(monkeypatch):
    import agent_harness
    import runtime_v2

    mirrored = []
    saved_meta = []
    cleared = []

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 append_ui_event must not read/write legacy ui_events")

    monkeypatch.setattr(runtime_v2, "runtime_v2_primary", lambda: True)
    monkeypatch.setattr(runtime_v2, "runtime_v2_strict", lambda: True)

    mgr = _manager_with(
        index=[{"id": "s1"}],
        _lock=threading.RLock(),
        _session_metadata_lock=lambda sid: nullcontext(),
        _load_metadata_unlocked=lambda sid: {},
        _save_metadata_unlocked=lambda sid, meta: saved_meta.append((sid, dict(meta))),
        _save_index=lambda: None,
        clear_session_unread_result=lambda sid: cleared.append(sid),
        _load_ui_events=fail_legacy,
        _save_ui_events=fail_legacy,
        _mirror_ui_event_to_runtime_v2=lambda sid, ev: mirrored.append((sid, dict(ev))) or object(),
    )

    agent_harness.SessionManager.append_ui_event(mgr, "s1", {"type": "user", "content": "hello"})

    assert mirrored and mirrored[0][0] == "s1"
    assert mirrored[0][1]["type"] == "user"
    assert len(saved_meta) == 1
    assert saved_meta[0][0] == "s1"
    assert saved_meta[0][1]["last_user_preview"] == "hello"
    assert saved_meta[0][1]["updated_at"]
    assert mgr.index[0]["last_user_preview"] == "hello"
    assert mgr.index[0]["updated_at"] == saved_meta[0][1]["updated_at"]
    assert cleared == ["s1"]


def test_runtime_v2_subagent_dialogue_turns_use_model_projection_not_legacy(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeHistoryOps

    RuntimeHistoryOps(tmp_path).replace_model_history(
        "child",
        [
            {"type": "user", "content": "question"},
            {"type": "assistant", "content": "answer"},
        ],
        reason="test",
    )

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent dialogue fallback must not read legacy llm_history")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _load_ui_events_for_active_runtime=lambda sid: [],
        _get_llm_history_path=fail_legacy,
    )

    turns = agent_harness.SessionManager._extract_subagent_dialogue_turns(mgr, "child")

    assert turns == [{"user": "question", "final": "answer"}]


def test_runtime_v2_list_subagents_flat_reads_projection_not_legacy_ui(tmp_path):
    import json
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeUiProjection

    root_id = "11111111-1111-4111-8111-111111111111"
    child_id = "22222222-2222-4222-8222-222222222222"
    child_dir = tmp_path / root_id / "subagents" / child_id
    child_dir.mkdir(parents=True)
    (child_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": "child",
                "subagent_description": "desc",
                "subagent_type": "generalPurpose",
                "subagent_ok": True,
            }
        ),
        encoding="utf-8",
    )
    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event(child_id, {"type": "user", "content": "u"})
    mirror.mirror_ui_event(child_id, {"type": "final", "content": "a"})
    mirror.mirror_ui_event(root_id, {"type": "subagent_finish", "agent_id": child_id, "ok": True})

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 subagent list must not read legacy ui_events")

    projection = RuntimeUiProjection(tmp_path, path_resolver=lambda sid: tmp_path / sid)
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        index=[],
        _runtime_v2_primary=lambda: True,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _get_session_path=lambda sid: tmp_path / sid,
        _load_ui_events=fail_legacy,
        _load_ui_events_for_active_runtime=lambda sid: projection.read_ui_events(sid),
    )

    rows = agent_harness.SessionManager.list_subagents_flat(mgr, root_id)

    assert len(rows) == 1
    assert rows[0]["id"] == child_id
    assert rows[0]["status"] == "completed"
    assert rows[0]["has_final"] is True
    assert rows[0]["result_preview"] == "a"


def test_runtime_v2_pending_subagent_results_do_not_fallback_legacy(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 pending subagent path must not read legacy pending files")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _load_metadata=lambda sid: {},
        _get_pending_subagent_results_path=fail_legacy,
    )
    mgr.repository.load_json_list = fail_legacy

    assert agent_harness.SessionManager._load_pending_subagent_results(mgr, "parent") == []


def test_runtime_v2_pending_subagent_continue_uses_v2_ui_projection(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeSubagentStore

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("parent", {"type": "user", "content": "u"})
    mirror.mirror_ui_event("parent", {"type": "final", "content": "a"})
    store = RuntimeSubagentStore(tmp_path)
    store.append_pending_result("parent", {
        "agent_id": "agent1",
        "description": "worker",
        "status": "completed",
        "result": "done",
        "after_final_index": 1,
    })

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 pending continue must not read legacy ui/pending files")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_metadata=lambda sid: {},
        _load_ui_events=fail_legacy,
        _get_pending_subagent_results_path=fail_legacy,
        _save_pending_subagent_results=lambda sid, rows: store.save_pending_results(sid, rows),
    )
    mgr.repository.load_json_list = fail_legacy

    assert agent_harness.SessionManager.can_continue_after_subagents(mgr, "parent") is True
    lines = agent_harness.SessionManager.consume_pending_subagent_notifications(mgr, "parent")

    assert len(lines) == 1
    assert "Subagent agent1" in lines[0]
    assert store.list_pending_results("parent") == []


def test_result_completed_during_parent_run_is_not_bound_to_previous_final(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeSubagentStore

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("parent", {"type": "user", "content": "old"})
    mirror.mirror_ui_event("parent", {"type": "final", "content": "old answer"})
    mirror.mirror_ui_event("parent", {"type": "user", "content": "new"})
    store = RuntimeSubagentStore(tmp_path)
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_metadata=lambda sid: {},
    )

    agent_harness.SessionManager.append_pending_subagent_result(mgr, "parent", {
        "agent_id": "agent1",
        "status": "completed",
        "result": "new result",
        "parent_run_id": "run-new",
    })

    row = store.list_pending_results("parent")[0]
    assert row["delivery_scope"] == "parent_run"
    assert "after_final_index" not in row
    assert row["result_id"]
    assert agent_harness.SessionManager.can_continue_after_subagents(mgr, "parent") is False


def test_parent_run_consumes_own_result_once_without_final(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)
    store.append_pending_result("parent", {
        "result_id": "result-1",
        "agent_id": "agent1",
        "status": "completed",
        "result": "done",
        "parent_run_id": "run-1",
        "delivery_scope": "parent_run",
        "delivery_state": "pending",
    })
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _load_metadata=lambda sid: {},
        _load_ui_events_for_active_runtime=lambda sid: [],
    )

    first = agent_harness.SessionManager.consume_pending_subagent_notifications(
        mgr, "parent", parent_run_id="run-1"
    )
    second = agent_harness.SessionManager.consume_pending_subagent_notifications(
        mgr, "parent", parent_run_id="run-1"
    )

    assert len(first) == 1
    assert second == []
    assert store.list_pending_results("parent") == []


def test_unconsumed_parent_run_result_becomes_actionable_after_final(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeSubagentStore

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("parent", {"type": "user", "content": "u"})
    mirror.mirror_ui_event("parent", {"type": "final", "content": "a"})
    store = RuntimeSubagentStore(tmp_path)
    store.append_pending_result("parent", {
        "result_id": "result-1",
        "agent_id": "agent1",
        "status": "completed",
        "result": "late",
        "parent_run_id": "run-1",
        "delivery_scope": "parent_run",
        "delivery_state": "pending",
    })
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_metadata=lambda sid: {},
    )

    assert agent_harness.SessionManager.anchor_pending_subagent_results_for_run(
        mgr, "parent", "run-1"
    ) == 1
    assert agent_harness.SessionManager.can_continue_after_subagents(mgr, "parent") is True


def test_pending_result_claim_can_rollback_and_then_commit(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeSubagentStore

    store = RuntimeSubagentStore(tmp_path)
    store.append_pending_result("parent", {
        "result_id": "result-1",
        "agent_id": "agent1",
        "status": "completed",
        "result": "done",
        "parent_run_id": "run-1",
        "delivery_state": "pending",
    })
    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _load_metadata=lambda sid: {},
        _load_ui_events_for_active_runtime=lambda sid: [],
    )

    claimed = agent_harness.SessionManager.claim_pending_subagent_notifications(
        mgr, "parent", "claim-1", parent_run_id="run-1"
    )
    assert [row["result_id"] for row in claimed] == ["result-1"]
    assert agent_harness.SessionManager.claim_pending_subagent_notifications(
        mgr, "parent", "claim-2", parent_run_id="run-1"
    ) == []
    assert agent_harness.SessionManager.resolve_pending_subagent_claim(
        mgr, "parent", "claim-1", consumed=False
    ) == 1
    assert store.list_pending_results("parent")[0]["delivery_state"] == "pending"

    assert agent_harness.SessionManager.claim_pending_subagent_notifications(
        mgr, "parent", "claim-3", parent_run_id="run-1"
    )
    assert agent_harness.SessionManager.resolve_pending_subagent_claim(
        mgr, "parent", "claim-3", consumed=True
    ) == 1
    assert store.list_pending_results("parent") == []


def test_runtime_v2_append_pending_subagent_result_writes_only_v2_store(tmp_path):
    import agent_harness
    from runtime_v2 import RuntimeMirror, RuntimeSubagentStore

    mirror = RuntimeMirror(tmp_path)
    mirror.mirror_ui_event("parent", {"type": "user", "content": "u"})
    mirror.mirror_ui_event("parent", {"type": "final", "content": "a"})
    store = RuntimeSubagentStore(tmp_path)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("Runtime V2 append pending subagent result must not touch legacy files")

    mgr = _manager_with(
        repository=_Repository(tmp_path),
        _runtime_v2_primary=lambda: True,
        _runtime_subagent_store=lambda: store,
        _resolve_session_path=lambda sid: tmp_path / sid,
        _load_ui_events=fail_legacy,
        _get_pending_subagent_results_path=fail_legacy,
    )
    mgr.repository.load_json_list = fail_legacy
    mgr.repository.save_json_list = fail_legacy

    agent_harness.SessionManager.append_pending_subagent_result(mgr, "parent", {
        "agent_id": "agent1",
        "status": "completed",
        "result": "done",
    })

    rows = store.list_pending_results("parent")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent1"
    assert rows[0]["after_final_index"] == 1
