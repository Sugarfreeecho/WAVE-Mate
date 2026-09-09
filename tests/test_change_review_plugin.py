from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_store():
    name = "test_change_review_store"
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "plugins" / "change-review" / "store.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_plugin_module(filename, name):
    path = ROOT / "plugins" / "change-review" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def review(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "app"))
    import agent_tools

    monkeypatch.setattr(agent_tools, "WORK_DIR", tmp_path / "workspace")
    agent_tools.WORK_DIR.mkdir()
    store = _load_store().FileChangeReviewStore(tmp_path / "session")
    with agent_tools.tool_work_dir_override(agent_tools.WORK_DIR):
        yield store, agent_tools.WORK_DIR


def capture(store, workspace, tool, args, mutate, run="run-1", call="call-1"):
    started = store.begin_capture(
        tool, args, run_id=run, tool_call_id=call, work_root=workspace
    )
    mutate()
    return store.finish_capture(started)


def init_git_workspace(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "review@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Review Test"], check=True)


def test_create_modify_delete_and_undo(review):
    store, workspace = review
    path = workspace / "hello.txt"
    changes = capture(
        store,
        workspace,
        "write_file",
        {"path": "hello.txt", "contents": "one\ntwo\n"},
        lambda: path.write_text("one\ntwo\n", encoding="utf-8"),
    )
    assert len(changes) == 1
    assert changes[0]["operation"] == "create"
    assert (changes[0]["added"], changes[0]["removed"]) == (2, 0)
    assert changes[0]["diff"].startswith("--- a/hello.txt")

    result = store.undo([changes[0]["snapshot_id"]], "undo-create")
    assert result["ok"] is True
    assert not path.exists()
    store.commit_undo("undo-create")
    assert store.undo([changes[0]["snapshot_id"]], "undo-create")["idempotent_replay"] is True


def test_runtime_callback_captures_real_write_file_invocation(tmp_path):
    sys.path.insert(0, str(ROOT / "app"))
    import agent_tools

    workspace = tmp_path / "workspace"; workspace.mkdir()
    session_dir = tmp_path / "session"

    class Manager:
        def _get_session_path(self, _session_id):
            return session_dir

    runtime = _load_plugin_module("runtime.py", "test_change_review_runtime")
    callbacks = runtime.initialize(SimpleNamespace(session_manager=Manager()))
    state = {"session_id": "write-session", "_runtime_v2_run_id": "write-run"}
    with agent_tools.tool_work_dir_override(workspace):
        capture_state = callbacks["before_native_file_tool"](
            state,
            "write_file",
            {"path": "created.txt", "contents": "hello\n"},
            "write-call",
            "",
        )
        result = agent_tools.write_file(path="created.txt", contents="hello\n")
        changes = callbacks["after_native_file_tool"](state, capture_state, result.startswith("Successfully"))
    assert result.startswith("Successfully wrote file")
    assert len(changes) == 1
    assert changes[0]["path"] == "created.txt"


def test_runtime_callback_can_observe_an_unknown_external_tool(tmp_path):
    sys.path.insert(0, str(ROOT / "app"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    init_git_workspace(workspace)
    session_dir = tmp_path / "session"

    class Manager:
        def _get_session_path(self, _session_id):
            return session_dir

    runtime = _load_plugin_module("runtime.py", "test_change_review_external_runtime")
    callbacks = runtime.initialize(SimpleNamespace(session_manager=Manager()))
    state = {"session_id": "external-session", "_runtime_v2_run_id": "external-run"}
    capture_state = callbacks["before_native_file_tool"](
        state, "third_party_writer", {}, "external-call", str(workspace), True
    )
    (workspace / "external.txt").write_text("created externally\n", encoding="utf-8")
    changes = callbacks["after_native_file_tool"](state, capture_state, True)

    assert len(changes) == 1
    assert changes[0]["path"] == "external.txt"
    assert changes[0]["operation"] == "create"
    callbacks["after_run"](state)
    stored = json.loads((session_dir / "change_reviews/index.json").read_text(encoding="utf-8"))
    assert stored["baselines"] == {}
    assert not list((session_dir / "change_reviews/baselines").glob("*.zip"))
    store = _load_store().FileChangeReviewStore(session_dir)
    store.undo([changes[0]["snapshot_id"]], "undo-after-baseline-cleanup")
    assert not (workspace / "external.txt").exists()


def test_real_apply_patch_keeps_line_endings_and_reports_hunk_diff(review):
    store, workspace = review
    import agent_tools

    path = workspace / "patched.py"
    path.write_bytes(b"alpha\nbeta\ngamma\n")
    patch = "\n".join([
        "*** Begin Patch",
        "*** Update File: patched.py",
        "@@",
        " alpha",
        "-beta",
        "+delta",
        " gamma",
        "*** End Patch",
    ])

    with agent_tools.tool_work_dir_override(workspace):
        started = store.begin_capture(
            "apply_patch", {"patch": patch}, run_id="patch-run",
            tool_call_id="patch-call", work_root=workspace,
        )
        result = agent_tools.apply_patch(patch)
        changes = store.finish_capture(started, successful=result.startswith("Done!"))

    assert result.startswith("Done!")
    assert path.read_bytes() == b"alpha\ndelta\ngamma\n"
    assert len(changes) == 1
    assert (changes[0]["added"], changes[0]["removed"]) == (1, 1)
    assert changes[0]["diff"].count("+delta") == 1
    assert "-alpha" not in changes[0]["diff"]


def test_real_apply_patch_does_not_turn_large_lf_file_into_full_file_diff(review):
    store, workspace = review
    import agent_tools

    path = workspace / "large_patch.py"
    original_lines = [f"value_{index} = {index}" for index in range(1, 1801)]
    path.write_bytes(("\n".join(original_lines) + "\n").encode("utf-8"))
    patch = "\n".join([
        "*** Begin Patch",
        "*** Update File: large_patch.py",
        "@@",
        " value_899 = 899",
        "-value_900 = 900",
        "-value_901 = 901",
        "+value_900 = 'changed'",
        "+value_901 = 'changed'",
        "+value_901_5 = 'inserted'",
        " value_902 = 902",
        "*** End Patch",
    ])

    with agent_tools.tool_work_dir_override(workspace):
        started = store.begin_capture(
            "apply_patch", {"patch": patch}, run_id="large-patch-run",
            tool_call_id="large-patch-call", work_root=workspace,
        )
        result = agent_tools.apply_patch(patch)
        changes = store.finish_capture(started, successful=result.startswith("Done!"))

    assert result.startswith("Done!")
    assert b"\r\n" not in path.read_bytes()
    assert len(changes) == 1
    assert (changes[0]["added"], changes[0]["removed"]) == (3, 2)
    assert changes[0]["diff"].count("+value_901_5 = 'inserted'") == 1
    assert "-value_1 = 1" not in changes[0]["diff"]


def test_same_round_same_file_is_cumulative(review):
    store, workspace = review
    path = workspace / "same.txt"
    path.write_text("a\nb\n", encoding="utf-8")
    first = capture(
        store, workspace, "edit_file", {"path": "same.txt"},
        lambda: path.write_text("a\nc\n", encoding="utf-8"), call="call-1",
    )[0]
    second = capture(
        store, workspace, "edit_file", {"path": "same.txt"},
        lambda: path.write_text("a\nc\nd\n", encoding="utf-8"), call="call-2",
    )[0]
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["revision"] == 2
    assert (second["added"], second["removed"]) == (2, 1)
    store.undo([second["snapshot_id"]], "undo-cumulative")
    assert path.read_text(encoding="utf-8") == "a\nb\n"


def test_noop_has_no_change_but_failed_tool_reports_actual_partial_write(review):
    store, workspace = review
    path = workspace / "unchanged.txt"
    path.write_text("same", encoding="utf-8")
    assert capture(store, workspace, "edit_file", {"path": "unchanged.txt"}, lambda: None) == []
    assert capture(store, workspace, "apply_patch", {"patch": "invalid"}, lambda: None) == []

    started = store.begin_capture(
        "write_file", {"path": "unchanged.txt"}, run_id="failed-run",
        tool_call_id="failed-call", work_root=workspace,
    )
    path.write_text("partially-written", encoding="utf-8")
    partial = store.finish_capture(started, successful=False)
    assert len(partial) == 1
    assert partial[0]["operation"] == "modify"
    assert (partial[0]["added"], partial[0]["removed"]) == (1, 1)


def test_same_round_return_to_baseline_is_not_undoable(review):
    module = _load_store()
    store, workspace = review
    path = workspace / "baseline.txt"
    path.write_text("before\n", encoding="utf-8")
    first = capture(
        store, workspace, "edit_file", {"path": "baseline.txt"},
        lambda: path.write_text("after\n", encoding="utf-8"),
    )[0]
    second = capture(
        store, workspace, "edit_file", {"path": "baseline.txt"},
        lambda: path.write_text("before\n", encoding="utf-8"), call="call-2",
    )[0]
    assert second["effective"] is False
    with pytest.raises(module.SnapshotGoneError):
        store.undo([first["snapshot_id"]], "undo-neutralized")


def test_git_process_baseline_tracks_non_native_changes_and_net_zero_cleanup(tmp_path):
    module = _load_store()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    init_git_workspace(workspace)
    tracked = workspace / "tracked.txt"
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    store = module.FileChangeReviewStore(tmp_path / "session")

    scratch = workspace / ".playwright-mcp" / "page.yml"
    first = capture(
        store,
        workspace,
        "mcp_browser_snapshot",
        {},
        lambda: (scratch.parent.mkdir(), scratch.write_text("temporary\n", encoding="utf-8")),
        run="process-1",
        call="mcp-1",
    )
    assert len(first) == 1
    assert first[0]["path"] == ".playwright-mcp/page.yml"
    assert first[0]["operation"] == "create"
    assert (first[0]["added"], first[0]["removed"]) == (1, 0)

    cleaned = capture(
        store,
        workspace,
        "delete_file",
        {"path": str(scratch.parent)},
        lambda: shutil.rmtree(scratch.parent),
        run="process-1",
        call="native-delete",
    )
    row = next(item for item in cleaned if item["snapshot_id"] == first[0]["snapshot_id"])
    assert row["effective"] is False
    with pytest.raises(module.SnapshotGoneError):
        store.undo([first[0]["snapshot_id"]], "undo-net-zero")


def test_git_process_baseline_reports_true_insertions_and_deletions(tmp_path):
    module = _load_store()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    init_git_workspace(workspace)
    path = workspace / "tracked.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    store = module.FileChangeReviewStore(tmp_path / "session")

    inserted = capture(
        store,
        workspace,
        "run_shell",
        {"command": "append"},
        lambda: path.write_text("one\ntwo\nthree\n", encoding="utf-8"),
        run="insert-run",
    )
    assert len(inserted) == 1
    assert (inserted[0]["added"], inserted[0]["removed"]) == (1, 0)
    store.undo([inserted[0]["snapshot_id"]], "undo-insert")
    store.commit_undo("undo-insert")

    deleted = capture(
        store,
        workspace,
        "mcp_delete",
        {},
        path.unlink,
        run="delete-run",
    )
    assert len(deleted) == 1
    assert deleted[0]["operation"] == "delete"
    assert (deleted[0]["added"], deleted[0]["removed"]) == (0, 2)
    store.undo([deleted[0]["snapshot_id"]], "undo-delete")
    assert path.read_text(encoding="utf-8") == "one\ntwo\n"


def test_gitignore_change_does_not_turn_an_existing_file_into_a_fake_delete(tmp_path):
    module = _load_store()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    init_git_workspace(workspace)
    visible = workspace / "visible.txt"
    visible.write_text("still here\n", encoding="utf-8")
    store = module.FileChangeReviewStore(tmp_path / "session")

    changes = capture(
        store,
        workspace,
        "run_shell",
        {},
        lambda: (workspace / ".gitignore").write_text("visible.txt\n", encoding="utf-8"),
        run="ignore-run",
    )

    assert [row["path"] for row in changes] == [".gitignore"]
    assert visible.read_text(encoding="utf-8") == "still here\n"


def test_binary_and_large_file_omit_line_diff(review):
    store, workspace = review
    binary = workspace / "binary.dat"
    binary.write_bytes(b"before\x00bytes")
    binary_change = capture(
        store, workspace, "write_file", {"path": "binary.dat"},
        lambda: binary.write_bytes(b"after\x00bytes"),
    )[0]
    assert binary_change["diff"] is None
    assert binary_change["diff_omitted_reason"] == "binary"
    assert binary_change["added"] is None

    large = workspace / "large.txt"
    large.write_bytes(b"a" * (1024 * 1024 + 1))
    large_change = capture(
        store, workspace, "write_file", {"path": "large.txt"},
        lambda: large.write_bytes(b"b" * (1024 * 1024 + 1)), run="run-2",
    )[0]
    assert large_change["diff_omitted_reason"] == "too_large_bytes"


def test_text_diff_normalizes_line_endings_instead_of_reporting_whole_file(review):
    store, workspace = review
    path = workspace / "crlf.py"
    path.write_bytes(b"line one\r\nline two\r\n")
    change = capture(
        store,
        workspace,
        "write_file",
        {"path": "crlf.py", "contents": "line one\nline two\n# comment\n"},
        lambda: path.write_bytes(b"line one\nline two\n# comment\n"),
    )[0]
    assert (change["added"], change["removed"]) == (1, 0)
    assert "-line one" not in change["diff"]
    assert "+# comment" in change["diff"]


def test_same_round_text_return_to_baseline_ignores_newline_only_rewrite(review):
    store, workspace = review
    path = workspace / "roundtrip.py"
    path.write_bytes(b"before\r\n")
    first = capture(
        store,
        workspace,
        "write_file",
        {"path": "roundtrip.py", "contents": "before\n# marker\n"},
        lambda: path.write_bytes(b"before\n# marker\n"),
        run="newline-roundtrip",
    )[0]
    second = capture(
        store,
        workspace,
        "edit_file",
        {"path": "roundtrip.py"},
        lambda: path.write_bytes(b"before\n"),
        run="newline-roundtrip",
        call="roundtrip-2",
    )[0]
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["effective"] is False


def test_batch_conflict_aborts_without_restoring_any_file(review):
    module = _load_store()
    store, workspace = review
    one = workspace / "one.txt"; two = workspace / "two.txt"
    one.write_text("old-one", encoding="utf-8"); two.write_text("old-two", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: one.txt\n@@\n-old-one\n+new-one\n*** Update File: two.txt\n@@\n-old-two\n+new-two\n*** End Patch"
    changes = capture(
        store, workspace, "apply_patch", {"patch": patch},
        lambda: (one.write_text("new-one", encoding="utf-8"), two.write_text("new-two", encoding="utf-8")),
    )
    two.write_text("third-party", encoding="utf-8")
    with pytest.raises(module.ChangeConflictError) as raised:
        store.undo([row["snapshot_id"] for row in changes], "undo-conflict")
    assert raised.value.paths == ["two.txt"]
    assert one.read_text(encoding="utf-8") == "new-one"
    assert two.read_text(encoding="utf-8") == "third-party"


def test_directory_delete_restores_files_and_empty_directories(review):
    store, workspace = review
    root = workspace / "tree"; empty = root / "empty"; nested = root / "nested"
    empty.mkdir(parents=True); nested.mkdir(); (nested / "value.txt").write_text("value", encoding="utf-8")
    changes = capture(
        store, workspace, "delete_file", {"path": "tree"},
        lambda: __import__("shutil").rmtree(root),
    )
    assert [row["path"] for row in changes] == ["tree/nested/value.txt"]
    store.undo([changes[0]["snapshot_id"]], "undo-directory")
    assert (nested / "value.txt").read_text(encoding="utf-8") == "value"
    assert empty.is_dir()

    only_empty = workspace / "only-empty"
    only_empty.mkdir()
    empty_change = capture(
        store, workspace, "delete_file", {"path": "only-empty"},
        lambda: only_empty.rmdir(), run="run-empty",
    )[0]
    assert empty_change["diff_omitted_reason"] == "directory"
    store.undo([empty_change["snapshot_id"]], "undo-empty-directory")
    assert only_empty.is_dir()


def test_prepared_undo_can_roll_back_when_event_commit_fails(review):
    store, workspace = review
    path = workspace / "rollback.txt"; path.write_text("before", encoding="utf-8")
    change = capture(
        store, workspace, "edit_file", {"path": "rollback.txt"},
        lambda: path.write_text("after", encoding="utf-8"),
    )[0]
    store.undo([change["snapshot_id"]], "undo-rollback")
    assert path.read_text(encoding="utf-8") == "before"
    store.rollback_undo("undo-rollback")
    assert path.read_text(encoding="utf-8") == "after"
    assert json.loads(store.index_path.read_text(encoding="utf-8"))["operations"] == {}


def test_branch_copy_and_truncation_cleanup_keep_only_referenced_snapshots(review, tmp_path):
    store, workspace = review
    first_path = workspace / "first.txt"; second_path = workspace / "second.txt"
    first = capture(
        store, workspace, "write_file", {"path": "first.txt"},
        lambda: first_path.write_text("first", encoding="utf-8"),
    )[0]
    second = capture(
        store, workspace, "write_file", {"path": "second.txt"},
        lambda: second_path.write_text("second", encoding="utf-8"), run="run-2",
    )[0]
    branch_dir = tmp_path / "branch"
    store.copy_referenced_to(branch_dir, [first["snapshot_id"]])
    branch_index = json.loads((branch_dir / "change_reviews/index.json").read_text(encoding="utf-8"))
    assert set(branch_index["records"]) == {first["snapshot_id"]}

    store.prune_unreferenced([second["snapshot_id"]])
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert index["records"][first["snapshot_id"]]["reverted"] is True
    assert index["records"][second["snapshot_id"]]["reverted"] is False


def test_manifest_exposes_trusted_plugin_owned_web_and_runtime():
    manifest = json.loads(
        (ROOT / "plugins/change-review/.myagent-plugin/plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["id"] == "change-review"
    assert manifest["capabilities"]["trusted_host"]["workflow_runtime"] == "runtime.py"
    assert manifest["capabilities"]["ui"]["chat.extension"][0]["renderer"]["module"] == "change-review.js"

    from agent_extensions import load_plugins
    from plugins.ui import plugin_ui_contributions

    plugin = next(item for item in load_plugins(force=True).plugins if item.plugin_id == "change-review")
    contribution = next(item for item in plugin_ui_contributions(plugin) if item["slot"] == "chat.extension")
    assert contribution["renderer"]["module"].startswith("/plugin-assets/change-review/change-review.js?v=")

    agent_loop_source = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    assert agent_loop_source.count("observe_workspace=True") == 2
    assert "ToolInvocationKind.MCP" in agent_loop_source
    assert "ToolInvocationKind.PLUGIN" in agent_loop_source


def test_tool_finished_ui_changes_round_trip_without_entering_model_history(tmp_path):
    sys.path.insert(0, str(ROOT / "app"))
    from runtime_v2.history_ops import RuntimeHistoryOps
    from runtime_v2.mirror import RuntimeMirror
    from runtime_v2.model_projection import RuntimeModelProjection
    from runtime_v2.ui_projection import RuntimeUiProjection

    session_id = "review-runtime"
    change = {
        "path": "demo.txt", "snapshot_id": "opaque", "revision": 1,
        "diff": "--- a/demo.txt\n+++ b/demo.txt\n+secret-ui-only\n",
        "added": 1, "removed": 0,
    }
    mirrored = RuntimeMirror(tmp_path).mirror_ui_event(session_id, {
        "type": "tool_call", "tool": "write_file", "args": {"path": "demo.txt"},
        "result": "Successfully wrote file: demo.txt", "tool_call_id": "call-1",
        "ui": {"changes": [change]},
    })
    assert mirrored is not None and mirrored.type == "tool_finished"
    assert mirrored.payload["ui"]["changes"][0]["snapshot_id"] == "opaque"
    RuntimeHistoryOps(tmp_path).append_model_message(
        session_id, "tool", "Successfully wrote file: demo.txt", tool_call_id="call-1"
    )
    projected = RuntimeUiProjection(tmp_path).read_ui_events(session_id)
    assert projected[0]["ui"]["changes"][0]["diff"] == change["diff"]
    model = RuntimeModelProjection(tmp_path).read_message_dicts(session_id)
    assert model[-1]["content"] == "Successfully wrote file: demo.txt"
    assert "secret-ui-only" not in json.dumps(model, ensure_ascii=False)


def test_undo_api_persists_ui_event_and_notifies_child_and_parent(review, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import plugins.host as plugin_host
    import session_event_bus

    store, workspace = review
    path = workspace / "api.txt"; path.write_text("before", encoding="utf-8")
    change = capture(
        store, workspace, "edit_file", {"path": "api.txt"},
        lambda: path.write_text("after", encoding="utf-8"),
    )[0]

    class Manager:
        repository = SimpleNamespace(sessions_dir=tmp_path, _path_resolver=None)

        def _get_session_path(self, session_id):
            return tmp_path / "session" if session_id == "child" else tmp_path / session_id

        def _load_metadata(self, session_id):
            return {"parent_session_id": "root"} if session_id == "child" else {}

        def get_subagent_parent_id(self, session_id):
            return "root" if session_id == "child" else None

        def list_sessions(self, include_archived=False):
            return [{"id": "root"}]

        def list_subagent_descendants(self, root):
            return ["child"] if root == "root" else []

        def append_ui_event(self, session_id, event):
            persisted.append((session_id, event))

    persisted = []
    notices = []
    published = []
    host = _load_plugin_module("host.py", "test_change_review_host")
    monkeypatch.setattr(plugin_host, "bundled_host_plugin_enabled", lambda _plugin_id: True)
    monkeypatch.setattr(host, "_append_model_notice", lambda _manager, sid, text: notices.append((sid, text)))

    async def publish(session_id, event):
        published.append((session_id, event))

    monkeypatch.setattr(session_event_bus, "publish_session_event", publish)
    app = FastAPI()
    host.install(app, {"session_manager": Manager()}, SimpleNamespace(plugin_id="change-review"))
    with TestClient(app) as client:
        response = client.post(
            "/sessions/child/change-reviews/undo",
            json={"snapshot_ids": [change["snapshot_id"]], "operation_id": "api-undo"},
        )
    assert response.status_code == 200, response.text
    assert path.read_text(encoding="utf-8") == "before"
    assert persisted[0][1]["type"] == "file_changes_reverted"
    assert [session_id for session_id, _text in notices] == ["child", "root"]
    assert [session_id for session_id, _event in published] == ["child", "root"]
    assert published[-1][1]["agent_id"] == "child"
