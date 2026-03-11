from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.clients import ClientCredentialsAuthenticator
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    CASE_ID_FIELD,
    CASE_OWNER_ID_FIELD,
    SALESFORCE_CASE_CHANNEL,
)
from tiger_agent.salesforce.types import CaseData


def upsert_case_push_topic_definition(
    salesforce_client: Salesforce,
    topic_name: str,
    fields: list[str],  # the fields that should trigger the push event
    notifyOnCreate: bool = False,
    notifyOnUpdate: bool = False,
) -> None:
    """This method ensures that a push topic is created for a case"""
    fields = ", ".join(fields)
    topic_config = {
        "Name": topic_name,
        "Query": f"SELECT {fields} FROM Case",
        "ApiVersion": "64.0",
        "NotifyForOperationCreate": notifyOnCreate,
        "NotifyForOperationUpdate": notifyOnUpdate,
        "NotifyForOperationUndelete": False,
        "NotifyForOperationDelete": False,
        "NotifyForFields": "Referenced",
    }

    results = salesforce_client.query(
        f"SELECT Id FROM PushTopic WHERE Name = '{topic_name}'"
    )
    if results["totalSize"] > 0:
        topic_id = results["records"][0]["Id"]
        logfire.info("PushTopic already exists, updating", extra={"id": topic_id})
        salesforce_client.PushTopic.update(topic_id, topic_config)
    else:
        result = salesforce_client.PushTopic.create(topic_config)
        logfire.info("PushTopic created", extra={"id": result["id"]})


async def subscribe_to_topic(
    salesforce_client: Salesforce,
    topic_name: str,
    handler: Callable[[CaseData], Coroutine[Any, Any, None]],
):
    channel = f"/topic/{topic_name}"
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


async def handle_new_case(case: CaseData):
    if not SALESFORCE_CASE_CHANNEL:
        logfire.warn(
            "A new case was created, but no Slack channel configured",
            extra={"case": case.model_dump_json()},
        )
    logfire.info("New case", extra={"case": case.model_dump_json()})


async def handle_updated_case_assignee(case: CaseData):
    logfire.info("Case assignee changed", extra={"case": case.model_dump_json()})


async def subscribe_to_new_cases(salesforce_client: Salesforce):
    try:
        topic_name = "NewCasesTopic"
        upsert_case_push_topic_definition(
            salesforce_client=salesforce_client,
            topic_name=topic_name,
            fields=CASE_FIELDS,
            notifyOnCreate=True,
        )
        await subscribe_to_topic(
            salesforce_client=salesforce_client,
            topic_name=topic_name,
            handler=handle_new_case,
        )
    except Exception:
        logfire.exception("Error in subscribe_to_new_cases")


async def subscribe_to_case_assignee_changed(salesforce_client: Salesforce):
    try:
        topic_name = "CaseOwnerChangedTopic"
        upsert_case_push_topic_definition(
            salesforce_client=salesforce_client,
            topic_name=topic_name,
            fields=[CASE_ID_FIELD, CASE_OWNER_ID_FIELD],
            notifyOnCreate=False,
            notifyOnUpdate=True,
        )
        await subscribe_to_topic(
            salesforce_client=salesforce_client,
            topic_name=topic_name,
            handler=handle_updated_case_assignee,
        )
    except Exception:
        logfire.exception("Error in subscribe_to_case_assignee_changed")
