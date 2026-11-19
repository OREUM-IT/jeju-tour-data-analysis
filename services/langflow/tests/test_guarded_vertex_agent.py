"""Offline behavior checks; no Langflow server, provider, or model is contacted."""

import asyncio
import importlib.util
import pathlib
import traceback
import unittest


_HELPER = pathlib.Path(__file__).resolve().parents[1] / "guarded_vertex_agent.py"
_SPEC = importlib.util.spec_from_file_location("guarded_vertex_agent", _HELPER)
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)


class FakeEventManager:
    def __init__(self):
        self.events = []

    def on_token(self, **kwargs):
        self.events.append(("token", kwargs))

    def on_message(self, **kwargs):
        self.events.append(("message", kwargs))

    def on_log(self, **kwargs):
        self.events.append(("log", kwargs))


class FakeAgent:
    def __init__(self):
        self._event_manager = FakeEventManager()
        self.private_logs = []
        self.persisted_messages = []

    async def send_message(self, message, **kwargs):
        self.persisted_messages.append(message)
        await self._send_message_event(message)
        return message

    async def _send_message_event(self, message, **kwargs):
        if self._event_manager is not None:
            self._event_manager.on_message(data=message)

    def log(self, message):
        self.private_logs.append(message)
        if self._event_manager is not None:
            self._event_manager.on_log(data=message)


class GuardedAgentTests(unittest.IsolatedAsyncioTestCase):
    def assert_restored(self, agent, manager):
        self.assertIs(agent._event_manager, manager)
        self.assertNotIn("send_message", vars(agent))
        self.assertNotIn("_send_message_event", vars(agent))
        self.assertNotIn("_guarded_agent_active", vars(agent))

    def assert_sanitized(self, error, expected, sensitive):
        self.assertEqual(str(error), expected)
        rendered = "".join(traceback.format_exception(error))
        self.assertNotIn(sensitive, rendered)
        self.assertIsNone(error.__cause__)

    async def test_success_keeps_message_object_without_publishing_or_storing(self):
        agent = FakeAgent()
        manager = agent._event_manager
        message = object()

        async def run():
            # This matches Langflow's callback-binding order in run_agent.
            token_callback = agent._event_manager.on_token if agent._event_manager else None
            if token_callback:
                token_callback(data="unvalidated token")
            self.assertIs(await agent.send_message(message=message), message)
            await agent._send_message_event(message)
            agent.log("private trace")
            return message

        self.assertIs(await guard._guarded_agent_run(agent, run), message)
        self.assertEqual(manager.events, [])
        self.assertEqual(agent.persisted_messages, [])
        self.assertEqual(agent.private_logs, ["private trace"])
        self.assert_restored(agent, manager)
        # Normal behavior resumes after the guarded operation.
        await agent.send_message("validated downstream output")
        self.assertEqual(len(manager.events), 1)

    async def test_provider_exception_is_sanitized_and_callbacks_restored(self):
        agent = FakeAgent()
        manager = agent._event_manager
        sensitive = "PRIVATE_MODEL_OUTPUT_CANARY"

        async def run():
            raise RuntimeError(sensitive)

        with self.assertRaises(ValueError) as caught:
            await guard._guarded_agent_run(agent, run)
        self.assert_sanitized(caught.exception, "AGENT_EXECUTION_FAILED", sensitive)
        self.assert_restored(agent, manager)

    async def test_inner_timeout_is_sanitized_without_echoing_transport_data(self):
        agent = FakeAgent()
        manager = agent._event_manager
        sensitive = "PRIVATE_TRANSPORT_CANARY"

        async def run():
            raise TimeoutError(sensitive)

        with self.assertRaises(TimeoutError) as caught:
            await guard._guarded_agent_run(agent, run)
        self.assert_sanitized(caught.exception, "AGENT_EXECUTION_TIMEOUT", sensitive)
        self.assert_restored(agent, manager)

    async def test_deadline_cancels_awaited_work_and_restores_after_cleanup(self):
        agent = FakeAgent()
        manager = agent._event_manager
        cleaned_up = asyncio.Event()

        async def run():
            try:
                await asyncio.Event().wait()
            finally:
                # Cleanup must still run while publication is suppressed.
                self.assertIsNone(agent._event_manager)
                await agent._send_message_event("cleanup canary")
                cleaned_up.set()

        with self.assertRaisesRegex(TimeoutError, "^AGENT_EXECUTION_TIMEOUT$"):
            await guard._guarded_agent_run(agent, run, timeout_seconds=0.01)
        self.assertTrue(cleaned_up.is_set())
        self.assertEqual(manager.events, [])
        self.assert_restored(agent, manager)

    async def test_external_cancellation_stays_cancelled_and_drops_raw_reason(self):
        agent = FakeAgent()
        manager = agent._event_manager
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def run():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

        task = asyncio.create_task(guard._guarded_agent_run(agent, run))
        await started.wait()
        sensitive = "PRIVATE_CANCELLATION_CANARY"
        task.cancel(sensitive)
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        self.assertTrue(task.cancelled())
        self.assertTrue(cleaned_up.is_set())
        self.assert_sanitized(caught.exception, "AGENT_EXECUTION_CANCELLED", sensitive)
        self.assert_restored(agent, manager)

    async def test_swallowed_deadline_cancellation_cannot_turn_into_success(self):
        agent = FakeAgent()
        manager = agent._event_manager

        async def run():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "unvalidated late partial result"

        with self.assertRaisesRegex(TimeoutError, "^AGENT_EXECUTION_TIMEOUT$"):
            await guard._guarded_agent_run(agent, run, timeout_seconds=0.01)
        self.assert_restored(agent, manager)

    async def test_swallowed_external_cancellation_cannot_turn_into_success(self):
        agent = FakeAgent()
        manager = agent._event_manager
        started = asyncio.Event()

        async def run():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "unvalidated late partial result"

        task = asyncio.create_task(guard._guarded_agent_run(agent, run))
        await started.wait()
        task.cancel("PRIVATE_CANCELLATION_CANARY")
        with self.assertRaisesRegex(asyncio.CancelledError, "^AGENT_EXECUTION_CANCELLED$"):
            await task
        self.assertTrue(task.cancelled())
        self.assert_restored(agent, manager)

    async def test_instance_callbacks_and_absent_manager_are_restored_exactly(self):
        agent = FakeAgent()
        del agent._event_manager

        async def original_send(message, **kwargs):
            return message

        agent.send_message = original_send
        agent._guarded_agent_active = False

        async def run():
            self.assertIsNone(agent._event_manager)
            return "result"

        self.assertEqual(await guard._guarded_agent_run(agent, run), "result")
        self.assertNotIn("_event_manager", vars(agent))
        self.assertIs(agent.send_message, original_send)
        self.assertIs(agent._guarded_agent_active, False)

    async def test_concurrent_reuse_fails_closed_without_unmuting_first_operation(self):
        agent = FakeAgent()
        manager = agent._event_manager
        started, release = asyncio.Event(), asyncio.Event()
        second_called = False

        async def first():
            started.set()
            await release.wait()
            self.assertIsNone(agent._event_manager)
            return "first result"

        async def second():
            nonlocal second_called
            second_called = True

        task = asyncio.create_task(guard._guarded_agent_run(agent, first))
        try:
            await started.wait()
            with self.assertRaisesRegex(ValueError, "^AGENT_EXECUTION_FAILED$"):
                await guard._guarded_agent_run(agent, second)
            self.assertFalse(second_called)
            self.assertIsNone(agent._event_manager)
        finally:
            release.set()
            self.assertEqual(await task, "first result")
        self.assert_restored(agent, manager)

    async def test_policy_rejects_invalid_or_longer_deadlines_before_starting_work(self):
        agent = FakeAgent()
        manager = agent._event_manager
        called = False

        async def run():
            nonlocal called
            called = True

        for timeout in (0, -1, 90.01, True, float("nan"), float("inf"), "60", None):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "^AGENT_EXECUTION_POLICY_INVALID$"):
                    await guard._guarded_agent_run(agent, run, timeout_seconds=timeout)
                self.assertFalse(called)
                self.assert_restored(agent, manager)


if __name__ == "__main__":
    unittest.main()
