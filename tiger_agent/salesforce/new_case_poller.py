"""
This will call Salesforce's API to find all cases created in the last day
and then find any of those cases that have not been processed/are being processed by
the agent. Any cases that were missed are inserted into agent.events to be processed.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import logfire
import schedule
from psycopg_pool import AsyncConnectionPool
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.constants import CASE_FIELDS
from tiger_agent.salesforce.types import CaseData

logger = logging.getLogger(__name__)


class SalesforceNewCasePoller:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        salesforce_client: Salesforce,
        handler: Callable[[CaseData], Coroutine[Any, Any, None]],
    ):
        self._pool = pool
        self._salesforce_client = salesforce_client
        self._handler = handler

    @logfire.instrument("_process_missed_cases")
    async def _process_missed_cases(self) -> None:
        since = datetime.now(UTC) - timedelta(days=1)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        fields = ", ".join(CASE_FIELDS)
        # condition on Owner.Email ensures we are only handling cases that have been assigned
        result = self._salesforce_client.query(
            f"SELECT {fields} FROM Case WHERE CreatedDate >= {since_str} AND Owner.Email != null AND Status = 'New'"
        )
        cases = [CaseData(**record) for record in result.get("records", [])]

        if not cases:
            return

        case_ids = [case.Id for case in cases]
        placeholders = ", ".join(["%s"] * len(case_ids))

        async with self._pool.connection() as con:
            rows = await con.execute(
                f"""
                SELECT event->'case'->>'Id' AS case_id
                FROM (
                    SELECT event, event_ts FROM agent.event
                    WHERE event->>'type' = 'salesforce_event'
                    AND event_ts >= %s
                    UNION ALL
                    SELECT event, event_ts FROM agent.event_hist
                    WHERE event->>'type' = 'salesforce_event'
                    AND event_ts >= %s
                ) combined
                WHERE event->'case'->>'Id' IN ({placeholders})
                """,
                (since, since, *case_ids),
            )
            existing_ids = {row[0] for row in await rows.fetchall()}

        new_cases = [case for case in cases if case.Id not in existing_ids]

        for case in new_cases:
            logfire.info(
                "Inserting missed new case",
                case_id=case.Id,
                case_number=case.CaseNumber,
            )
            await self._handler(case)

    def start(self) -> None:
        def job():
            asyncio.create_task(self._process_missed_cases())

        schedule.every(5).minutes.do(job)

        logger.info("Scheduled poll_missed_new_cases every 5 minutes")
