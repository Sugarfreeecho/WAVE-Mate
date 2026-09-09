import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.runtime_v2 import RuntimeEventLogBusyError, RuntimeMirror


class RuntimeMirrorTests(unittest.TestCase):
    def test_strict_append_surfaces_non_lock_persistence_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)

            @contextmanager
            def failing_transaction(_session_id, **_kwargs):
                raise OSError("disk unavailable")
                yield

            mirror.event_log.session_transaction = failing_transaction
            self.assertIsNone(mirror.append("s1", "run_finished"))
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                mirror.append("s1", "run_finished", raise_on_error=True)

    def test_online_lock_timeout_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            holder = RuntimeMirror(tmp)
            online = RuntimeMirror(tmp, transaction_timeout_seconds=0.05)
            acquired = threading.Event()
            release = threading.Event()

            def hold_transaction():
                with holder.event_log.session_transaction("s1"):
                    acquired.set()
                    release.wait(timeout=2)

            thread = threading.Thread(target=hold_transaction)
            thread.start()
            self.assertTrue(acquired.wait(timeout=1))
            try:
                with self.assertRaises(RuntimeEventLogBusyError):
                    online.append("s1", "message_user", {"content": "hello"})
            finally:
                release.set()
                thread.join(timeout=1)
            self.assertEqual(online.event_log.read_all("s1"), [])

    def test_mirrors_legacy_user_and_final_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {"type": "user", "content": "hello"})
            mirror.mirror_ui_event("s1", {"type": "final", "content": "done"})

            snapshot = mirror.snapshots.read("s1")

            self.assertEqual([m["role"] for m in snapshot["messages"]], ["user", "assistant"])
            self.assertEqual(snapshot["last_seq"], 2)

    def test_mirrors_user_steer_as_user_message_with_ui_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            event = mirror.mirror_ui_event("s1", {
                "type": "user_steer",
                "content": "follow up",
                "steer": True,
                "steer_id": "st1",
                "client_id": "c1",
            })

            snapshot = mirror.snapshots.read("s1")

            self.assertIsNotNone(event)
            self.assertEqual(event.type, "message_user")
            self.assertEqual(event.payload["ui_type"], "user_steer")
            self.assertEqual(snapshot["messages"][0]["role"], "user")

    def test_mirrors_run_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_run_started("s1", "r1")
            mirror.mirror_run_interrupted("s1", "r1")

            snapshot = mirror.snapshots.read("s1")

            self.assertEqual(snapshot["runs"]["r1"]["status"], "interrupted")
            self.assertEqual(snapshot["active_runs"], [])

    def test_mirrors_subagent_details_to_parent_local_subagent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("parent", {
                "type": "subagent_progress",
                "agent_id": "a1",
                "content": "working",
            })

            parent_snapshot = mirror.snapshots.read("parent")
            child_snapshot = mirror.subagents.read_snapshot("parent", "a1")

            self.assertIn("a1", parent_snapshot["subagents"])
            self.assertEqual(child_snapshot["messages"], [])
            self.assertTrue((mirror.sessions_dir / "parent" / "subagents" / "a1" / "events.jsonl").exists())

    def test_externalizes_large_tool_text_to_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {
                "type": "tool_result",
                "tool": "shell",
                "result": "x" * 17000,
            })

            event = mirror.event_log.read_all("s1")[0]
            ref = event.payload["result_ref"]

            self.assertTrue((mirror.sessions_dir / "s1" / ref["blob_ref"]).exists())
            self.assertEqual(ref["bytes"], 17000)

    def test_nested_session_blob_stays_in_resolved_subagent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp)
            nested_dir = sessions_dir / "parent" / "subagents" / "child"

            def resolver(session_id):
                return nested_dir if session_id == "child" else sessions_dir / session_id

            mirror = RuntimeMirror(sessions_dir, path_resolver=resolver)
            mirror.mirror_ui_event("child", {
                "type": "tool_result",
                "tool": "shell",
                "result": "x" * 17000,
            })

            event = mirror.event_log.read_all("child")[0]
            ref = event.payload["result_ref"]
            self.assertTrue((nested_dir / ref["blob_ref"]).is_file())
            self.assertFalse((sessions_dir / "child").exists())

    def test_mirrors_context_summary_body_as_committed_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {
                "type": "context_summary_body",
                "content": "summary text",
            })

            snapshot = mirror.snapshots.read("s1")
            event = mirror.event_log.read_all("s1")[0]

            self.assertEqual(event.type, "context_summary_committed")
            self.assertEqual(snapshot["context"]["summary"]["summary"], "summary text")

    def test_provider_cache_stats_become_context_token_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            event = mirror.mirror_ui_event("s1", {
                "type": "cache_stats",
                "input_tokens": 98765,
                "output_tokens": 12,
                "threshold": 128000,
                "model": "model-a",
                "context_token_mode": "hybrid",
            })

            snapshot = mirror.snapshots.read("s1")
            tokens = snapshot["context"]["tokens"]

            self.assertEqual(event.type, "context_tokens")
            self.assertEqual(tokens["estimated"], 98765)
            self.assertEqual(tokens["token_source"], "provider_exact")
            self.assertEqual(tokens["source"], "provider_usage")
            self.assertEqual(tokens["token_mode"], "hybrid")


if __name__ == "__main__":
    unittest.main()
