import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.db.utils import (
    delete_unclaimed_slack_events,
    extend_event_visibility,
)
from tiger_agent.slack.types import SlackAppMentionEvent, SlackRequestNewCaseFormEvent
from tiger_agent.tasks import utils as task_utils
from tiger_agent.tasks.types import Task
from tiger_agent.tasks.utils import heartbeat_interval_seconds, process_task

UPDATE_SQL_HEAD = "update agent.event"


def _make_task(attempts: int = 1) -> Task:
    return Task(
        id=45755,
        event_ts=datetime(2026, 9, 15, tzinfo=UTC),
        attempts=attempts,
        vt=datetime(2026, 9, 15, tzinfo=UTC),
        claimed=[],
        event=SlackRequestNewCaseFormEvent(
            user="U_REQUESTER",
            channel="C_CHAN",
            trigger_message_ts="1700000000.000100",
        ),
    )


def _lease_updates(pool) -> list:
    return [
        call
        for call in pool._cursor.execute.await_args_list
        if call.args[0].startswith(UPDATE_SQL_HEAD)
    ]


def _deletes(pool) -> list:
    return [
        call
        for call in pool._cursor.execute.await_args_list
        if "agent.delete_event" in call.args[0]
    ]


@pytest.fixture
def pool(make_pool_mock):
    pool = make_pool_mock()
    pool._cursor.rowcount = 1
    return pool


@pytest.fixture
def hctx(pool, make_async_web_client_mock):
    hctx = MagicMock()
    hctx.pool = pool
    hctx.invisibility_minutes = 0  # so the floor decides the interval
    hctx.app.client = make_async_web_client_mock()
    hctx.cancellations.get_session_count.return_value = 0
    return hctx


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(task_utils, "HEARTBEAT_MIN_SECONDS", 0.01)


def _slow_processor(seconds: float, fail: bool = False):
    async def processor(_hctx, _task):
        await asyncio.sleep(seconds)
        if fail:
            raise RuntimeError("handler blew up")

    return processor


def _other_tasks() -> set[asyncio.Task]:
    return asyncio.all_tasks() - {asyncio.current_task()}


class TestHeartbeatInterval:
    def test_half_the_window_for_the_production_default(self, monkeypatch):
        monkeypatch.setattr(task_utils, "HEARTBEAT_MIN_SECONDS", 30.0)
        assert heartbeat_interval_seconds(10) == 300

    def test_floored_for_tiny_windows(self, monkeypatch):
        monkeypatch.setattr(task_utils, "HEARTBEAT_MIN_SECONDS", 30.0)
        assert heartbeat_interval_seconds(0) == 30
        assert heartbeat_interval_seconds(1) == 30


class TestExtendEventVisibility:
    async def test_updates_the_row_guarded_by_attempts(self, pool):
        held = await extend_event_visibility(
            pool=pool, event_id=45755, attempts=1, invisibility_minutes=10
        )

        assert held is True
        [call] = _lease_updates(pool)
        sql, params = call.args
        assert "attempts = %s" in sql
        assert "id = %s" in sql
        assert params == (10, 45755, 1)

    async def test_reports_a_lost_lease(self, pool):
        pool._cursor.rowcount = 0

        held = await extend_event_visibility(
            pool=pool, event_id=45755, attempts=1, invisibility_minutes=10
        )

        assert held is False


class TestProcessTaskHeartbeat:
    async def test_extends_the_lease_while_the_processor_runs(self, hctx, pool):
        task = _make_task(attempts=1)

        ok = await process_task(_slow_processor(0.06), hctx, task)

        assert ok is True
        updates = _lease_updates(pool)
        assert len(updates) >= 3
        assert all(call.args[1] == (0, task.id, task.attempts) for call in updates)
        assert len(_deletes(pool)) == 1

    async def test_heartbeat_is_cancelled_when_the_processor_finishes(self, hctx, pool):
        await process_task(_slow_processor(0.03), hctx, _make_task())

        assert _other_tasks() == set()
        count = len(_lease_updates(pool))
        await asyncio.sleep(0.05)
        assert len(_lease_updates(pool)) == count

    async def test_heartbeat_is_cancelled_when_the_processor_raises(self, hctx, pool):
        ok = await process_task(_slow_processor(0.03, fail=True), hctx, _make_task())

        assert ok is False
        assert _other_tasks() == set()
        assert _deletes(pool) == []

    async def test_lost_lease_stops_heartbeating_but_not_the_run(self, hctx, pool):
        pool._cursor.rowcount = 0

        ok = await process_task(_slow_processor(0.06), hctx, _make_task())

        assert ok is True
        assert len(_lease_updates(pool)) == 1
        assert len(_deletes(pool)) == 1

    async def test_database_errors_in_the_heartbeat_are_retried(self, hctx, pool):
        real_execute = pool._cursor.execute
        calls = {"n": 0}

        async def flaky_execute(sql, *args, **kwargs):
            if sql.startswith(UPDATE_SQL_HEAD):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise ConnectionError("db hiccup")
            return await real_execute(sql, *args, **kwargs)

        pool._cursor.execute = AsyncMock(side_effect=flaky_execute)

        ok = await process_task(_slow_processor(0.06), hctx, _make_task())

        assert ok is True
        assert calls["n"] >= 2

    async def test_no_heartbeat_fires_for_a_quick_task(self, hctx, pool, monkeypatch):
        monkeypatch.setattr(task_utils, "HEARTBEAT_MIN_SECONDS", 10.0)

        ok = await process_task(_slow_processor(0.0), hctx, _make_task())

        assert ok is True
        assert _lease_updates(pool) == []
        assert _other_tasks() == set()


class TestCancelBeforeClaim:
    async def test_cancelled_slack_event_is_dropped_without_archiving(
        self, hctx, pool, monkeypatch
    ):
        cancel_called = [False]

        async def fake_is_cancelled(*, pool, channel_id, thread_ts, user_id, event_ts):
            assert channel_id == "C_CHAN"
            assert user_id == "U_USER"
            cancel_called[0] = True
            return True

        monkeypatch.setattr(task_utils, "is_session_cancelled", fake_is_cancelled)

        task = Task(
            id=45755,
            event_ts=datetime(2026, 9, 15, tzinfo=UTC),
            attempts=1,
            vt=datetime(2026, 9, 15, tzinfo=UTC),
            claimed=[],
            event=SlackAppMentionEvent(
                ts="1700000000.000100",
                channel="C_CHAN",
                user="U_USER",
                team="T_HOME",
                text="hello",
                event_ts="1700000000.000100",
            ),
        )

        ok = await process_task(_slow_processor(0), hctx, task)

        assert ok is True
        assert cancel_called[0]
        # The event is deleted, not archived to event_hist
        assert _deletes(pool) == []
        con_calls = pool._connection.execute.await_args_list
        delete_calls = [c for c in con_calls if "DELETE FROM agent.event" in c.args[0]]
        assert len(delete_calls) == 1
        assert delete_calls[0].args[1] == (task.id,)
        # The session is reset because no local runs are active
        hctx.app.client.agents_sessions_setStatus.assert_awaited_once_with(
            channel_id="C_CHAN",
            thread_ts="1700000000.000100",
            status="active",
        )

    async def test_uncancelled_event_is_processed_normally(
        self, hctx, pool, monkeypatch
    ):
        is_cancelled = AsyncMock(return_value=False)
        monkeypatch.setattr(task_utils, "is_session_cancelled", is_cancelled)
        task = Task(
            id=45755,
            event_ts=datetime(2026, 9, 15, tzinfo=UTC),
            attempts=1,
            vt=datetime(2026, 9, 15, tzinfo=UTC),
            claimed=[],
            event=SlackAppMentionEvent(
                ts="1700000000.000100",
                channel="C_CHAN",
                user="U_USER",
                team="T_HOME",
                text="hello",
                event_ts="1700000000.000100",
            ),
        )

        ok = await process_task(_slow_processor(0), hctx, task)

        assert ok is True
        # The query was actually invoked with the thread and event timestamp.
        is_cancelled.assert_awaited_once()
        kwargs = is_cancelled.await_args.kwargs
        assert kwargs["channel_id"] == "C_CHAN"
        assert kwargs["thread_ts"] == "1700000000.000100"
        assert kwargs["user_id"] == "U_USER"
        assert kwargs["event_ts"] == task.event_ts
        assert len(_deletes(pool)) == 1


class TestDeleteUnclaimedSlackEvents:
    async def test_deletes_only_unclaimed_rows_for_that_message(self, pool):
        pool._cursor.rowcount = 2

        dropped = await delete_unclaimed_slack_events(
            pool, channel="C_CHAN", ts="1700000000.000100"
        )

        assert dropped == 2
        [call] = [
            c
            for c in pool._cursor.execute.await_args_list
            if c.args[0].startswith("delete from agent.event")
        ]
        sql, params = call.args
        assert "vt <= now()" in sql
        assert "event->>'channel' = %s" in sql
        assert "event->>'ts' = %s" in sql
        assert params == ("C_CHAN", "1700000000.000100")
