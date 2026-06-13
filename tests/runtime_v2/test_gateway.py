import asyncio
import tempfile
import unittest

from app.runtime_v2 import RuntimeGateway


class RuntimeGatewayTests(unittest.TestCase):
    def test_run_lifecycle_emits_terminal_event(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                gateway = RuntimeGateway(tmp)
                await gateway.start_run("s1", run_id="r1")
                await gateway.finish_run("s1", "r1")

                events = gateway.read_after_seq("s1", 0)
                self.assertEqual([ev.type for ev in events], ["run_started", "run_finished"])
                self.assertEqual(gateway.state()["active_runs"], [])

        asyncio.run(scenario())

    def test_publisher_receives_event(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                gateway = RuntimeGateway(tmp)
                queue = await gateway.publisher.subscribe("s1")
                await gateway.append_event("s1", "message_user", {"content": "hello"})
                event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(event.type, "message_user")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
