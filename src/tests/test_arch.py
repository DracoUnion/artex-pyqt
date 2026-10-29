"""Unit tests for the zero-Qt architecture layers (common / tasks / bootstrap).

These never import PySide6, so they run headless.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.events import EventHub  # noqa: E402
from tasks import TaskManager, TaskState  # noqa: E402


class TestEventHub(unittest.TestCase):

    def test_sub_and_publish(self):
        hub = EventHub()
        seen = []
        unsub = hub.subscribe("demo", lambda ev: seen.append(ev))
        hub.publish("demo", x=1)
        unsub()
        hub.publish("demo", x=2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["type"], "demo")
        self.assertEqual(seen[0]["x"], 1)

    def test_wildcard_subscriber(self):
        hub = EventHub()
        seen = []
        hub.subscribe("*", lambda ev: seen.append(ev["type"]))
        hub.publish("a", v=1)
        hub.publish("b", v=2)
        self.assertEqual(seen, ["a", "b"])


class TestTaskManager(unittest.TestCase):

    def test_submit_runs_fn_and_emits_done(self):
        tasks = TaskManager(max_workers=2)
        events = []
        tasks.subscribe(lambda ev: events.append(ev["type"]))

        def work(*, cancel_event, **kwargs):
            time.sleep(0.02)
            return "ok"

        task = tasks.submit(work, label="call")
        deadline = time.time() + 3
        while task.state not in (TaskState.DONE, TaskState.FAILED) and time.time() < deadline:
            time.sleep(0.005)
        tasks.shutdown()
        self.assertEqual(task.result, "ok")
        self.assertIn("started", events)
        self.assertIn("done", events)

    def test_failed_task_sets_error(self):
        tasks = TaskManager(max_workers=1)

        def boom(*, cancel_event, **kwargs):
            raise ValueError("boom")

        task = tasks.submit(boom, label="fail")
        deadline = time.time() + 2
        while task.state not in (TaskState.DONE, TaskState.FAILED) and time.time() < deadline:
            time.sleep(0.005)
        tasks.shutdown()
        self.assertEqual(task.state, TaskState.FAILED)
        self.assertEqual(task.error, "boom")


class TestBootstrap(unittest.TestCase):

    def test_build_context_assembles_gateway(self):
        """Gateway boots on temp dirs; health + agents dispatch work."""
        from bootstrap import build_context

        with tempfile.TemporaryDirectory() as tmp:
            ctx = build_context(
                data_dir=Path(tmp) / "data",
                skills_dir=Path(tmp) / "skills",
            )
            self.assertIsNotNone(ctx.gateway)
            self.assertIsNotNone(ctx.events)
            health = ctx.gateway.dispatch("GET", "/api/health")
            self.assertEqual(health["status"], "ok")
            agents = ctx.gateway.dispatch("GET", "/api/agents")
            self.assertIsInstance(agents, list)
            # Close the SQLite connection so TemporaryDirectory cleanup works.
            ctx.gateway.db.close()
            ctx.gateway.close()
            ctx.tasks.shutdown()


if __name__ == "__main__":
    unittest.main()