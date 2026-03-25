import asyncio
from asyncio import TaskGroup

import logfire
import schedule
from simple_salesforce.api import Salesforce

from tiger_agent.db.utils import insert_event
from tiger_agent.events.types import HarnessContext
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    CASE_ID_FIELD,
    CASE_OWNER_ID_FIELD,
    SALESFORCE_CASE_CHANNEL,
)
from tiger_agent.salesforce.new_case_poller import SalesforceNewCasePoller
from tiger_agent.salesforce.types import (
    CaseData,
    SalesforceAssignmentChangedEvent,
    SalesforceNewCaseEvent,
)
from tiger_agent.salesforce.utils import (
    is_case_assignment_new,
    should_ignore_new_case,
    subscribe_to_topic,
)


class SalesforceEventHandler:
    def __init__(self, hctx: HarnessContext):
        self._salesforce_client: Salesforce | None = hctx.salesforce_client
        self._pool = hctx.pool
        self._trigger = hctx.trigger
        self._new_case_poller: SalesforceNewCasePoller | None

    @logfire.instrument("SalesforceEventHandler start")
    async def start(self, tasks: TaskGroup):

        self._new_case_poller = SalesforceNewCasePoller(
            pool=self._pool,
            salesforce_client=self._salesforce_client,
            handler=self.handle_updated_case_assignee,
        )

        # for now, we are going to use just assignment events
        # tasks.create_task(self._subscribe_to_new_cases())

        tasks.create_task(self._subscribe_to_case_assignee_changed())
        self._new_case_poller.start()
        tasks.create_task(self._run_schedule())

        await self._new_case_poller._process_missed_cases()

    def _upsert_case_push_topic_definition(
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

    @logfire.instrument("handle_new_case", extract_args=["case"])
    async def handle_new_case(self, case: CaseData):
        if not SALESFORCE_CASE_CHANNEL:
            logfire.warn(
                "A new case was created, but no Slack channel configured",
                extra={"case": case.model_dump_json()},
            )
            return
        if case.Status == "Spam":
            logfire.info("Ignoring case flagged as spam")
            return

        if should_ignore_new_case(case):
            logfire.info("Ignoring case")
            return

        full_case_data = self._salesforce_client.Case.get(case.Id)
        case = case.model_copy(
            update={"Description": full_case_data.get("Description")}
        )

        await insert_event(
            pool=self._pool,
            event=SalesforceNewCaseEvent(case=case).model_dump(),
        )

        await self._trigger.put(True)

    async def _run_schedule(self):
        while True:
            schedule.run_pending()
            await asyncio.sleep(1)

    @logfire.instrument("handle_updated_case_assignee", extract_args=["case"])
    async def handle_updated_case_assignee(self, case: CaseData):
        if not case.Owner.Email:
            # no user assigned yet
            logfire.info("Ignoring event, no user assigned to case")
            return

        if case.Status != "New":
            logfire.info("Ignoring case event as status is not new")
            return

        if not await is_case_assignment_new(
            case_id=case.Id, owner_id=case.Owner.Id, pool=self._pool
        ):
            logfire.info("Ignoring case event as owner has not changed")
            return

        full_case_data = self._salesforce_client.Case.get(case.Id)
        case = case.model_copy(
            update={
                "Description": full_case_data.get("Description"),
            }
        )

        await insert_event(
            pool=self._pool,
            event=SalesforceAssignmentChangedEvent(case=case).model_dump(),
        )

        await self._trigger.put(True)

    async def _subscribe_to_new_cases(self):
        try:
            topic_name = "NewCasesTopic"
            self._upsert_case_push_topic_definition(
                topic_name=topic_name,
                fields=CASE_FIELDS,
                notifyOnCreate=True,
            )
            await subscribe_to_topic(
                salesforce_client=self._salesforce_client,
                topic_name=topic_name,
                handler=self.handle_new_case,
            )
        except Exception:
            logfire.exception("Error in subscribe_to_new_cases")

    async def _subscribe_to_case_assignee_changed(self):
        try:
            topic_name = "CaseOwnerChangedTopic"
            self._upsert_case_push_topic_definition(
                topic_name=topic_name,
                fields=[CASE_ID_FIELD, CASE_OWNER_ID_FIELD],
                notifyOnCreate=False,
                notifyOnUpdate=True,
            )
            await subscribe_to_topic(
                salesforce_client=self._salesforce_client,
                topic_name=topic_name,
                handler=self.handle_updated_case_assignee,
            )
        except Exception:
            logfire.exception("Error in subscribe_to_case_assignee_changed")
