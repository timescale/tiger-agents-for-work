import asyncio
import os
from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError
from simple_salesforce.api import Salesforce

from tiger_agent.events.types import Event
from tiger_agent.salesforce.clients import (
    ClientCredentialsAuthenticator,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    SALESFORCE_DOMAIN,
)
from tiger_agent.salesforce.types import CaseData, SalesforceBaseEvent

RECONNECT_DELAY_SECONDS = 30
IGNORED_CONTACT_EMAILS = set(
    os.environ.get("SALESFORCE_IGNORE_CONTACT_EMAILS", "")
    .lower()
    .replace(" ", "")
    .split(",")
)

logfire.info("Salesforce ignore list", extra={"list": IGNORED_CONTACT_EMAILS})


async def subscribe_to_topic(
    salesforce_client: Salesforce,
    topic_name: str,
    handler: Callable[[CaseData], Coroutine[Any, Any, None]],
):
    channel = f"/topic/{topic_name}"
    while True:
        try:
            async with Client(ClientCredentialsAuthenticator()) as streaming_client:
                await streaming_client.subscribe(channel)
                logfire.info(
                    "Subscribed to PushTopic ", extra={"topic_name": topic_name}
                )

                async for message in streaming_client:
                    data = message.get("data", {})
                    sobject = data.get("sobject", {})

                    case_id = sobject.get("Id")
                    if not case_id:
                        continue

                    try:
                        fields = ", ".join(CASE_FIELDS)
                        result = salesforce_client.query(
                            f"SELECT {fields} FROM Case WHERE Id = '{case_id}' LIMIT 1"
                        )
                        if not result["records"]:
                            logfire.warning(
                                "Case not found after CDC event", extra={"id": case_id}
                            )
                            continue
                        case = CaseData(**result["records"][0])

                        logfire.info(
                            "Handling Salesforce event",
                            extra={"topic": topic_name, "payload": data},
                        )
                        await handler(case)
                    except Exception:
                        logfire.exception("Error handling new case", case_id=case_id)

            logfire.warning(
                "Streaming client exited unexpectedly, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )
        except Exception:
            logfire.exception(
                "Streaming connection error, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )

        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


def should_ignore_new_case(case: CaseData) -> bool:
    if not case.ContactEmail:
        return False
    return case.ContactEmail in IGNORED_CONTACT_EMAILS


@logfire.instrument("is_case_assignment_new", extract_args=False)
async def is_case_assignment_new(
    pool: AsyncConnectionPool, case_id: str, owner_id: str
) -> bool:
    """
    Verifies if the Salesforce case assignment is new e.g. if the assigned user (owner) has actually changed.
    This is done by reading the event and event_hist table for the most recent event for that case


    Args:
        case_id: The ID of the Salesforce case
        owner_id: The ID of the assignee

    Returns:
        True if the given owner id is different from the most recently processed/unprocessed
        Salesforce event
    """
    async with (
        pool.connection() as con,
        con.cursor(row_factory=dict_row) as cur,
    ):
        result = await cur.execute(
            """select * from agent.event
                WHERE
                    event->>'type' = 'salesforce_event'
                    AND event->>'subtype' = 'new_assignee'
                    AND event->'case'->>'Id' = %s
                    order by event_ts desc limit 1;""",
            (case_id,),
        )
        current_row: dict[str, Any] | None = await result.fetchone()

        if current_row:
            try:
                event = Event(**current_row)

                # there is an unprocessed new_assignee event
                # that has the same owner
                if (
                    isinstance(event.event, SalesforceBaseEvent)
                    and event.event.case.OwnerId == owner_id
                ):
                    return False
            except ValidationError as e:
                logfire.error(
                    "failed to parse historical event",
                    exc_info=e,
                    extra={"row": current_row},
                )

        result = await cur.execute(
            """select * from agent.event_hist 
                WHERE
                    event->>'type' = 'salesforce_event'
                    AND event->>'subtype' = 'new_assignee'
                    AND event->'case'->>'Id' = %s
                    order by event_ts desc limit 1;""",
            (case_id,),
        )
        processed_row: dict[str, Any] | None = await result.fetchone()
        if processed_row:
            try:
                event = Event(**processed_row)

                # there is an processed new_assignee event
                # that has the same owner
                if (
                    isinstance(event.event, SalesforceBaseEvent)
                    and event.event.case.OwnerId == owner_id
                ):
                    return False
            except ValidationError as e:
                logfire.error(
                    "failed to parse historical event",
                    exc_info=e,
                    extra={"row": processed_row},
                )

        return True


def create_case_url(case: CaseData) -> str:
    return f"https://{SALESFORCE_DOMAIN}/lightning/r/Case/{case.Id}/view"
