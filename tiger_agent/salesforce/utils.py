import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.clients import (
    ClientCredentialsAuthenticator,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
)
from tiger_agent.salesforce.types import CaseData

RECONNECT_DELAY_SECONDS = 30


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
                logfire.info("Subscribed to PushTopic ", extra={"topic_name": topic_name})

                async for message in streaming_client:
                    sobject = message.get("data", {}).get("sobject", {})

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
