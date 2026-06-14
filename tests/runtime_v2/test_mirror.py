import tempfile
import unittest

from app.runtime_v2 import RuntimeMirror


class RuntimeMirrorTests(unittest.TestCase):
    def test_mirrors_legacy_user_and_final_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {"type": "user", "content": "hello"})
            mirror.mirror_ui_event("s1", {"type": "final", "content": "done"})

            snapshot = mirror.snapshots.read("s1")

            self.assertEqual([m["role"] for m in snapshot["messages"]], ["user", "assistant"])
            self.assertEqual(snapshot["last_seq"], 2)

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


if __name__ == "__main__":
    unittest.main()
