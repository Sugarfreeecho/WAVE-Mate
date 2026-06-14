import tempfile
import unittest

from app.runtime_v2 import BlobStore, RuntimeSubagentStore


class RuntimeStorageLayoutTests(unittest.TestCase):
    def test_blob_store_writes_content_addressed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BlobStore(tmp)
            ref = store.put_text("large text")

            self.assertTrue(ref["blob_ref"].startswith("blobs/"))
            self.assertEqual(store.read_text(ref["blob_ref"]), "large text")

    def test_subagent_store_writes_under_parent_subagents_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeSubagentStore(tmp)
            event = store.append_event("parent", "agent1", "message_user", {"content": "sub"})
            store.write_metadata("parent", "agent1", {"name": "subagent"})
            snapshot = store.read_snapshot("parent", "agent1")

            self.assertEqual(event.seq, 1)
            self.assertEqual(snapshot["messages"][0]["payload"]["content"], "sub")
            self.assertTrue((store.agent_dir("parent", "agent1") / "events.jsonl").exists())
            self.assertTrue((store.agent_dir("parent", "agent1") / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
