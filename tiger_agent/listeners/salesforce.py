import asyncio
from asyncio import TaskGroup
from datetime import datetime

import logfire
import schedule

from tiger_agent.db.utils import insert_event, is_case_assignment_new
from tiger_agent.listeners import Listener
from tiger_agent.salesforce.case_feed_item_poller import SalesforceCaseFeedItemPoller
from tiger_agent.salesforce.constants import (
    CASE_ID_FIELD,
    CASE_OWNER_ID_FIELD,
    SALESFORCE_CASE_CHANNEL,
)
from tiger_agent.salesforce.new_case_poller import SalesforceNewCasePoller
from tiger_agent.salesforce.types import (
    CaseData,
    SalesforceAssignmentChangedEvent,
    SalesforceFeedItem,
    SalesforceFeedItemEvent,
)
from tiger_agent.salesforce.utils import (
    should_ignore_new_case,
    subscribe_to_topic,
)
from tiger_agent.types import Context


class SalesforceListener(Listener):
    def __init__(self, ctx: Context):
        assert ctx.salesforce_client is not None, "salesforce_client is required"
        self._salesforce_client = ctx.salesforce_client
        self._pool = ctx.pool
        self._trigger = ctx.trigger
        self._new_case_poller: SalesforceNewCasePoller | None
        self._feed_item_poller: SalesforceCaseFeedItemPoller | None

    @logfire.instrument("SalesforceEventHandler start")
    async def start(self, tasks: TaskGroup):

        self._new_case_poller = SalesforceNewCasePoller(
            pool=self._pool,
            salesforce_client=self._salesforce_client,
            handler=self.handle_updated_case_assignee,
        )

        self._feed_item_poller = SalesforceCaseFeedItemPoller(
            pool=self._pool,
            salesforce_client=self._salesforce_client,
            handler=self.handle_new_feed_item,
        )

        tasks.create_task(self._subscribe_to_case_assignee_changed())
        tasks.create_task(self._run_schedule())

        # poller will look for cases that have been created+assigned
        # that the agent has "missed"
        self._new_case_poller.start(run_immediate=True)

        # similarly, look for case feed items that the agent missed
        self._feed_item_poller.start(run_immediate=True)

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

    async def _run_schedule(self):
        while True:
            schedule.run_pending()
            await asyncio.sleep(1)

    @logfire.instrument("handle_updated_case_assignee", extract_args=["case"])
    async def handle_updated_case_assignee(self, case: CaseData):
        if not SALESFORCE_CASE_CHANNEL:
            logfire.warn(
                "A new case was created, but no Slack channel configured",
                extra={"case": case.model_dump_json()},
            )
            return

        if not case.Owner.Email:
            # no user assigned yet
            logfire.info("Ignoring event, no user assigned to case")
            return

        if case.Status != "New":
            logfire.info("Ignoring case event as status is not new")
            return

        if should_ignore_new_case(case):
            logfire.info("Ignoring case")
            return

        if not await is_case_assignment_new(
            case_id=case.Id, owner_id=case.Owner.Id, pool=self._pool
        ):
            logfire.info("Ignoring case event as owner has not changed")
            return

        full_case_data = self._salesforce_client.Case.get(case.Id)

        await insert_event(
            pool=self._pool,
            event=SalesforceAssignmentChangedEvent(case=full_case_data).model_dump(),
        )

        await self._trigger.put(True)

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

    @logfire.instrument("handle_new_feed_item", extract_args=False)
    async def handle_new_feed_item(self, feed_item: SalesforceFeedItem):

        event_ts = None
        if feed_item.CreatedDate:
            dt = datetime.fromisoformat(feed_item.CreatedDate)
            event_ts = str(dt.timestamp())

        await insert_event(
            pool=self._pool,
            event=SalesforceFeedItemEvent(
                feed_item=feed_item, event_ts=event_ts
            ).model_dump(mode="json"),
        )
        await self._trigger.put(True)
