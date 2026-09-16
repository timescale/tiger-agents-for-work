import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import PartStartEvent, TextPart
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncChatStream

from tiger_agent.slack.types import SlackAppMentionEvent
from tiger_agent.tasks.cancellation import RunCancellations
from tiger_agent.tasks.handlers import slack as handler_module
from tiger_agent.tasks.handlers.slack import SlackTaskHandler
from tiger_agent.tasks.types import Task

CHANNEL = "C_CHAN"
TS = "1700000000.000100"

_CANCELLED = object()


class _FakeRunEvents:
    """Stands in for pydantic-ai's AgentRunEvents: a queue-fed async iterator with cancel()."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.cancel_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is _CANCELLED:
            raise RunCancelled("The agent run was cancelled.")
        if item is None:
            raise StopAsyncIteration
        return item

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.queue.put_nowait(_CANCELLED)


def _make_task() -> Task:
    return Task(
        id=46029,
        event_ts=datetime(2026, 9, 16, tzinfo=UTC),
        attempts=1,
        vt=datetime(2026, 9, 16, tzinfo=UTC),
        claimed=[],
        event=SlackAppMentionEvent(
            ts=TS,
            channel=CHANNEL,
            user="U_USER",
            team="T_HOME",
            text="<@U_BOT> what happened yesterday?",
            event_ts=TS,
        ),
    )


def _live_stream(ts: str = "1700000000.000200") -> MagicMock:
    stream = MagicMock(spec=AsyncChatStream)
    stream._buffer = ""
    stream._state = "in_progress"
    stream._stream_ts = ts
    stream.append = AsyncMock(return_value=None)
    stream.stop = AsyncMock(return_value={"ok": True})
    stream._flush_buffer = AsyncMock(return_value=None)
    return stream


def _slack_error(code: str) -> SlackApiError:
    return SlackApiError(
        "The request to the Slack API failed.", {"ok": False, "error": code}
    )


@pytest.fixture
def run_events() -> _FakeRunEvents:
    return _FakeRunEvents()


@pytest.fixture
def client(make_async_web_client_mock):
    client = make_async_web_client_mock()
    client.chat_delete = AsyncMock(return_value={"ok": True})
    return client


@pytest.fixture
def hctx(client, make_pool_mock, make_bot_info):
    hctx = MagicMock()
    hctx.app.client = client
    hctx.pool = make_pool_mock()
    hctx.bot_info = make_bot_info(team_id="T_HOME")
    hctx.cancellations = RunCancellations()
    return hctx


@pytest.fixture
def handler(hctx, run_events, monkeypatch) -> SlackTaskHandler:
    monkeypatch.setattr(handler_module, "user_ignored", AsyncMock(return_value=False))
    monkeypatch.setattr(
        handler_module, "usage_limit_reached", AsyncMock(return_value=False)
    )
    agent = MagicMock()
    agent.run_stream_events = MagicMock(return_value=run_events)
    monkeypatch.setattr(
        handler_module,
        "create_agent_and_context",
        AsyncMock(
            return_value=SimpleNamespace(agent=agent, user_prompt="prompt", ctx={})
        ),
    )
    tiger_agent = MagicMock()
    tiger_agent.rate_limit_interval = "1 hour"
    tiger_agent.rate_limit_allowed_requests = 100
    return SlackTaskHandler(hctx, tiger_agent)


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


class TestCancelOnMessageDeleted:
    async def test_deleting_the_question_stops_the_run_and_cleans_up(
        self, handler, hctx, client, run_events
    ):
        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        run = asyncio.create_task(handler.handle(_make_task()))

        # the run streams an intro, then blocks waiting on the agent
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )
        await _settle()
        assert hctx.cancellations.is_tracked(CHANNEL, TS)

        # the listener sees message_deleted for this message
        assert hctx.cancellations.cancel(CHANNEL, TS) is True
        await asyncio.wait_for(run, timeout=1)

        assert run_events.cancel_calls == 1
        assert not hctx.cancellations.is_tracked(CHANNEL, TS)
        # the partial intro is removed and the status cleared
        client.chat_delete.assert_awaited_once_with(
            channel=CHANNEL, ts=stream._stream_ts
        )
        assert client.assistant_threads_setStatus.await_args.kwargs["status"] == ""
        # nothing celebrates a run that answered nobody
        client.reactions_add.assert_not_awaited()

    async def test_a_completed_run_is_untouched_by_the_registry(
        self, handler, hctx, client, run_events
    ):
        client.chat_stream = AsyncMock(return_value=_live_stream())
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="Here you go."))
        )
        run_events.queue.put_nowait(None)

        await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        assert run_events.cancel_calls == 0
        assert not hctx.cancellations.is_tracked(CHANNEL, TS)
        client.chat_delete.assert_not_awaited()
        client.reactions_add.assert_awaited_once()


class TestTargetGone:
    async def test_invalid_thread_ts_ends_the_run_without_raising(
        self, handler, hctx, client, run_events
    ):
        # both the first stream and the retry stream are rejected
        dead = _live_stream()
        dead.append = AsyncMock(side_effect=_slack_error("invalid_thread_ts"))
        client.chat_stream = AsyncMock(return_value=dead)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )

        await asyncio.wait_for(handler.handle(_make_task()), timeout=1)  # no raise

        assert run_events.cancel_calls == 1
        assert not hctx.cancellations.is_tracked(CHANNEL, TS)
        client.reactions_add.assert_not_awaited()

    async def test_other_slack_errors_still_propagate(
        self, handler, hctx, client, run_events
    ):
        dead = _live_stream()
        dead.append = AsyncMock(side_effect=_slack_error("ratelimited"))
        client.chat_stream = AsyncMock(return_value=dead)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )

        with pytest.raises(SlackApiError):
            await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        assert not hctx.cancellations.is_tracked(CHANNEL, TS)


class TestCancelDuringSetup:
    async def test_deletion_during_agent_setup_stops_the_run_before_it_starts(
        self, handler, hctx, client, run_events, monkeypatch
    ):
        """Registration precedes create_agent_and_context, so a cancel that
        arrives while MCP servers and thread history are being prepared is
        honoured instead of lost (the production repro: deleted 2.5 s in,
        `cancelled_run=false`)."""
        setup_started = asyncio.Event()
        release_setup = asyncio.Event()
        agent = MagicMock()
        agent.run_stream_events = MagicMock(return_value=run_events)

        async def slow_create_agent_and_context(**_kwargs):
            setup_started.set()
            await release_setup.wait()
            return SimpleNamespace(agent=agent, user_prompt="prompt", ctx={})

        monkeypatch.setattr(
            handler_module, "create_agent_and_context", slow_create_agent_and_context
        )
        run = asyncio.create_task(handler.handle(_make_task()))
        await asyncio.wait_for(setup_started.wait(), timeout=1)

        # already registered while setup is still in flight
        assert hctx.cancellations.is_tracked(CHANNEL, TS)
        assert hctx.cancellations.cancel(CHANNEL, TS) is True
        release_setup.set()
        await asyncio.wait_for(run, timeout=1)

        agent.run_stream_events.assert_not_called()
        client.chat_stream.assert_not_awaited()
        client.reactions_add.assert_not_awaited()
        assert client.assistant_threads_setStatus.await_args.kwargs["status"] == ""
        assert not hctx.cancellations.is_tracked(CHANNEL, TS)
