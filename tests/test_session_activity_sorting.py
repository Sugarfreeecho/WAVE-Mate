import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_runtime_v2_event_activity_moves_old_session_to_front(tmp_path):
    import agent_harness

    old_id = "11111111-1111-4111-8111-111111111111"
    new_id = "22222222-2222-4222-8222-222222222222"
    sessions_dir = tmp_path / "sessions"
    index_file = tmp_path / "sessions.json"
    sessions_dir.mkdir()

    rows = [
        {
            "id": new_id,
            "name": "newer",
            "created_at": "2026-07-10T00:00:00Z",
            "updated_at": "2026-07-10T00:00:00Z",
        },
        {
            "id": old_id,
            "name": "older",
            "created_at": "2026-07-09T00:00:00Z",
            "updated_at": "2026-07-09T00:00:00Z",
        },
    ]
    index_file.write_text(json.dumps({"sessions": rows}), encoding="utf-8")
    rows_by_id = {row["id"]: row for row in rows}
    for sid in (old_id, new_id):
        session_dir = sessions_dir / sid
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text(
            json.dumps(rows_by_id[sid]),
            encoding="utf-8",
        )

    event_path = sessions_dir / old_id / "events.jsonl"
    event_path.write_text('{"seq":1,"type":"message_user"}\n', encoding="utf-8")
    activity_ts = 1_800_000_000
    os.utime(event_path, (activity_ts, activity_ts))

    manager = agent_harness.SessionManager(sessions_dir, index_file)
    listed = manager.list_sessions(include_archived=True)

    assert [row["id"] for row in listed] == [old_id, new_id]
    assert manager._iso_ts(listed[0]["last_activity_at"]) == activity_ts


def test_user_event_side_effect_persists_activity_for_refresh(tmp_path):
    import agent_harness

    old_id = "11111111-1111-4111-8111-111111111111"
    new_id = "22222222-2222-4222-8222-222222222222"
    sessions_dir = tmp_path / "sessions"
    index_file = tmp_path / "sessions.json"
    sessions_dir.mkdir()
    rows = [
        {"id": new_id, "name": "newer", "created_at": "2026-07-10T00:00:00Z", "updated_at": "2026-07-10T00:00:00Z"},
        {"id": old_id, "name": "older", "created_at": "2026-07-09T00:00:00Z", "updated_at": "2026-07-09T00:00:00Z"},
    ]
    index_file.write_text(json.dumps({"sessions": rows}), encoding="utf-8")
    for row in rows:
        session_dir = sessions_dir / row["id"]
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text(json.dumps(row), encoding="utf-8")

    manager = agent_harness.SessionManager(sessions_dir, index_file)
    manager._apply_appended_ui_event_side_effects(old_id, {
        "type": "user",
        "content": "new question",
        "created_at": "2026-07-11T00:00:00Z",
    })

    reloaded = agent_harness.SessionManager(sessions_dir, index_file)
    # This fixture deliberately uses stable timestamps. Include archived rows so
    # the assertion remains about persisted activity ordering as wall time moves.
    rows_after_refresh = reloaded.list_sessions(include_archived=True)
    assert [row["id"] for row in rows_after_refresh] == [old_id, new_id]
    assert rows_after_refresh[0]["last_user_preview"] == "new question"


def test_active_goal_round_clears_completion_only_at_run_terminal(tmp_path, monkeypatch):
    import agent_goal
    import agent_harness
    import agent_loop

    monkeypatch.setenv("GOAL_ENABLED", "1")
    monkeypatch.setenv("RUNTIME_VERSION", "2")
    session_id = "33333333-3333-4333-8333-333333333333"
    sessions_dir = tmp_path / "sessions"
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True)
    metadata = {
        "id": session_id,
        "name": "goal",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
        "unread_result": True,
        "unread_result_status": "success",
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    index_file = tmp_path / "sessions.json"
    index_file.write_text(json.dumps({"sessions": [metadata]}), encoding="utf-8")
    manager = agent_harness.SessionManager(sessions_dir, index_file)
    agent_goal.manager_for(manager).create(session_id, "Keep running until complete")

    manager._apply_appended_ui_event_side_effects(
        session_id,
        {"type": "final", "content": "Intermediate round result"},
    )

    summary = manager.get_session_summary(session_id)
    assert summary["unread_result"] is True

    monkeypatch.setattr(agent_loop, "session_manager", manager)
    agent_loop._mark_run_terminal_unread(session_id, "run_finished")
    summary = manager.get_session_summary(session_id)
    assert summary["unread_result"] is False


def test_unread_acknowledgement_cannot_clear_a_newer_run(tmp_path):
    import agent_harness

    session_id = "34343434-3434-4434-8434-343434343434"
    sessions_dir = tmp_path / "sessions"
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True)
    metadata = {
        "id": session_id,
        "name": "run-scoped unread",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    index_file = tmp_path / "sessions.json"
    index_file.write_text(json.dumps({"sessions": [metadata]}), encoding="utf-8")
    manager = agent_harness.SessionManager(sessions_dir, index_file)

    manager.mark_session_unread_result(session_id, run_id="run-old")
    manager.mark_session_unread_result(session_id, run_id="run-new")

    reloaded_manager = agent_harness.SessionManager(sessions_dir, index_file)
    reloaded_unread = reloaded_manager.get_session_summary(session_id)
    assert reloaded_unread["unread_result"] is True
    assert reloaded_unread["unread_result_run_id"] == "run-new"

    assert reloaded_manager.clear_session_unread_result(
        session_id,
        expected_run_id="run-old",
    ) is False
    summary = reloaded_manager.get_session_summary(session_id)
    assert summary["unread_result"] is True
    assert summary["unread_result_run_id"] == "run-new"

    assert reloaded_manager.clear_session_unread_result(
        session_id,
        expected_run_id="run-new",
    ) is True
    summary = reloaded_manager.get_session_summary(session_id)
    assert summary["unread_result"] is False
    assert "unread_result_run_id" not in summary

    restarted_manager = agent_harness.SessionManager(sessions_dir, index_file)
    reloaded_summary = restarted_manager.get_session_summary(session_id)
    assert reloaded_summary["unread_result"] is False
    assert "unread_result_run_id" not in reloaded_summary


def test_pending_queue_user_turn_preserves_previous_unread_result(tmp_path):
    import agent_harness

    session_id = "44444444-4444-4444-8444-444444444444"
    sessions_dir = tmp_path / "sessions"
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True)
    metadata = {
        "id": session_id,
        "name": "pending queue",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
        "unread_result": True,
        "unread_result_status": "success",
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    index_file = tmp_path / "sessions.json"
    index_file.write_text(json.dumps({"sessions": [metadata]}), encoding="utf-8")
    manager = agent_harness.SessionManager(sessions_dir, index_file)

    manager._apply_appended_ui_event_side_effects(
        session_id,
        {
            "type": "user",
            "content": "Automatically dispatched pending task",
            "preserve_unread_result": True,
        },
    )

    summary = manager.get_session_summary(session_id)
    assert summary["unread_result"] is True
    assert summary["unread_result_status"] == "success"
