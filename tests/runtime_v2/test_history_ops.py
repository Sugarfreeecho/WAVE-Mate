import tempfile
import unittest

from app.runtime_v2 import RuntimeHistoryOps, RuntimeMirror


class RuntimeHistoryOpsTests(unittest.TestCase):
    def test_rewrite_and_delete_project_visible_messages_without_rewriting_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {"type": "user", "content": "old"})
            mirror.mirror_ui_event("s1", {"type": "final", "content": "answer"})

            ops = RuntimeHistoryOps(tmp)
            ops.rewrite_message("s1", 1, "new")
            ops.delete_message("s1", 2)

            snapshot = ops.snapshots.read("s1")
            events = ops.event_log.read_all("s1")

            self.assertEqual([ev.type for ev in events], [
                "message_user",
                "message_assistant_final",
                "message_rewritten",
                "message_deleted",
            ])
            self.assertEqual(len(snapshot["messages"]), 2)
            self.assertEqual(len(snapshot["visible_messages"]), 1)
            self.assertEqual(snapshot["visible_messages"][0]["payload"]["content"], "new")
            self.assertTrue(snapshot["visible_messages"][0]["rewritten"])

    def test_compaction_changes_model_messages_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {"type": "user", "content": "u1"})
            mirror.mirror_ui_event("s1", {"type": "final", "content": "a1"})
            mirror.mirror_ui_event("s1", {"type": "user", "content": "u2"})

            ops = RuntimeHistoryOps(tmp)
            ops.compact_history("s1", summary="summary", compacted_before_seq=3)

            snapshot = ops.snapshots.read("s1")

            self.assertEqual(len(snapshot["visible_messages"]), 3)
            self.assertEqual([m["role"] for m in snapshot["model_messages"]], ["system", "user"])
            self.assertEqual(snapshot["model_messages"][0]["payload"]["kind"], "history_compaction")

    def test_visible_range_hides_without_deleting_source_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            mirror = RuntimeMirror(tmp)
            mirror.mirror_ui_event("s1", {"type": "user", "content": "u1"})
            mirror.mirror_ui_event("s1", {"type": "final", "content": "a1"})
            mirror.mirror_ui_event("s1", {"type": "user", "content": "u2"})

            ops = RuntimeHistoryOps(tmp)
            ops.change_visible_range("s1", from_seq=3)

            snapshot = ops.snapshots.read("s1")

            self.assertEqual(len(snapshot["messages"]), 3)
            self.assertEqual(len(snapshot["visible_messages"]), 1)
            self.assertEqual(snapshot["visible_messages"][0]["payload"]["content"], "u2")


if __name__ == "__main__":
    unittest.main()
