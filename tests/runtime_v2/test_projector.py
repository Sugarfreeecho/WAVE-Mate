import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from app.runtime_v2 import RuntimeGateway, RuntimeHistoryOps, RuntimeModelProjection, RuntimeProjector
from app.runtime_v2.event_schema import RuntimeEvent


class RuntimeProjectorTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"RUNTIME_VERSION": "2"}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_project_run_terminal_state(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="run_started", session_id="s1", run_id="r1"),
            RuntimeEvent(seq=2, type="run_failed", session_id="s1", run_id="r1", payload={"error": "boom"}),
        ]
        snapshot = projector.project(events)

        self.assertEqual(snapshot["last_seq"], 2)
        self.assertEqual(snapshot["runs"]["r1"]["status"], "failed")
        self.assertEqual(snapshot["runs"]["r1"]["error"], "boom")
        self.assertEqual(snapshot["active_runs"], [])

    def test_terminal_run_is_not_reopened_by_late_started_event(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="run_interrupted", session_id="s1", run_id="r1"),
            RuntimeEvent(seq=2, type="run_started", session_id="s1", run_id="r1"),
        ]

        snapshot = projector.project(events)

        self.assertEqual(snapshot["runs"]["r1"]["status"], "interrupted")
        self.assertEqual(snapshot["active_runs"], [])

    def test_assistant_final_marks_run_finalizing_until_terminal(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="run_started", session_id="s1", run_id="r1"),
            RuntimeEvent(
                seq=2,
                type="assistant_final_committed",
                session_id="s1",
                run_id="r1",
                payload={"content": "done"},
            ),
        ]

        snapshot = projector.project(events)
        self.assertEqual(snapshot["runs"]["r1"]["status"], "running")
        self.assertEqual(snapshot["runs"]["r1"]["phase"], "finalizing")
        self.assertEqual(snapshot["runs"]["r1"]["response_committed_seq"], 2)
        self.assertEqual(len(snapshot["active_runs"]), 1)

        projector.apply(
            snapshot,
            RuntimeEvent(seq=3, type="run_finished", session_id="s1", run_id="r1"),
        )
        self.assertEqual(snapshot["runs"]["r1"]["phase"], "terminal")

    def test_new_run_supersedes_previous_unfinished_run(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="run_started", session_id="s1", run_id="stale"),
            RuntimeEvent(seq=2, type="run_started", session_id="s1", run_id="newer"),
            RuntimeEvent(seq=3, type="run_finished", session_id="s1", run_id="newer"),
        ]

        snapshot = projector.project(events)

        self.assertEqual(snapshot["runs"]["stale"]["status"], "interrupted")
        self.assertEqual(snapshot["runs"]["newer"]["status"], "finished")
        self.assertEqual(snapshot["active_runs"], [])

    def test_projects_context_tokens_and_legacy_todo_as_extension_state(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="context_tokens", session_id="s1", payload={"estimated": 123, "threshold": 1000}),
            RuntimeEvent(seq=2, type="todo_updated", session_id="s1", payload={
                "has_plan": True,
                "items": [{"id": "t1", "text": "Do it", "status": "pending"}],
                "done": 0,
                "total": 1,
            }),
        ]

        snapshot = projector.project(events)

        self.assertEqual(snapshot["context"]["tokens"]["estimated"], 123)
        self.assertEqual(snapshot["context"]["tokens"]["seq"], 1)
        todo = snapshot["extensions"]["session-todo"]["plan"]
        self.assertEqual(todo["value"]["total"], 1)
        self.assertEqual(todo["seq"], 2)
        self.assertEqual(todo["value"]["items"][0]["id"], "t1")
        self.assertNotIn("todo", snapshot)
        self.assertNotIn("todo", snapshot["context"])

    def test_projects_goal_accounting_delta_on_top_of_checkpoint(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="goal_created", session_id="s1", payload={
                "id": "goal-1",
                "objective": "Ship it",
                "version": 1,
                "status": "active",
                "used_tokens": 0,
                "accounted_usage_ids": [],
            }),
            RuntimeEvent(seq=2, type="goal_usage_updated", session_id="s1", payload={
                "_goal_delta": True,
                "id": "goal-1",
                "set": {"version": 2, "used_tokens": 9},
                "append": {"accounted_usage_ids": ["run-1:llm:0"]},
            }),
        ]

        snapshot = projector.project(events)

        goal = snapshot["extensions"]["agent-goal"]["goal"]
        self.assertEqual(goal["value"]["objective"], "Ship it")
        self.assertEqual(goal["value"]["version"], 2)
        self.assertEqual(goal["value"]["used_tokens"], 9)
        self.assertEqual(goal["value"]["accounted_usage_ids"], ["run-1:llm:0"])
        self.assertEqual(goal["seq"], 2)
        self.assertNotIn("goal", snapshot)
        self.assertNotIn("goal", snapshot["context"])

    def test_gateway_rebuilds_and_reads_snapshot(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                gateway = RuntimeGateway(tmp)
                await gateway.append_event("s1", "message_user", {"content": "hello"})
                await gateway.start_run("s1", run_id="r1")
                await gateway.finish_run("s1", "r1")

                snapshot = gateway.rebuild_session_state("s1")
                cached = gateway.read_snapshot("s1")

                self.assertEqual(snapshot["last_seq"], 3)
                self.assertEqual(cached["last_seq"], 3)
                self.assertEqual(cached["messages"][0]["role"], "user")
                self.assertEqual(cached["runs"]["r1"]["status"], "finished")

        asyncio.run(scenario())

    def test_projects_native_model_messages_with_tool_order(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="message_user", session_id="s1", payload={"content": "visible"}),
            RuntimeEvent(seq=2, type="model_user", session_id="s1", payload={"role": "user", "content": "model u"}),
            RuntimeEvent(seq=3, type="model_assistant", session_id="s1", payload={
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "read_file", "args": {"path": "a"}, "id": "tc1"}],
            }),
            RuntimeEvent(seq=4, type="model_tool", session_id="s1", payload={
                "role": "tool",
                "content": "tool result",
                "tool_call_id": "tc1",
            }),
            RuntimeEvent(seq=5, type="model_assistant", session_id="s1", payload={
                "role": "assistant",
                "content": "done",
                "metadata": {"is_final": True},
            }),
        ]

        snapshot = projector.project(events)

        self.assertEqual(len(snapshot["visible_messages"]), 1)
        self.assertEqual([m["role"] for m in snapshot["model_messages"]], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(snapshot["model_messages"][1]["payload"]["tool_calls"][0]["id"], "tc1")
        self.assertEqual(snapshot["model_messages"][2]["payload"]["tool_call_id"], "tc1")

    def test_model_history_generation_changes_only_for_non_append_semantics(self):
        projector = RuntimeProjector()
        events = [
            RuntimeEvent(seq=1, type="model_user", session_id="s1", payload={"content": "append"}),
            RuntimeEvent(
                seq=2,
                type="model_history_replaced",
                session_id="s1",
                payload={"messages": [{"type": "user", "content": "replacement"}]},
            ),
            RuntimeEvent(seq=3, type="model_assistant", session_id="s1", payload={"content": "append"}),
            RuntimeEvent(
                seq=4,
                type="context_summary_committed",
                session_id="s1",
                payload={"summary": "metadata only"},
            ),
            RuntimeEvent(
                seq=5,
                type="history_compacted",
                session_id="s1",
                payload={"summary": "compact", "compacted_before_seq": 2},
            ),
            RuntimeEvent(
                seq=6,
                type="visible_range_changed",
                session_id="s1",
                payload={"to_seq": 2, "apply_model": True},
            ),
        ]

        generations = []
        snapshot = projector.empty_snapshot()
        for event in events:
            projector.apply(snapshot, event)
            generations.append(snapshot["model_history_generation"])

        self.assertEqual(generations, [0, 1, 1, 1, 2, 3])

    def test_branch_model_seed_keeps_replay_items_but_strips_server_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = RuntimeHistoryOps(tmp)
            ops.replace_model_history(
                "source",
                [
                    {"type": "user", "content": "first"},
                    {
                        "type": "assistant",
                        "content": "answer",
                        "additional_kwargs": {
                            "_myagent_responses": {
                                "schema_version": 2,
                                "issuer": "issuer-1",
                                "response_id": "resp_parent",
                                "continuation_anchor": {"response_id": "resp_parent"},
                                "canonical_output_items": [
                                    {
                                        "schema_version": 1,
                                        "issuer": "issuer-1",
                                        "replayability": "native",
                                        "raw_item": {
                                            "type": "message",
                                            "role": "assistant",
                                            "content": [],
                                        },
                                    }
                                ],
                            }
                        },
                    },
                ],
            )
            source_tail = ops.event_log.read_all("source")[-1].seq
            ops.create_branch("branch", "source", source_tail)

            messages = RuntimeModelProjection(tmp).read_message_dicts("branch")
            state = messages[1]["additional_kwargs"]["_myagent_responses"]
            context = RuntimeModelProjection(tmp).read_request_context("branch")

            self.assertNotIn("response_id", state)
            self.assertNotIn("continuation_anchor", state)
            self.assertEqual(state["state_mode"], "stateless")
            self.assertEqual(len(state["canonical_output_items"]), 1)
            self.assertEqual(context["lineage_id"], "source")
            self.assertGreaterEqual(context["history_generation"], 1)

    def test_model_projection_reads_message_dicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = RuntimeHistoryOps(tmp)
            ops.append_model_message("s1", "user", "hello")
            ops.append_model_message(
                "s1",
                "assistant",
                "",
                tool_calls=[{"name": "read_file", "args": {"path": "a"}, "id": "tc1"}],
                additional_kwargs={"reasoning_content": "why"},
            )
            ops.append_model_message("s1", "tool", "result", tool_call_id="tc1")

            messages = RuntimeModelProjection(tmp).read_message_dicts("s1")

            self.assertEqual([m["type"] for m in messages], ["user", "assistant", "tool"])
            self.assertEqual(messages[1]["tool_calls"][0]["id"], "tc1")
            self.assertEqual(messages[1]["additional_kwargs"]["reasoning_content"], "why")
            self.assertEqual(messages[2]["tool_call_id"], "tc1")

    def test_run_bootstrap_uses_snapshot_without_scanning_root_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = RuntimeHistoryOps(tmp)
            ops.append_model_message("s1", "user", "hello")
            ops.commit_context_summary("s1", "remember this")
            projection = RuntimeModelProjection(tmp)

            with patch.object(
                projection.event_log,
                "iter_events",
                side_effect=AssertionError("ordinary bootstrap must not scan events.jsonl"),
            ):
                bootstrap = projection.read_run_bootstrap("s1")

            self.assertEqual(bootstrap["context_summary"], "remember this")
            self.assertEqual(
                [message["content"] for message in bootstrap["messages"]],
                ["hello"],
            )

    def test_model_projection_backfills_legacy_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            projection = RuntimeModelProjection(tmp)

            count = projection.ensure_backfilled_from_legacy("s1", [
                {"type": "user", "content": "legacy"},
                {"type": "assistant", "content": "answer", "metadata": {"is_final": True}},
            ])
            second = projection.ensure_backfilled_from_legacy("s1", [
                {"type": "user", "content": "ignored"},
            ])
            messages = projection.read_message_dicts("s1")

            self.assertEqual(count, 2)
            self.assertEqual(second, 0)
            self.assertEqual([m["content"] for m in messages], ["legacy", "answer"])

    def test_model_projection_sync_reports_partial_projection_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = RuntimeHistoryOps(tmp)
            ops.append_model_message("s1", "user", "partial")
            projection = RuntimeModelProjection(tmp)

            result = projection.sync_from_legacy_if_needed("s1", [
                {"type": "user", "content": "legacy"},
                {"type": "assistant", "content": "answer"},
            ])
            messages = projection.read_message_dicts("s1")

            self.assertEqual(result["action"], "mismatch")
            self.assertEqual(result["written"], 0)
            self.assertEqual([m["content"] for m in messages], ["partial"])

    def test_model_projection_sync_does_not_overwrite_longer_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ops = RuntimeHistoryOps(tmp)
            ops.replace_model_history("s1", [
                {"type": "user", "content": "v2 user"},
                {"type": "assistant", "content": "v2 answer"},
                {"type": "tool", "content": "v2 tool"},
            ])
            projection = RuntimeModelProjection(tmp)

            result = projection.sync_from_legacy_if_needed("s1", [
                {"type": "user", "content": "legacy user"},
                {"type": "assistant", "content": "legacy answer"},
            ])
            messages = projection.read_message_dicts("s1")

            self.assertEqual(result["action"], "mismatch")
            self.assertEqual(result["written"], 0)
            self.assertEqual([m["content"] for m in messages], ["v2 user", "v2 answer", "v2 tool"])


if __name__ == "__main__":
    unittest.main()
