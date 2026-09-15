"""Show delegated sub-agent work as task cards inside the streaming reply.

Slack's streaming API accepts ``task_update`` chunks alongside markdown text.
They render as a timeline of cards in the message, each with a title, a status
(pending / in_progress / complete / error) and a details area. Re-sending a
chunk with the same ``id`` updates the card's status and *appends* the new
``details`` text to what is already shown (details stream like message text),
so each update carries only the next line of an append-only log.

``TaskCards`` maps the coordinator's ``delegate_task`` calls onto those cards:
one card per delegation, created when the call starts, updated with the tool
the sub-agent is currently using, and marked complete or error when the result
comes back. Every update is a ``chat.appendStream`` call, so the stream that
holds the intro text stays alive through a long delegation and the final
answer lands in the same message instead of a second one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import logfire
from pydantic_ai.messages import (
    AgentStreamEvent,
    BaseToolCallPart,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartStartEvent,
    RetryPromptPart,
)
from slack_sdk.models.messages.chunk import TaskUpdateChunk

from tiger_agent.slack.status import DELEGATE_TOOL_NAME
from tiger_agent.slack.stream import ResponseStream

# Longest title Slack renders comfortably on one line in the card timeline.
TITLE_MAX_CHARS = 80
# Sub-agents call tools every few seconds; one card refresh per this interval
# is plenty for the user and keeps chat.appendStream volume down.
DETAILS_MIN_INTERVAL_SECONDS: float = 3.0
# Tool lines logged per card before the log is elided; the final status line
# is always sent. Slack caps each task_update chunk at 256 characters, so the
# log is built from short lines rather than a growing paragraph.
MAX_TOOL_LINES = 12
DETAILS_CHUNK_MAX_CHARS = 200

_UNNAMED_SUBTASK = "sub-task"


@dataclass
class _Card:
    id: str
    title: str
    agent: str
    task: str
    started: float
    tool_lines: int = 0
    last_tool: str | None = None
    details_sent_at: float | None = None
    done: bool = False


class TaskCards:
    def __init__(
        self,
        stream: ResponseStream,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._clock = clock
        self._cards: dict[str, _Card] = {}

    @property
    def open_cards(self) -> int:
        return sum(1 for card in self._cards.values() if not card.done)

    async def on_event(
        self,
        event: AgentStreamEvent,
        *,
        subtask: str | None = None,
        prompt: str | None = None,
    ) -> None:
        """Update the cards for one stream event.

        Args:
            event: An event from the coordinator (``subtask=None``) or from a
                sub-agent run.
            subtask: The sub-agent's name when the event comes from a child run.
            prompt: The child run's user prompt, i.e. the ``task`` the
                coordinator delegated; used to find that delegation's card.
        """
        if subtask is None:
            if isinstance(event, FunctionToolCallEvent):
                if event.part.tool_name == DELEGATE_TOOL_NAME:
                    await self._start(event)
            elif isinstance(event, FunctionToolResultEvent):
                await self._finish(event)
            return

        if isinstance(event, PartStartEvent) and isinstance(
            event.part, BaseToolCallPart
        ):
            card = self._card_for(prompt)
            if card is not None:
                await self._log_tool(card, event.part.tool_name)

    async def _start(self, event: FunctionToolCallEvent) -> None:
        try:
            args = event.part.args_as_dict()
        except Exception:
            logfire.warn(
                "Could not read delegate_task arguments for a task card",
                tool_call_id=event.tool_call_id,
            )
            args = {}
        agent = str(args.get("agent_name") or _UNNAMED_SUBTASK)
        task = str(args.get("task") or "")
        card = _Card(
            id=event.tool_call_id,
            title=summarize_task(task),
            agent=agent,
            task=task,
            started=self._clock(),
        )
        self._cards[card.id] = card
        await self._send(card, status="in_progress", details=f"{agent}: started")

    async def _finish(self, event: FunctionToolResultEvent) -> None:
        card = self._cards.get(event.tool_call_id)
        if card is None or card.done:
            return
        card.done = True
        elapsed = round(self._clock() - card.started)
        if isinstance(event.part, RetryPromptPart):
            reason = event.part.model_response()
            await self._send(
                card,
                status="error",
                details=f"\n✗ failed after {elapsed}s",
                output=_clip(reason, DETAILS_CHUNK_MAX_CHARS),
            )
        else:
            await self._send(card, status="complete", details=f"\n✓ done in {elapsed}s")

    async def _log_tool(self, card: _Card, tool_name: str) -> None:
        """Append one "• <tool>" line to the card, throttled and deduplicated."""
        if card.tool_lines > MAX_TOOL_LINES:
            return  # log already elided
        if tool_name == card.last_tool:
            return
        now = self._clock()
        if (
            card.details_sent_at is not None
            and now - card.details_sent_at < DETAILS_MIN_INTERVAL_SECONDS
        ):
            return
        card.last_tool = tool_name
        card.details_sent_at = now
        card.tool_lines += 1
        elided = card.tool_lines > MAX_TOOL_LINES
        line = "\n• …" if elided else f"\n• {tool_name}"
        await self._send(card, status="in_progress", details=line)

    def _card_for(self, prompt: str | None) -> _Card | None:
        open_cards = [card for card in self._cards.values() if not card.done]
        if not open_cards:
            return None
        if prompt is not None:
            for card in open_cards:
                if card.task == prompt:
                    return card
        if len(open_cards) == 1:
            return open_cards[0]
        # several delegations in flight and none matches this run's prompt:
        # better to leave the cards alone than to attribute the tool call to
        # the wrong one.
        return None

    async def _send(
        self,
        card: _Card,
        *,
        status: str,
        details: str,
        output: str | None = None,
    ) -> None:
        # `details` is appended to the card by Slack, so send only the new line
        chunk = TaskUpdateChunk(
            id=card.id,
            title=card.title,
            status=status,
            details=_clip(details, DETAILS_CHUNK_MAX_CHARS),
            output=output,
        )
        try:
            await self._stream.append(chunks=[chunk])
        except Exception:
            # a card is decoration; never let it take the response down
            logfire.exception("Failed to append a task card", card_id=card.id)


_WHITESPACE = re.compile(r"\s+")
_LEADING_MARKUP = re.compile(r"^[\s#>*\-•\d.)]+")
_INLINE_MARKUP = re.compile(r"\*+|`+|(?<!\w)_|_(?!\w)")


def summarize_task(task: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """First line of the delegated task, cleaned up to fit a card title."""
    first_line = next((line for line in task.splitlines() if line.strip()), "")
    text = _INLINE_MARKUP.sub("", _LEADING_MARKUP.sub("", first_line))
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return "Delegated task"
    return _clip(text, max_chars)


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
