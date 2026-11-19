"""Helpers embedded in a separate Langflow Agent before final-result validation.

Only the new flow should use these helpers; the measured baseline stays unchanged.
Call ``_guarded_agent_run(self, _run_once)`` in ``message_response`` without the
provider-remediation wrapper, which could recreate the graph and reset its
model-call limit. The guard must start before
``get_agent_requirements`` / ``run_agent`` binds its token callback.

The flow builder separately sets ``max_iterations=4``. Langflow 1.13.3 maps that
input to ``ModelCallLimitMiddleware(run_limit=4)``. This helper does not change
model selection, provider retries, or the component's model-call budget.

Suppression covers this component's message, token and log events. Private admin
traces/build artifacts may still contain the unvalidated result. A user-facing
endpoint must return only the downstream validator's final ChatOutput.
The deadline uses cooperative asyncio cancellation; it does not promise that a
remote provider or cancellation-resistant dependency stops at the same instant.
"""

import asyncio
import math
from contextlib import contextmanager
from typing import Any, Awaitable, Callable


GUARDED_AGENT_MAX_SECONDS = 90.0
GUARDED_AGENT_MODEL_CALL_LIMIT = 4
_GUARDED_AGENT_UNSET = object()


async def _guarded_keep_message(message: Any, *args: Any, **kwargs: Any) -> Any:
    # process_agent_events expects the callback to return a Message, not None.
    return message


async def _guarded_drop_message_event(*args: Any, **kwargs: Any) -> None:
    return None


@contextmanager
def _guarded_agent_emissions(component: Any):
    """Keep generated messages in memory and restore the component in all exits.

    Do not mutate the shared event manager itself. Clearing the component's
    reference also prevents run_agent from binding manager.on_token and stops
    Component.log from publishing intermediate log events. Tracing remains local
    to the administrator's runtime. Concurrent reuse of one mutable component
    instance fails closed instead of restoring another run's callbacks early.
    """
    if getattr(component, "_guarded_agent_active", False):
        raise ValueError("AGENT_EXECUTION_FAILED")

    fields = ("_event_manager", "send_message", "_send_message_event", "_guarded_agent_active")
    namespace = vars(component)
    previous = {name: namespace.get(name, _GUARDED_AGENT_UNSET) for name in fields}
    try:
        component._guarded_agent_active = True
        component._event_manager = None
        component.send_message = _guarded_keep_message
        component._send_message_event = _guarded_drop_message_event
        yield
    finally:
        for name, value in previous.items():
            if value is _GUARDED_AGENT_UNSET:
                # Restore inherited methods as inherited methods, without leaving
                # a bound-method shadow in the instance's namespace.
                if name in vars(component):
                    delattr(component, name)
            else:
                setattr(component, name, value)


async def _guarded_agent_run(
    component: Any,
    run_once: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: float = GUARDED_AGENT_MAX_SECONDS,
) -> Any:
    """Await one complete Agent operation with a maximum 90-second deadline.

    A smaller timeout is allowed for local tests or a stricter calling policy.
    Provider exceptions and cancellation messages can contain partial model text;
    only fixed error codes leave this boundary. Cancellation keeps its asyncio
    semantics rather than being converted into an ordinary failed response.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= GUARDED_AGENT_MAX_SECONDS
    ):
        raise ValueError("AGENT_EXECUTION_POLICY_INVALID")

    task = asyncio.current_task()
    initial_cancellations = task.cancelling() if task is not None else 0
    try:
        with _guarded_agent_emissions(component):
            async with asyncio.timeout(timeout_seconds) as deadline:
                result = await run_once()
                # A dependency may swallow cancellation and return a partial
                # result. Never publish that late result as a successful run.
                if deadline.expired():
                    raise TimeoutError("AGENT_EXECUTION_TIMEOUT")
                if task is not None and task.cancelling() > initial_cancellations:
                    raise asyncio.CancelledError("AGENT_EXECUTION_CANCELLED")
                return result
    except asyncio.CancelledError:
        raise asyncio.CancelledError("AGENT_EXECUTION_CANCELLED") from None
    except TimeoutError:
        raise TimeoutError("AGENT_EXECUTION_TIMEOUT") from None
    except Exception:
        raise ValueError("AGENT_EXECUTION_FAILED") from None
