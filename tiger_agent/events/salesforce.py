from asyncio import TaskGroup
from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.clients import (
    ClientCredentialsAuthenticator,
    get_salesforce_api_client,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    CASE_ID_FIELD,
    CASE_OWNER_ID_FIELD,
    SALESFORCE_CASE_CHANNEL,
)
from tiger_agent.salesforce.types import CaseData


class SalesforceEventHandler:
    def __init__(self):
        self._salesforce_client: Salesforce | None

    @logfire.instrument("SalesforceEventHandler start")
    async def start(self, tasks: TaskGroup):
        self._salesforce_client = get_salesforce_api_client()
        tasks.create_task(self.subscribe_to_new_cases())
        tasks.create_task(self.subscribe_to_case_assignee_changed())

    def upsert_case_push_topic_definition(
        self,
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

        results = self._salesforce_client.query(
            f"SELECT Id FROM PushTopic WHERE Name = '{topic_name}'"
        )
        if results["totalSize"] > 0:
            topic_id = results["records"][0]["Id"]
            logfire.info("PushTopic already exists, updating", extra={"id": topic_id})
            self._salesforce_client.PushTopic.update(topic_id, topic_config)
        else:
            result = self._salesforce_client.PushTopic.create(topic_config)
            logfire.info("PushTopic created", extra={"id": result["id"]})

    async def subscribe_to_topic(
        self,
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
                    result = self._salesforce_client.query(
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

    async def handle_new_case(self, case: CaseData):
        if not SALESFORCE_CASE_CHANNEL:
            logfire.warn(
                "A new case was created, but no Slack channel configured",
                extra={"case": case.model_dump_json()},
            )
        logfire.info("New case", extra={"case": case.model_dump_json()})

    async def handle_updated_case_assignee(case: CaseData):
        logfire.info("Case assignee changed", extra={"case": case.model_dump_json()})

    async def subscribe_to_new_cases(self):
        try:
            topic_name = "NewCasesTopic"
            self._upsert_case_push_topic_definition(
                topic_name=topic_name,
                fields=CASE_FIELDS,
                notifyOnCreate=True,
            )
            await self.subscribe_to_topic(
                topic_name=topic_name,
                handler=self.handle_new_case,
            )
        except Exception:
            logfire.exception("Error in subscribe_to_new_cases")

    async def subscribe_to_case_assignee_changed(self):
        try:
            topic_name = "CaseOwnerChangedTopic"
            self.upsert_case_push_topic_definition(
                topic_name=topic_name,
                fields=[CASE_ID_FIELD, CASE_OWNER_ID_FIELD],
                notifyOnCreate=False,
                notifyOnUpdate=True,
            )
            await self.subscribe_to_topic(
                topic_name=topic_name,
                handler=self.handle_updated_case_assignee,
            )
        except Exception:
            logfire.exception("Error in subscribe_to_case_assignee_changed")
