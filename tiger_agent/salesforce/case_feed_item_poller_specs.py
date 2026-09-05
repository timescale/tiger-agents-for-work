import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tiger_agent.salesforce.case_feed_item_poller import SalesforceCaseFeedItemPoller

MODULE = "tiger_agent.salesforce.case_feed_item_poller"


@pytest.fixture
def poller(make_pool_mock):
    return SalesforceCaseFeedItemPoller(
        pool=make_pool_mock(),
        salesforce_client=MagicMock(),
        handler=AsyncMock(),
        poll_interval_seconds=20,
    )


@pytest.fixture
def stub_salesforce():
    """Stub the two Salesforce fetches and the dedupe so _poll is pure."""
    with (
        patch(f"{MODULE}.get_case_feed_items", return_value=[]) as feed,
        patch(f"{MODULE}.get_case_email_messages", return_value=[]) as email,
        patch(f"{MODULE}.filter_new_feed_items", new=AsyncMock(return_value=[])) as flt,
    ):
        yield {"feed": feed, "email": email, "filter": flt}


class TestPollCursor:
    async def test_advances_after_a_successful_poll(self, poller, stub_salesforce):
        assert poller._last_poll is None

        await poller._poll()

        assert poller._last_poll is not None

    async def test_does_not_advance_when_the_poll_raises(self, poller, stub_salesforce):
        """A mid-poll failure must not skip the window it never processed."""
        stub_salesforce["filter"].side_effect = RuntimeError("salesforce is down")

        with pytest.raises(RuntimeError):
            await poller._poll()

        assert poller._last_poll is None

    async def test_a_failed_poll_leaves_the_window_start_unchanged(
        self, poller, stub_salesforce
    ):
        await poller._poll()
        first_cursor = poller._last_poll

        stub_salesforce["filter"].side_effect = RuntimeError("transient")
        with pytest.raises(RuntimeError):
            await poller._poll()

        assert poller._last_poll == first_cursor


class TestPollReentrancy:
    async def test_skips_the_tick_while_a_poll_is_in_flight(self, poller):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_poll():
            started.set()
            await release.wait()

        with patch.object(poller, "_poll", side_effect=slow_poll):
            await poller._poll_if_idle()
            await started.wait()
            first_task = poller._poll_task

            # second tick arrives while the first is still running
            await poller._poll_if_idle()
            assert poller._poll_task is first_task

            release.set()
            await first_task

    async def test_starts_a_new_poll_once_the_previous_finished(self, poller):
        with patch.object(poller, "_poll", new=AsyncMock()):
            await poller._poll_if_idle()
            first_task = poller._poll_task
            await first_task

            await poller._poll_if_idle()
            assert poller._poll_task is not first_task
            await poller._poll_task


class TestDedupePredicate:
    def test_dedupe_does_not_key_on_the_mutable_timestamp(self):
        """EmailMessage event_ts derives from MessageDate, which Salesforce revises."""
        import inspect

        from tiger_agent.db.utils import filter_new_feed_items

        # Comments in this function legitimately discuss event_ts, so assert
        # against the code only.
        code = "\n".join(
            line
            for line in inspect.getsource(filter_new_feed_items).splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "feed_item'->>'Id' = elem->>'id'" in code
        assert "event_ts" not in code
