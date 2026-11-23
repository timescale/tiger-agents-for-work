"""Shared vocabulary for the sinks that watch an agent run's stream events.

The coordinator's ``run_stream_events`` loop and every sub-agent run feed the
same :class:`StreamEventSink` objects, so the names a sink needs to recognise
(the delegation tool, pydantic-ai's synthetic output tool) and the handler that
tags sub-agent events with their agent live here rather than in any one sink.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any, Protocol

from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import AgentStreamEvent
from pydantic_ai.tools import RunContext

# The tool the SubAgents capability registers for delegation.
DELEGATE_TOOL_NAME = "delegate_task"

# pydantic-ai delivers structured output through a synthetic tool call named
# after its DEFAULT_OUTPUT_TOOL_NAME. It is the run finishing, not a step the
# user should see as "calling a tool".
OUTPUT_TOOL_NAMES = frozenset({"final_result"})

UNNAMED_SUBTASK = "sub-task"


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
    forwarded to each sink tagged with the sub-agent's name (from ``ctx.agent``)
    and the run's prompt, which is the ``task`` the coordinator delegated.
    ``TaskCards`` uses the prompt to find the delegation's card.
    """

    async def handler(
        ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]
    ) -> None:
        agent = getattr(ctx, "agent", None)
        name = getattr(agent, "name", None) or UNNAMED_SUBTASK
        prompt = getattr(ctx, "prompt", None)
        prompt = prompt if isinstance(prompt, str) else None
        async for event in events:
            for sink in sinks:
                await sink.on_event(event, subtask=name, prompt=prompt)

    return handler
