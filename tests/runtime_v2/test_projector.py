import asyncio
import tempfile
import unittest

from app.runtime_v2 import RuntimeGateway, RuntimeProjector
from app.runtime_v2.event_schema import RuntimeEvent


class RuntimeProjectorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
