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
        if isinstance(item, BaseException):
            raise item
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
        # the partial intro is removed and the session goes back to active
        client.chat_delete.assert_awaited_once_with(
            channel=CHANNEL, ts=stream._stream_ts
        )
        assert client.agents_sessions_setStatus.await_args.kwargs["status"] == "active"
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
        assert client.agents_sessions_setStatus.await_args.kwargs["status"] == "active"
        assert not hctx.cancellations.is_tracked(CHANNEL, TS)


class TestSessionStatusOnUnexpectedFailure:
    """An unexpected crash must not strand the session showing "processing"."""

    async def test_an_unexpected_error_still_resets_the_session_status(
        self, handler, hctx, client, run_events
    ):
        client.chat_stream = AsyncMock(return_value=_live_stream())
        run_events.queue.put_nowait(RuntimeError("model blew up"))

        with pytest.raises(RuntimeError, match="model blew up"):
            await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        assert statuses and statuses[-1] == "active", (
            f"session left as {statuses[-1] if statuses else 'unset'!r}"
        )

    async def test_the_run_is_untracked_after_an_unexpected_error(
        self, handler, hctx, client, run_events
    ):
        """track()'s finally always runs, so the registry must not leak."""
        client.chat_stream = AsyncMock(return_value=_live_stream())
        run_events.queue.put_nowait(RuntimeError("model blew up"))

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        assert not hctx.cancellations.is_tracked(CHANNEL, TS)


class TestSessionStatusOnEarlyReturn:
    async def test_user_ignored_resets_session_status(
        self, handler, client, monkeypatch
    ):
        monkeypatch.setattr(
            handler_module, "user_ignored", AsyncMock(return_value=True)
        )
        await handler.handle(_make_task())

        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        assert statuses and statuses[-1] == "active"
        client.reactions_add.assert_not_awaited()

    async def test_usage_limit_reached_resets_session_status(
        self, handler, client, monkeypatch
    ):
        monkeypatch.setattr(
            handler_module, "usage_limit_reached", AsyncMock(return_value=True)
        )
        await handler.handle(_make_task())

        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        assert statuses and statuses[-1] == "active"
        client.reactions_add.assert_not_awaited()


class TestSessionTracking:
    async def test_early_exit_in_same_thread_keeps_session_busy(
        self, handler, hctx, client, run_events, monkeypatch
    ):
        """While one request is blocked, an ignored mention in the same
        thread must not flip the session back to active."""
        barrier = asyncio.Event()

        async def slow_create(**_kwargs):
            barrier.set()
            await asyncio.sleep(10)
            return SimpleNamespace(
                agent=MagicMock(), user_prompt="prompt", ctx={}
            )

        monkeypatch.setattr(
            handler_module, "create_agent_and_context", slow_create
        )
        monkeypatch.setattr(
            handler_module, "user_ignored", AsyncMock(return_value=False)
        )

        # Start a long-running task in the thread
        long_task = asyncio.create_task(handler.handle(_make_task()))
        await asyncio.wait_for(barrier.wait(), timeout=1)

        # Now a second task in the same thread exits early
        monkeypatch.setattr(
            handler_module, "user_ignored", AsyncMock(return_value=True)
        )
        await handler.handle(_make_task())

        # Session must still be processing because the first task is alive
        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        # The first task set busy, the early-return task did not add a new
        # "processing" (it saw count==2), and neither has exited yet.
        assert statuses.count("processing") == 1
        assert "active" not in statuses

        # Tear down the long task cleanly
        hctx.cancellations.cancel(CHANNEL, TS)
        long_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await long_task

    async def test_last_leaving_task_resets_session_to_active(
        self, handler, hctx, client, monkeypatch
    ):
        monkeypatch.setattr(
            handler_module, "user_ignored", AsyncMock(return_value=True)
        )
        await handler.handle(_make_task())
        await handler.handle(_make_task())

        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        # Each sequential task sets busy on enter and active on exit.
        # Because the pool_mock does not block on its context manager,
        # the first task fully completes before the second task starts
        # -- so there is never true overlap. Both get their own cycle.
        assert statuses.count("processing") == 2
        assert statuses.count("active") == 2

    async def test_set_busy_called_at_worker_start_for_fresh_task(
        self, handler, client
    ):
        await handler.handle(_make_task())
        statuses = [
            call.kwargs["status"]
            for call in client.agents_sessions_setStatus.await_args_list
        ]
        # The listener (not exercised here) also sets busy, but the handler
        # itself must ensure the status is set when work begins.
        assert "processing" in statuses


class TestUserlessEvent:
    async def test_workflow_mention_with_no_user_posts_via_post_response(
        self, handler, hctx, client, run_events, monkeypatch
    ):
        """When event.user is None, the handler streams nothing and falls
        back to post_response after the model run completes."""
        monkeypatch.setattr(handler_module, "user_ignored", AsyncMock(return_value=False))
        monkeypatch.setattr(
            handler_module, "usage_limit_reached", AsyncMock(return_value=False)
        )
        client.chat_stream = AsyncMock(return_value=_live_stream())
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="Done."))
        )
        run_events.queue.put_nowait(None)

        task = _make_task()
        task.event = SlackAppMentionEvent(
            ts=TS,
            channel=CHANNEL,
            user=None,  # e.g. a workflow or bot-posted mention
            team="T_HOME",
            text="what happened yesterday?",
            event_ts=TS,
        )

        await asyncio.wait_for(handler.handle(task), timeout=1)

        # No stream was started because there is no recipient_user_id
        client.chat_stream.assert_not_awaited()
        # Answer posted via post_response instead
        client.chat_postMessage.assert_awaited()
        # Session reset exactly once
        active_count = sum(
            1 for call in client.agents_sessions_setStatus.await_args_list
            if call.kwargs.get("status") == "active"
        )
        assert active_count == 1


class TestCancelSkipsCardClose:
    async def test_cancelled_run_does_not_close_cards(
        self, handler, hctx, client, run_events
    ):
        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )
        run = asyncio.create_task(handler.handle(_make_task()))
        await _settle()

        hctx.cancellations.cancel(CHANNEL, TS)
        await asyncio.wait_for(run, timeout=1)

        # On cancellation the stream is discarded, not stopped, and the card
        # close is skipped. discard() internally calls stream.stop() once (to
        # finalise before deleting), so exactly 1 stop call is expected.
        assert stream.stop.await_count == 1
        client.chat_delete.assert_awaited_once()

    async def test_no_card_append_on_cancel(
        self, handler, hctx, client, run_events
    ):
        """After a tool has opened a card, cancel must not attempt to close it."""
        from pydantic_ai.messages import ToolCallPart

        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        # Tool call opens a coordinator card
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=ToolCallPart(tool_name="slack_search", args={}))
        )
        run = asyncio.create_task(handler.handle(_make_task()))
        await _settle()

        hctx.cancellations.cancel(CHANNEL, TS)
        await asyncio.wait_for(run, timeout=1)

        # Only the initial card-open chunk is sent; no closing chunk after
        # cancellation because the discard path skips card appends.
        chunks = [c for c in stream.append.await_args_list if c.kwargs.get("chunks")]
        assert len(chunks) == 1  # only the tool-start update


class TestPollSessionCancel:
    async def test_db_cancel_triggers_run_cancellation(
        self, handler, hctx, client, run_events, monkeypatch
    ):
        """A session_cancel record written by another replica should still
        stop the run via the polling side task."""
        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )

        calls = {"n": 0}

        async def fake_is_cancelled(*, pool, channel_id, thread_ts, user_id, event_ts):
            calls["n"] += 1
            return calls["n"] >= 2  # False on first poll, True on second

        # Make the polling loop sleep for 0.01s instead of 5s so the test
        # completes in the 1s timeout window.
        monkeypatch.setattr(handler_module, "_POLL_INTERVAL_SECONDS", 0.01)

        monkeypatch.setattr(handler_module, "is_session_cancelled", fake_is_cancelled)

        run = asyncio.create_task(handler.handle(_make_task()))
        await asyncio.wait_for(run, timeout=1)

        assert run_events.cancel_calls == 1
        assert calls["n"] >= 2

    async def test_db_poll_errors_do_not_crash_the_run(
        self, handler, hctx, client, run_events, monkeypatch
    ):
        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="Here you go."))
        )
        run_events.queue.put_nowait(None)

        calls = {"n": 0}

        async def flaky(*, pool, channel_id, thread_ts, user_id, event_ts):
            calls["n"] += 1
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(handler_module, "is_session_cancelled", flaky)
        monkeypatch.setattr(handler_module, "_POLL_INTERVAL_SECONDS", 0.01)

        await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        # Run completes normally despite polling errors. We shortened the
        # poll interval so the path could fire, but the stream may finish
        # before the first poll wakes; the important thing is no crash.
        assert run_events.cancel_calls == 0
        client.reactions_add.assert_awaited_once()


class TestResetCalledExactlyOnce:
    async def test_cancelled_path_has_one_reset(
        self, handler, hctx, client, run_events
    ):
        stream = _live_stream()
        client.chat_stream = AsyncMock(return_value=stream)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )
        run = asyncio.create_task(handler.handle(_make_task()))
        await _settle()

        hctx.cancellations.cancel(CHANNEL, TS)
        await asyncio.wait_for(run, timeout=1)

        active_count = sum(
            1 for call in client.agents_sessions_setStatus.await_args_list
            if call.kwargs.get("status") == "active"
        )
        assert active_count == 1, (
            f"expected one active reset, got {active_count}"
        )

    async def test_target_gone_path_has_one_reset(
        self, handler, hctx, client, run_events
    ):
        dead = _live_stream()
        dead.append = AsyncMock(side_effect=_slack_error("invalid_thread_ts"))
        client.chat_stream = AsyncMock(return_value=dead)
        run_events.queue.put_nowait(
            PartStartEvent(index=0, part=TextPart(content="I'll look into that."))
        )

        await asyncio.wait_for(handler.handle(_make_task()), timeout=1)

        active_count = sum(
            1 for call in client.agents_sessions_setStatus.await_args_list
            if call.kwargs.get("status") == "active"
        )
        assert active_count == 1

