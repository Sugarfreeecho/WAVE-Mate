import tempfile
import unittest

from app.runtime_v2 import SessionEventLog


class SessionEventLogTests(unittest.TestCase):
    def test_append_and_read_after_seq(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionEventLog(tmp)
            first = log.append("s1", "message_user", {"content": "hello"})
            second = log.append("s1", "run_started", {"run": "r1"}, run_id="r1")

            self.assertEqual(first.seq, 1)
            self.assertEqual(second.seq, 2)
            self.assertEqual([ev.seq for ev in log.read_after_seq("s1", 1)], [2])

    def test_repair_drops_bad_lines_and_renumbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = SessionEventLog(tmp)
            log.append("s1", "message_user", {})
            path = log.event_path("s1")
            with path.open("a", encoding="utf-8") as fh:
                fh.write("{bad json}\n")
            log.append("s1", "run_finished", {})

            result = log.repair("s1")
            events = log.read_all("s1")

            self.assertEqual(result["dropped"], 1)
            self.assertEqual([ev.seq for ev in events], [1, 2])


if __name__ == "__main__":
    unittest.main()
