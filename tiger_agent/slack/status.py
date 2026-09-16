"""Keep the Slack assistant status alive and descriptive while a response is in flight.

Slack removes an ``assistant.threads.setStatus`` status two minutes after the
last time it was sent (or as soon as the app posts a message). The coordinator
agent used to keep it alive by accident: it called a tool every few seconds and
each tool-call part re-sent the status. With work delegated to sub-agents the
coordinator can sit silent for many minutes, so the status has to be driven by
every event in the agent tree, not just the coordinator's own tool calls.

``ResponseStatus`` is fed each stream event, from the coordinator's
``run_stream_events`` loop and from sub-agent runs via
``make_subagent_event_handler``. It re-sends the status whenever the text
changes and, as a keep-alive, whenever more than ``STATUS_REFRESH_SECONDS``
have passed since the last send. There is no background timer: a single tool
call that emits nothing for more than two minutes will let the status lapse
until the next event (see ``docs/plans/status-refresh-background-task.md``).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterable, Callable
from typing import Any, Protocol

import logfire
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    BaseToolCallPart,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    PartStartEvent,
)
from pydantic_ai.tools import RunContext
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.slack.utils import set_status

# Slack drops the status after 120 s of silence; refresh comfortably inside that.
STATUS_REFRESH_SECONDS: float = 60.0

# The tool the SubAgents capability registers for delegation.
DELEGATE_TOOL_NAME = "delegate_task"

# pydantic-ai delivers structured output through a synthetic tool call named
# after its DEFAULT_OUTPUT_TOOL_NAME. It is the run finishing, not a step the
# user should see as "calling a tool".
OUTPUT_TOOL_NAMES = frozenset({"final_result"})

_UNNAMED_SUBTASK = "sub-task"


class ResponseStatus:
    """Tracks what the agent tree is doing and mirrors it into the thread status.

    Feed every coordinator event to :meth:`on_event`; feed sub-agent events via
    :func:`make_subagent_event_handler`. Sub-task lifetimes are derived from the
    coordinator's own ``delegate_task`` call/result events, so the count of
    running sub-tasks is exact regardless of how the sub-agent's event handler
    is chunked.
    """

    def __init__(
        self,
        client: AsyncWebClient,
        channel_id: str,
        thread_ts: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._clock = clock
        self._message: str | None = None
        self._last_sent: float | None = None
        # delegate tool_call_id -> sub-agent name, for every delegation in flight
        self._subtasks: dict[str, str] = {}

    @property
    def message(self) -> str | None:
        """The status text currently shown, or None for the generic loading messages."""
        return self._message

    @property
    def running_subtasks(self) -> int:
        return len(self._subtasks)

    async def on_event(
        self,
        event: AgentStreamEvent,
        *,
        subtask: str | None = None,
        prompt: str | None = None,  # noqa: ARG002 - shared sink signature
    ) -> None:
        """Update the status for one stream event.

        Args:
            event: An event from the coordinator (``subtask=None``) or from a
                sub-agent run (``subtask`` is that agent's name).
            prompt: The sub-agent run's prompt; unused here, accepted so every
                sink shares one signature.
        """
        if isinstance(event, FunctionToolCallEvent) and subtask is None:
            if event.part.tool_name == DELEGATE_TOOL_NAME:
                self._subtasks[event.tool_call_id] = self._delegate_name(event)
            return

        if isinstance(event, FunctionToolResultEvent) and subtask is None:
            finished = self._subtasks.pop(event.tool_call_id, None) is not None
            if finished and not self._subtasks:
                # the last delegation came back; the coordinator is thinking again
                await self.set(None)
            return

        if isinstance(event, PartStartEvent) and isinstance(
            event.part, BaseToolCallPart
        ):
            if event.part.tool_name not in OUTPUT_TOOL_NAMES:
                await self.set(self._tool_message(event.part.tool_name, subtask))
            return

        if isinstance(event, PartEndEvent) and isinstance(event.part, BaseToolCallPart):
            if event.part.tool_name not in OUTPUT_TOOL_NAMES:
                await self.set(self._idle_message(subtask))
            return

        await self.refresh()

    async def set(self, message: str | None) -> None:
        """Change the status text and send it right away."""
        self._message = message
        await self._send()

    async def refresh(self) -> None:
        """Re-send the current status if it is about to expire."""
        if (
            self._last_sent is None
            or self._clock() - self._last_sent >= STATUS_REFRESH_SECONDS
        ):
            await self._send()

    async def clear(self) -> None:
        """Remove the status once the response has been posted."""
        self._message = None
        await set_status(
            client=self._client,
            channel_id=self._channel_id,
            thread_ts=self._thread_ts,
            is_busy=False,
        )

    async def _send(self) -> None:
        self._last_sent = self._clock()
        await set_status(
            client=self._client,
            channel_id=self._channel_id,
            thread_ts=self._thread_ts,
            is_busy=True,
            message=self._message,
        )

    def _tool_message(self, tool_name: str, subtask: str | None) -> str:
        if subtask is None:
            return f"Calling Tool: {tool_name}"
        return f"{self._subtask_prefix(subtask)}: {tool_name}"

    def _idle_message(self, subtask: str | None) -> str | None:
        if subtask is None:
            return None
        return f"{self._subtask_prefix(subtask)}: thinking..."

    def _subtask_prefix(self, subtask: str) -> str:
        running = len(self._subtasks)
        if running > 1:
            return f"Sub-tasks ({running} running)"
        return f"Sub-task ({subtask})"

    @staticmethod
    def _delegate_name(event: FunctionToolCallEvent) -> str:
        try:
            name = event.part.args_as_dict().get("agent_name")
        except Exception:
            logfire.warn(
                "Could not read the delegated agent name from the tool call",
                tool_call_id=event.tool_call_id,
            )
            return _UNNAMED_SUBTASK
        return str(name) if name else _UNNAMED_SUBTASK


class StreamEventSink(Protocol):
    async def on_event(
        self,
        event: AgentStreamEvent,
        *,
        subtask: str | None = None,
        prompt: str | None = None,
    ) -> None: ...


def make_subagent_event_handler(*sinks: StreamEventSink) -> EventStreamHandler[Any]:
    """Build the ``event_stream_handler`` to hand to ``SubAgents``.

    pydantic-ai invokes the handler with the sub-agent's run context and the
    events of one model request or tool-execution step. Every event is
    forwarded to each sink tagged with the sub-agent's name (from
    ``ctx.agent``) and the run's prompt, which is the ``task`` the coordinator
    delegated. ``ResponseStatus`` uses the name to show
    ``Sub-task (<name>): <tool>``; ``TaskCards`` uses the prompt to find the
    delegation's card.
    """

    async def handler(
        ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]
    ) -> None:
        agent = getattr(ctx, "agent", None)
        name = getattr(agent, "name", None) or _UNNAMED_SUBTASK
        prompt = getattr(ctx, "prompt", None)
        prompt = prompt if isinstance(prompt, str) else None
        async for event in events:
            for sink in sinks:
                await sink.on_event(event, subtask=name, prompt=prompt)

    return handler
