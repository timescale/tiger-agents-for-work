"""Show the agent tree's tool calls as task cards inside the streaming reply.

Slack's streaming API accepts ``task_update`` chunks alongside markdown text.
They render as a timeline of cards in the message, each with a title, a status
(pending / in_progress / complete / error) and a details area. Re-sending a
chunk with the same ``id`` updates the card's status and *appends* the new
``details`` text to what is already shown (details stream like message text),
so each update carries only the next lines of an append-only log.

``TaskCards`` shows one card per agent: the coordinator gets a card as soon as
it calls its first tool, and each ``delegate_task`` call opens another. Every
tool call the agent makes is logged to its card as a ``• <tool>`` line. Calls
that happen inside the same throttle window are batched into one chunk rather
than dropped, so the log stays complete without one ``chat.appendStream`` call
per tool. Every update keeps the stream that holds the intro text alive, so the
final answer lands in the same message instead of a second one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

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

from tiger_agent.slack.events import (
    DELEGATE_TOOL_NAME,
    OUTPUT_TOOL_NAMES,
    UNNAMED_SUBTASK,
)
from tiger_agent.slack.stream import ResponseStream

# Longest title Slack renders comfortably on one line in the card timeline.
TITLE_MAX_CHARS = 80
# Agents call tools every few seconds; one card refresh per this interval is
# plenty for the user and keeps chat.appendStream volume down. Calls made
# inside the window are not dropped, they ride along on the next flush.
DETAILS_MIN_INTERVAL_SECONDS: float = 3.0
# Slack caps each task_update chunk at 256 characters. A flush that would
# exceed this is split across consecutive chunks so no tool line is lost.
DETAILS_CHUNK_MAX_CHARS = 200

# The card for the agent's own tool calls. It has no delegate_task call to
# take an id from, so it uses a fixed one; no tool_call_id can collide with it.
# "Coordinator" is how the agent tree describes itself internally and means
# nothing to the people reading the thread, so the card is titled for what it
# shows instead.
COORDINATOR_CARD_ID = "__coordinator__"
COORDINATOR_TITLE = "Tool calls"
COORDINATOR_AGENT = "coordinator"

# How a run ended, for the closing line on any card still open.
type CardOutcome = Literal["completed", "cancelled", "target_gone"]

_OUTCOME_LINE: dict[str, str] = {
    "cancelled": "✗ cancelled",
    "target_gone": "✗ stopped",
}


@dataclass
class _Card:
    id: str
    title: str
    agent: str
    task: str
    started: float
    pending: list[str] = field(default_factory=list)
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
            elif (
                isinstance(event, PartStartEvent)
                and isinstance(event.part, BaseToolCallPart)
                # the coordinator's own tool calls; delegate_task is logged to
                # the coordinator's card too, the sub-agent's work goes on the
                # card that FunctionToolCallEvent opens for it
                and event.part.tool_name not in OUTPUT_TOOL_NAMES
            ):
                await self._log_tool(self._coordinator_card(), event.part.tool_name)
            return

        if isinstance(event, PartStartEvent) and isinstance(
            event.part, BaseToolCallPart
        ):
            if event.part.tool_name in OUTPUT_TOOL_NAMES:
                return  # the sub-agent handing back its report, not a step
            card = self._card_for(prompt)
            if card is not None:
                await self._log_tool(card, event.part.tool_name)

    async def close(self, outcome: CardOutcome = "completed") -> None:
        """Close every card still open, now the run is over.

        The agent's own card has no ``delegate_task`` result to close it the
        way a delegated card is closed by :meth:`_finish`, so without this it
        is left at ``in_progress`` and Slack renders it as a task that started
        and never reported finishing (a warning glyph on the card).

        Safe to call more than once, and on a run that opened no cards.
        """
        for card in list(self._cards.values()):
            if card.done:
                continue
            card.done = True
            elapsed = round(self._clock() - card.started)
            pending = self._drain(card)
            if outcome == "completed":
                await self._send(
                    card,
                    status="complete",
                    details=f"{pending}\n\n✓ done in {elapsed}s",
                )
            else:
                await self._send(
                    card,
                    status="error",
                    details=f"{pending}\n\n{_OUTCOME_LINE[outcome]} after {elapsed}s",
                )

    def _coordinator_card(self) -> _Card:
        """The coordinator's card, created on its first tool call."""
        card = self._cards.get(COORDINATOR_CARD_ID)
        if card is None:
            card = _Card(
                id=COORDINATOR_CARD_ID,
                title=COORDINATOR_TITLE,
                agent=COORDINATOR_AGENT,
                task="",
                started=self._clock(),
            )
            self._cards[card.id] = card
        return card

    async def _start(self, event: FunctionToolCallEvent) -> None:
        try:
            args = event.part.args_as_dict()
        except Exception:
            logfire.warn(
                "Could not read delegate_task arguments for a task card",
                tool_call_id=event.tool_call_id,
            )
            args = {}
        agent = str(args.get("agent_name") or UNNAMED_SUBTASK)
        task = str(args.get("task") or "")
        card = _Card(
            id=event.tool_call_id,
            title=summarize_task(task),
            agent=agent,
            task=task,
            started=self._clock(),
        )
        self._cards[card.id] = card
        await self._send(card, status="in_progress", details=f"started ({agent})")

    async def _finish(self, event: FunctionToolResultEvent) -> None:
        card = self._cards.get(event.tool_call_id)
        if card is None or card.done:
            return
        card.done = True
        elapsed = round(self._clock() - card.started)
        # anything still queued belongs on the card before its closing line
        pending = self._drain(card)
        # the blank line closes the bullet list so the final status line
        # renders flush left instead of indented under the last tool
        if isinstance(event.part, RetryPromptPart):
            reason = event.part.model_response()
            await self._send(
                card,
                status="error",
                details=f"{pending}\n\n✗ failed after {elapsed}s",
                output=_clip(reason, DETAILS_CHUNK_MAX_CHARS),
            )
        else:
            await self._send(
                card, status="complete", details=f"{pending}\n\n✓ done in {elapsed}s"
            )

    async def _log_tool(self, card: _Card, tool_name: str) -> None:
        """Queue one "• <tool>" line for the card, flushing on the throttle.

        A delegated card's lines carry the sub-agent's name, so a card holding
        several delegations' work still reads unambiguously. The agent's own
        card needs no tag: every line on it is its own call.

        Every call is recorded. Calls that arrive inside
        ``DETAILS_MIN_INTERVAL_SECONDS`` of the last flush wait in
        ``card.pending`` and go out together on the next one.
        """
        if card.done:
            return
        card.pending.append(f"\n• {tool_name}{self._agent_tag(card)}")
        now = self._clock()
        if (
            card.details_sent_at is not None
            and now - card.details_sent_at < DETAILS_MIN_INTERVAL_SECONDS
        ):
            return
        card.details_sent_at = now
        await self._send(card, status="in_progress", details=self._drain(card))

    @staticmethod
    def _agent_tag(card: _Card) -> str:
        """ " (investigator)" for a delegated card, nothing for the agent's own."""
        if card.id == COORDINATOR_CARD_ID or not card.agent:
            return ""
        return f" ({card.agent})"

    def _drain(self, card: _Card) -> str:
        """Take everything queued for the card as one details string."""
        lines = "".join(card.pending)
        card.pending.clear()
        return lines

    def _card_for(self, prompt: str | None) -> _Card | None:
        open_cards = [
            card
            for card in self._cards.values()
            if not card.done and card.id != COORDINATOR_CARD_ID
        ]
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
        # `details` is appended to the card by Slack, so send only the new
        # lines. A batched flush can outgrow one chunk, so split it and send
        # the pieces in order rather than clipping tool lines away.
        # Only the final piece carries the terminal status and output.
        pieces = _split(details, DETAILS_CHUNK_MAX_CHARS)
        for index, piece in enumerate(pieces):
            is_last = index == len(pieces) - 1
            chunk = TaskUpdateChunk(
                id=card.id,
                title=card.title,
                status=status if is_last else "in_progress",
                details=piece,
                output=output if is_last else None,
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


def _split(text: str, max_chars: int) -> list[str]:
    """Break an append-only details payload into chunk-sized pieces.

    Splits on the "\\n• " line boundaries so a tool name is never cut in half.
    A single line longer than ``max_chars`` is clipped, since it cannot fit.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for line in re.findall(r"\n?[^\n]*", text):
        if not line:
            continue
        if len(line) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(_clip(line, max_chars))
            continue
        if len(current) + len(line) > max_chars:
            pieces.append(current)
            current = line
        else:
            current += line
    if current:
        pieces.append(current)
    return pieces


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
