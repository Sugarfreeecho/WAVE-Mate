import asyncio
import inspect
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_live_tool_delta_replay_is_compacted_without_losing_prefix():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-tool"
        await bus.close_session_stream(sid)
        for index in range(600):
            await bus.publish_session_event(
                sid,
                {
                    "type": "tool_call_delta",
                    "ephemeral": True,
                    "react_iter": 2,
                    "stream_seq": 3,
                    "delta_seq": index + 1,
                    "index": 0,
                    "id": "call-1" if index == 0 else "",
                    "name_delta": "task" if index == 0 else "",
                    "arguments_delta": str(index % 10),
                },
            )
        subscription = bus.subscribe_session_events(sid, replay_recent=True)
        replay = await asyncio.wait_for(subscription.__anext__(), timeout=1)
        await subscription.aclose()
        await bus.close_session_stream(sid)
        return replay

    replay = asyncio.run(scenario())
    assert replay["type"] == "tool_call_delta"
    assert replay["name_delta"] == "task"
    assert replay["arguments_delta"] == "".join(str(i % 10) for i in range(600))
    assert replay["replayed_snapshot"] is True
    assert replay["seq_scope"] == "event_bus"


def test_completed_tool_prunes_live_delta_snapshot():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-prune"
        await bus.close_session_stream(sid)
        await bus.publish_session_event(
            sid,
            {
                "type": "tool_call_delta",
                "ephemeral": True,
                "react_iter": 1,
                "stream_seq": 1,
                "delta_seq": 1,
                "index": 0,
                "id": "call-prune",
                "name_delta": "task",
                "arguments_delta": "{}",
            },
        )
        await bus.publish_session_event(
            sid,
            {
                "type": "tool_pending",
                "ephemeral": True,
                "react_iter": 1,
                "tool_call_id": "call-prune",
                "tool": "task",
                "args": {},
            },
        )
        await bus.publish_session_event(
            sid,
            {
                "type": "tool_call",
                "react_iter": 1,
                "tool_call_id": "call-prune",
                "tool": "task",
            },
        )
        assert not bus._live_delta_snapshots.get(sid)
        assert not [
            event
            for event in bus._recent_ephemeral.get(sid, ())
            if event.get("type") == "tool_call_delta"
        ]
        assert not bus._live_state_snapshots.get(sid)
        await bus.close_session_stream(sid)

    asyncio.run(scenario())


def test_refresh_replay_keeps_running_tool_after_many_stream_chunks():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-running-tool"
        await bus.close_session_stream(sid)
        await bus.publish_session_event(
            sid,
            {
                "type": "tool_pending",
                "ephemeral": True,
                "react_iter": 3,
                "tool_call_index": 0,
                "tool_call_id": "call-running",
                "tool": "run_shell",
                "args": {"command": "long-running"},
            },
        )
        for index in range(800):
            await bus.publish_session_event(
                sid,
                {
                    "type": "tool_command_delta",
                    "ephemeral": True,
                    "react_iter": 3,
                    "tool_call_id": "call-running",
                    "stream_seq": 1,
                    "delta_seq": index + 1,
                    "delta": str(index % 10),
                },
            )
        subscription = bus.subscribe_session_events(sid, replay_recent=True)
        first = await asyncio.wait_for(subscription.__anext__(), timeout=1)
        second = await asyncio.wait_for(subscription.__anext__(), timeout=1)
        await subscription.aclose()
        await bus.close_session_stream(sid)
        return [first, second]

    replay = asyncio.run(scenario())
    assert [event["type"] for event in replay] == ["tool_pending", "tool_command_delta"]
    assert replay[0]["tool_call_id"] == "call-running"
    assert replay[1]["delta"] == "".join(str(i % 10) for i in range(800))


def test_refresh_replay_keeps_complete_context_summary_after_recent_buffer_limit():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-context-summary"
        run_id = "run-summary"
        await bus.close_session_stream(sid)
        for index in range(900):
            await bus.publish_session_event(
                sid,
                {
                    "type": "context_summary_delta",
                    "ephemeral": True,
                    "run_id": run_id,
                    "delta": str(index % 10),
                },
            )
        subscription = bus.subscribe_session_events(sid, replay_recent=True)
        replay = await asyncio.wait_for(subscription.__anext__(), timeout=1)
        await subscription.aclose()
        await bus.close_session_stream(sid)
        return replay

    replay = asyncio.run(scenario())
    assert replay["type"] == "context_summary_delta"
    assert replay["delta"] == "".join(str(i % 10) for i in range(900))
    assert replay["replayed_snapshot"] is True


def test_committed_context_body_prunes_its_live_delta_snapshot():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-context-committed"
        await bus.close_session_stream(sid)
        await bus.publish_session_event(
            sid,
            {
                "type": "context_summary_delta",
                "ephemeral": True,
                "run_id": "run-committed",
                "delta": "partial",
            },
        )
        await bus.publish_session_event(
            sid,
            {
                "type": "context_summary_body",
                "run_id": "run-committed",
                "content": "complete",
            },
        )
        assert not bus._live_delta_snapshots.get(sid)
        await bus.close_session_stream(sid)

    asyncio.run(scenario())


def test_terminal_event_prunes_only_the_terminated_run_ephemerals():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-terminal-run-boundary"
        await bus.close_session_stream(sid)
        for run_id, delta in (("old-run", "old"), ("replacement-run", "new")):
            await bus.publish_session_event(
                sid,
                {
                    "type": "context_summary_delta",
                    "ephemeral": True,
                    "run_id": run_id,
                    "delta": delta,
                },
            )
        await bus.publish_session_event(
            sid,
            {"type": "run_interrupted", "run_id": "old-run"},
        )
        subscription = bus.subscribe_session_events(sid, replay_recent=True)
        replay = await asyncio.wait_for(subscription.__anext__(), timeout=1)
        await subscription.aclose()
        await bus.close_session_stream(sid)
        return replay

    replay = asyncio.run(scenario())
    assert replay["run_id"] == "replacement-run"
    assert replay["delta"] == "new"


def test_ephemeral_terminal_event_is_not_replayed_and_prunes_run_state():
    import session_event_bus as bus

    async def scenario():
        sid = "stream-replay-ephemeral-terminal"
        await bus.close_session_stream(sid)
        await bus.publish_session_event(
            sid,
            {
                "type": "status",
                "content": "正在思考中...",
                "ephemeral": True,
                "run_id": "finished-run",
            },
        )
        await bus.publish_session_event(
            sid,
            {
                "type": "run_interrupted",
                "ephemeral": True,
                "run_id": "finished-run",
            },
        )
        replay = list(bus._recent_ephemeral.get(sid, ()))
        snapshots = dict(bus._live_delta_snapshots.get(sid, {}))
        states = dict(bus._live_state_snapshots.get(sid, {}))
        await bus.close_session_stream(sid)
        return replay, snapshots, states

    replay, snapshots, states = asyncio.run(scenario())
    assert replay == []
    assert snapshots == {}
    assert states == {}


def test_refresh_replay_compacts_thinking_status_and_clears_it_on_delta():
    import session_event_bus as bus

    async def replay_all(sid, count):
        subscription = bus.subscribe_session_events(sid, replay_recent=True)
        rows = []
        for _ in range(count):
            rows.append(await asyncio.wait_for(subscription.__anext__(), timeout=1))
        await subscription.aclose()
        return rows

    async def scenario():
        sid = "stream-replay-thinking-status"
        await bus.close_session_stream(sid)
        for index in range(30):
            await bus.publish_session_event(
                sid,
                {
                    "type": "status",
                    "content": "正在思考中...",
                    "ephemeral": True,
                    "heartbeat": index,
                },
            )
        compacted = await replay_all(sid, 1)
        await bus.publish_session_event(
            sid,
            {
                "type": "llm_reasoning_delta",
                "delta": "reasoning",
                "ephemeral": True,
                "react_iter": 1,
                "stream_seq": 1,
                "delta_seq": 1,
            },
        )
        after_delta = await replay_all(sid, 1)
        await bus.close_session_stream(sid)
        return compacted, after_delta

    compacted, after_delta = asyncio.run(scenario())
    assert [event["type"] for event in compacted] == ["status"]
    assert compacted[0]["heartbeat"] == 29
    assert [event["type"] for event in after_delta] == ["llm_reasoning_delta"]


def test_session_event_bus_wakes_subscriber_on_a_different_event_loop():
    import session_event_bus as bus

    sid = "cross-loop-event-bus"
    ready = threading.Event()
    received = []

    def subscriber_worker():
        async def receive_one():
            subscription = bus.subscribe_session_events(sid, replay_recent=False)
            pending = asyncio.create_task(subscription.__anext__())
            await asyncio.sleep(0)
            ready.set()
            received.append(await asyncio.wait_for(pending, timeout=1))
            await subscription.aclose()

        asyncio.run(receive_one())

    asyncio.run(bus.close_session_stream(sid))
    thread = threading.Thread(target=subscriber_worker, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    asyncio.run(bus.publish_session_event(sid, {"type": "status", "content": "awake"}))
    thread.join(timeout=1)
    asyncio.run(bus.close_session_stream(sid))

    assert not thread.is_alive()
    assert received and received[0]["content"] == "awake"


def test_run_task_cancellation_is_thread_safe_across_event_loops():
    import session_lifecycle

    sid = "cross-loop-cancel"
    ready = threading.Event()
    cancelled = threading.Event()

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_forever():
            try:
                ready.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = loop.create_task(run_forever())
        session_lifecycle.register_run_task(sid, task)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    asyncio.run(session_lifecycle.cancel_run_tasks([sid]))
    thread.join(timeout=1)

    assert cancelled.is_set()
    assert not thread.is_alive()
    assert not session_lifecycle.is_run_active(sid)


def test_registered_run_identity_becomes_inactive_at_durable_terminal():
    import session_lifecycle

    async def scenario():
        sid = "run-identity-terminal"
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        session_lifecycle.register_run_task(
            sid,
            task,
            run_id="run-1",
            mode="chat",
        )
        try:
            active = session_lifecycle.get_active_run_info(sid)
            assert active and active["run_id"] == "run-1"
            assert active["run_active"] is True

            session_lifecycle.mark_run_terminal(sid, "run-1")
            terminal = session_lifecycle.get_active_run_info(sid)
            assert terminal and terminal["run_id"] == "run-1"
            assert terminal["run_active"] is False
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert session_lifecycle.get_active_run_info(sid) is None

    asyncio.run(scenario())


def test_runtime_lifecycle_retries_before_exposing_terminal(monkeypatch):
    import agent_loop

    lifecycle = agent_loop._RuntimeV2RunLifecycle("s1", "r1", "chat")
    attempts = []
    marked = []

    def append_once(event_type, payload):
        attempts.append((event_type, dict(payload)))
        if len(attempts) < 3:
            raise OSError("temporary persistence failure")

    monkeypatch.setattr(lifecycle, "_append_once", append_once)
    monkeypatch.setattr(
        "session_lifecycle.mark_run_terminal",
        lambda session_id, run_id: marked.append((session_id, run_id)),
    )

    assert asyncio.run(lifecycle.commit("run_finished", {"mode": "chat"})) is True
    assert len(attempts) == 3
    assert attempts[-1][1]["mode"] == "chat"
    assert lifecycle.terminal_event_type == "run_finished"
    assert marked == [("s1", "r1")]
    assert asyncio.run(lifecycle.commit("run_finished", {"mode": "chat"})) is False


def test_runtime_lifecycle_never_marks_failed_terminal_commit(monkeypatch):
    import agent_loop

    lifecycle = agent_loop._RuntimeV2RunLifecycle("s1", "r1", "chat")
    monkeypatch.setattr(
        lifecycle,
        "_append_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(RuntimeError, match="lifecycle commit failed"):
        asyncio.run(lifecycle.commit("run_finished"))
    assert lifecycle.terminal_committed is False


def test_runtime_lifecycle_accepts_only_one_concurrent_terminal(monkeypatch):
    import agent_loop

    lifecycle = agent_loop._RuntimeV2RunLifecycle("s1", "r1", "chat")
    appended = []
    marked = []
    monkeypatch.setattr(
        lifecycle,
        "_append_once",
        lambda event_type, _payload: appended.append(event_type),
    )
    monkeypatch.setattr(
        "session_lifecycle.mark_run_terminal",
        lambda session_id, run_id: marked.append((session_id, run_id)),
    )

    async def scenario():
        return await asyncio.gather(
            lifecycle.commit("run_finished"),
            lifecycle.commit("run_interrupted"),
        )

    results = asyncio.run(scenario())
    assert sorted(results) == [False, True]
    assert len(appended) == 1
    assert lifecycle.terminal_event_type == appended[0]
    assert marked == [("s1", "r1")]


def test_stream_detach_does_not_request_user_interrupt():
    import agent_loop

    chat_source = inspect.getsource(agent_loop.astream_events)
    continuation_source = inspect.getsource(agent_loop.astream_events_continuation)
    for source in (chat_source, continuation_source):
        assert 'reason="disconnect"' not in source
        assert "task.add_done_callback(_discard_task_result)" in source

    react_source = inspect.getsource(agent_loop._react_node_once)
    assert "not _can_execute_closed_stream_tool" in react_source
    assert "recovered_closed_tool_calls" in react_source


def test_interrupt_waits_are_bounded_and_context_lock_timeout_returns():
    import agent_loop

    sid = "bounded-context-policy-wait"
    lock = agent_loop._context_policy_lock_for_session(sid)
    lock.acquire()
    try:
        started = time.monotonic()
        assert agent_loop._wait_context_policy_idle(sid, 0.02) is False
        assert time.monotonic() - started < 0.5
    finally:
        lock.release()

    react_source = inspect.getsource(agent_loop._react_node_once)
    assert "stream_worker_done_event.wait," in react_source
    assert "STREAM_WORKER_ABORT_TIMEOUT_SEC" in react_source
    assert "asyncio.to_thread(stream_worker_done_event.wait)" not in react_source


def test_frontend_terminal_cleanup_discards_tool_and_progress_drafts():
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    render_source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    sessions_source = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    end_run = sse_source.split("function endRunForClient", 1)[1].split(
        "async function readSseChunkWithIdleTimeout", 1
    )[0]
    assert "removeAbortedToolDraftRows(ctx, {});" in end_run
    assert "discardProgressStreamChunks(ctx);" in end_run
    assert "discardPartialStreams: parsed.type !== 'run_finished'" in sse_source
    assert "function discardProgressStreamChunks(ctx)" in render_source
    assert "historyHydrationStream.hidden = false" in sessions_source
    assert "vis.hidden = true" in sessions_source


def test_frontend_final_waits_for_authoritative_terminal_event():
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    shared_source = (ROOT / "frontend/src/app/modules/shared-state-and-dialogs.js").read_text(encoding="utf-8")
    reducer_source = (ROOT / "frontend/src/app/state/session-event-reducer.js").read_text(encoding="utf-8")

    final_handler = sse_source.split("if (parsed.type === 'final') {", 1)[1].split(
        "renderMessageRecord(runCtx", 1
    )[0]
    send_setup = sse_source.split("if (submitSessionIdInitial) {", 1)[1].split(
        "} else {", 1
    )[0]
    foreground_finalizer = sse_source.split(
        "if (runSessionId !== currentSessionId) {", 1
    )[1].split("if (getSessionRunState(runSessionId))", 1)[0]

    assert "function markSessionResultComplete(sessionId, status)" in shared_source
    assert "sessionUnreadComplete.add(sid)" in shared_source
    assert "markRunFinalSeen(runCtx);" in final_handler
    assert "markSessionResultComplete" not in final_handler
    assert "endRunForClient" not in final_handler
    assert "runCtx.terminalSeen = true;" in sse_source
    assert "Transport completion is not a run outcome" in sse_source
    assert "sessionStore.markRunFinalizing(sessionId, finalizingRunId)" in reducer_source
    assert "markSessionResultComplete(" in reducer_source
    assert "if (!options.fromQueue) clearSessionUnreadState(submitSessionIdInitial);" in send_setup
    assert "clearSessionUnreadState(runSessionId)" not in foreground_finalizer


def test_frontend_terminal_cleanup_never_creates_empty_process_groups():
    render_source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    existing_body = render_source.split("function getExistingProcessBody", 1)[1].split(
        "function autoResizeTextarea", 1
    )[0]
    temporary_cleanup = render_source.split("function removeTemporaryStatus", 1)[1].split(
        "function appendToolCallDelta", 1
    )[0]
    tool_cleanup = render_source.split("function removeAbortedToolDraftRows", 1)[1].split(
        "function appendToolPendingRow", 1
    )[0]

    assert "ensureProcessGroup" not in existing_body
    assert "getProcessBody(ctx)" not in temporary_cleanup
    assert "getExistingProcessBody(ctx)" in temporary_cleanup
    assert "getProcessBody(ctx)" not in tool_cleanup
    assert "getExistingProcessBody(ctx)" in tool_cleanup


def test_frontend_recovery_is_server_owned_and_transient_rows_are_stable():
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    render_source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    reducer_source = (ROOT / "frontend/src/app/state/session-event-reducer.js").read_text(encoding="utf-8")

    auto_resume = sse_source.split("function maybeAutoResumeInterruptedReact", 1)[1].split(
        "window.addEventListener('online'", 1
    )[0]
    temporary_upsert = render_source.split("function upsertTemporaryStatus", 1)[1].split(
        "function appendToolCallDelta", 1
    )[0]

    assert "?recovery=true" not in sse_source
    assert "startContinueAfterSubagents(sid, 'react')" not in sse_source
    assert "observeServerOwnedReactRecovery(sid)" in auto_resume
    assert "appendLog(ctx, '检测到上次运行未完成" not in auto_resume
    assert "row === lastRow" in temporary_upsert
    assert "setUiRuntimeText(scroller, nextText)" in temporary_upsert
    assert "if (sessionId && !ephemeral" in reducer_source


def test_all_closed_external_tools_can_start_before_finish_reason():
    import agent_loop

    for tool_name in ("read_file", "task", "write_file", "apply_patch", "run_shell", "mcp_remote"):
        assert agent_loop._can_execute_closed_stream_tool(tool_name) is True
    # This is an internal history-replacement control operation, not an external
    # command; it must run after the current assistant turn has been assembled.
    assert agent_loop._can_execute_closed_stream_tool("context_manage") is False


def test_early_tool_completion_waits_for_llm_commit_before_ui_emit():
    import agent_loop

    react_source = inspect.getsource(agent_loop._react_node_once)
    early_runner = react_source.split("async def _run_early_tool_call", 1)[1].split(
        "def _maybe_start_closed_tool_call", 1
    )[0]
    checkpoint = react_source.split("async def checkpoint_completed_tool_result", 1)[1].split(
        "call_record =", 1
    )[0]
    interrupted_commit = react_source.split("if steer_interrupted_this_call:", 1)[1].split(
        "if await _consume_steer_messages", 1
    )[0]

    # Execution still starts early, but a completed tool row can no longer be
    # persisted/published ahead of this react iteration's LLM rows.
    assert "asyncio.create_task(_run_early_tool_call" in react_source
    assert "_emit_tool_call_sse" not in early_runner
    assert "_emit_tool_call_sse" in checkpoint
    assert interrupted_commit.index('"type": "llm_response"') < interrupted_commit.index(
        "_emit_tool_call_sse"
    )


def test_early_tool_path_normalizes_names_and_records_short_circuit_failures():
    import agent_loop

    react_source = inspect.getsource(agent_loop._react_node_once)
    assert "row[\"name\"] = merge_streamed_tool_name(" in react_source
    assert "return short_circuit(" in react_source
    assert "_unknown_tool_result(tool_name, tool_args, tool_id)" in react_source


def test_consecutive_side_effecting_tools_reuse_the_previous_workspace_snapshot():
    import agent_loop

    react_source = inspect.getsource(agent_loop._react_node_once)
    execute_wrapper = react_source.split("async def execute_one(tool_call)", 1)[1].split(
        "# ---------- 2.6", 1
    )[0]

    assert "workspace_audit_tail_by_root" in react_source
    assert "audit_root in workspace_audit_tail_by_root" in execute_wrapper
    assert "workspace_audit_tail_by_root[audit_root] = after_workspace" in execute_wrapper
    assert 'audit_before_source = "previous_after"' in execute_wrapper


def test_frontend_uses_independent_sse_sequence_scopes_and_fast_reattach():
    store_source = (ROOT / "frontend/src/app/state/session-store.js").read_text(encoding="utf-8")
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    webui_source = (ROOT / "app/webui.py").read_text(encoding="utf-8")

    assert "sid + '::' + seqScope" in store_source
    assert "parsed.seq_scope || 'legacy'" in sse_source
    assert "scheduleActiveSessionReconnect(runSessionId, { delayMs: 120, failure: true })" in sse_source
    assert 'payload["seq_scope"] = "ui_projection"' in webui_source
    assert "subscription.__anext__()" in webui_source


def test_frontend_reconnect_counts_only_real_failures():
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    scheduler = sse_source.split("function scheduleActiveSessionReconnect", 1)[1].split(
        "async function processRewriteTruncateAsync", 1
    )[0]

    assert "STREAM_RECONNECT_MAX_ATTEMPTS = 10" in sse_source
    assert "function isStreamConsuming" in sse_source
    assert "run.ctx.streamConsuming" in sse_source
    assert "var countFailure = !!opts.failure" in scheduler
    assert "if (countFailure) state.attempts += 1" in scheduler
    assert scheduler.count("if (isStreamConsuming(sid))") >= 2
    assert "scheduleActiveSessionReconnect(sid, { failure: countFailure })" in scheduler
    assert "resetStreamReconnectState(sid);" in scheduler


def test_frontend_recovers_half_open_streams_after_sleep_and_ask_resolution():
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    interactions_source = (ROOT / "frontend/src/app/modules/human-interactions.js").read_text(encoding="utf-8")
    sessions_source = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    idle_reader = sse_source.split("async function readSseChunkWithIdleTimeout", 1)[1].split(
        "function handoffSessionStreamAfterHumanInteraction", 1
    )[0]
    handoff = sse_source.split("function handoffSessionStreamAfterHumanInteraction", 1)[1].split(
        "async function consumeAgentSseResponse", 1
    )[0]
    submit = interactions_source.split("async function submitHumanQuestion", 1)[1].split(
        "function resumeRecoveredHumanInteractionStream", 1
    )[0]
    reconcile = sessions_source.split("async function reconcileRunStateFromServer", 1)[1].split(
        "function showSessionLoadRetry", 1
    )[0]

    assert "SSE_RESUME_PROBE_TIMEOUT_MS = 20000" in sse_source
    assert "activeTimeoutMs = Math.min(activeTimeoutMs, SSE_RESUME_PROBE_TIMEOUT_MS)" in idle_reader
    assert "run.controller.abort()" in handoff
    assert "force: true" in handoff
    assert "resumeRecoveredHumanInteractionStream(card.dataset.sessionId, recoveryAfterIndex)" in submit
    assert "if (data.recovery_scheduled)" not in submit
    assert "run.submitted && run.ctx && run.ctx.streamConsuming" in reconcile
    assert "abortSessionRun(sid, 'reconcile-finished')" in reconcile


def test_frontend_inserts_live_react_rows_in_logical_phase_order():
    source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    helper = source.split("function insertReactOrderedFeedRow", 1)[1].split(
        "function createProcessFeedRow", 1
    )[0]
    creator = source.split("function createProcessFeedRow", 1)[1].split(
        "function appendLlmStreamDelta", 1
    )[0]

    assert "existingIter === iter && existingPhase > phase" in helper
    assert "body.insertBefore(row, existing)" in helper
    assert "insertReactOrderedFeedRow(body, row, type, streamOpts.reactIter, reactGenerationForContext(ctx))" in creator
    assert "data-react-generation" in creator


def test_pending_append_steer_stays_as_tail_anchor_until_committed():
    source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    sse_source = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    anchor_helper = source.split(
        "function appendProcessRowBeforePendingAppendSteer", 1
    )[1].split("function insertReactOrderedFeedRow", 1)[0]
    ordered_insert = source.split("function insertReactOrderedFeedRow", 1)[1].split(
        "function createProcessFeedRow", 1
    )[0]
    steer_renderer = sse_source.split("function appendSteerProcessMessage", 1)[1].split(
        "function appendPendingSteerToProcess", 1
    )[0]

    assert "type !== 'user-steer'" in anchor_helper
    assert '[data-steer-mode="append"][data-steer-pending="1"]' in anchor_helper
    assert "body.insertBefore(row, pendingAppendSteer)" in anchor_helper
    assert ordered_insert.count(
        "appendProcessRowBeforePendingAppendSteer(body, row, type)"
    ) == 2
    assert "existing.removeAttribute('data-steer-pending')" in steer_renderer
    assert "existing.dataset.steerCommitted = '1'" in steer_renderer
    assert "appendPendingSteerToProcess(sid, local);" in sse_source
    assert "appendPendingSteerToProcess(sid, latest);" in sse_source
    assert "serverState === 'queued' || serverState === 'claimed'" in sse_source
