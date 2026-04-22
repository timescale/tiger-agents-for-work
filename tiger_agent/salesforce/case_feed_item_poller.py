"""
Poll Salesforce for new Chatter feed items on cases.

Neither FeedItem nor CaseComment support PushTopics or CDC, so we poll
the Chatter news feed on an interval and call the handler for any new
items found since the last poll.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

import logfire
import schedule
from psycopg_pool import AsyncConnectionPool
from pytz import UTC
from simple_salesforce.api import Salesforce

from tiger_agent.db.utils import filter_new_feed_items
from tiger_agent.salesforce.types import SalesforceFeedItem
from tiger_agent.salesforce.utils import get_recent_case_feed_items

logger = logging.getLogger(__name__)

# on startup, how far back we should grab feeditems from
INITIAL_LOOKBACK_IN_HOURS = 12


class SalesforceCaseFeedItemPoller:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        salesforce_client: Salesforce,
        handler: Callable[[SalesforceFeedItem], Coroutine[Any, Any, None]],
        poll_interval_seconds: int = 20,
    ):
        self._salesforce_client = salesforce_client
        self._pool = pool
        self._handler = handler
        self._poll_interval_seconds = poll_interval_seconds
        self._last_poll: datetime | None = None

    async def _poll(self) -> None:
        # TODO: can improve the fallback by doing a query on the last feed item event in the db
        since = self._last_poll or (
            datetime.now(UTC) - timedelta(hours=INITIAL_LOOKBACK_IN_HOURS)
        )
        self._last_poll = datetime.now(UTC)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        case_feed_items = get_recent_case_feed_items(
            salesforce_client=self._salesforce_client,
            types=["TextPost", "ContentPost"],
            public_only=True,
            created_after=since_str,
        )

        # filter out feed items that have already been handled
        filtered_new_feed_items = await filter_new_feed_items(
            self._pool, feed_items=case_feed_items
        )

        if not filtered_new_feed_items:
            return

        logfire.info("New case feed items found", count=len(filtered_new_feed_items))
        for feed_item in filtered_new_feed_items:
            try:
                await self._handler(feed_item)
            except Exception:
                logfire.exception(
                    "Error handling new feed item", feed_item_id=feed_item.Id
                )

    def start(self, run_immediate: bool = False) -> None:
        def job():
            asyncio.create_task(self._poll())

        schedule.every(self._poll_interval_seconds).seconds.do(job)
        logger.info(
            "Scheduled case feed poll every %d second(s)",
            self._poll_interval_seconds,
        )

        if not run_immediate:
            return

        job()
