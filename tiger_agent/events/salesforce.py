from asyncio import TaskGroup

import logfire
from simple_salesforce.api import Salesforce

from tiger_agent.db.utils import insert_event
from tiger_agent.events.types import HarnessContext
from tiger_agent.salesforce.clients import (
    get_salesforce_api_client,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    CASE_ID_FIELD,
    CASE_OWNER_ID_FIELD,
    SALESFORCE_CASE_CHANNEL,
)
from tiger_agent.salesforce.types import CaseData, SalesforceNewCaseEvent
from tiger_agent.salesforce.utils import subscribe_to_topic


class SalesforceEventHandler:
    def __init__(self, hctx: HarnessContext):
        self._salesforce_client: Salesforce | None
        self._pool = hctx.pool
        self._trigger = hctx.trigger

    @logfire.instrument("SalesforceEventHandler start")
    async def start(self, tasks: TaskGroup):
        self._salesforce_client = get_salesforce_api_client()
        tasks.create_task(self._subscribe_to_new_cases())
        tasks.create_task(self._subscribe_to_case_assignee_changed())

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

    async def handle_new_case(self, case: CaseData):
        if not SALESFORCE_CASE_CHANNEL:
            logfire.warn(
                "A new case was created, but no Slack channel configured",
                extra={"case": case.model_dump_json()},
            )
            return

        full_case_data = self._salesforce_client.Case.get(case.Id)
        case = case.model_copy(
            update={"Description": full_case_data.get("Description")}
        )
        logfire.info("New case", extra={"case": case.model_dump_json()})

        case = CaseData(
            Description="""We get this error when trying to query data that has been transitioned into S3
original: error: access id or access key is missing
at Parser.parseErrorMessage (/var/task/node_modules/pg-protocol/src/parser.ts:369:69)
at Parser.handlePacket (/var/task/node_modules/pg-protocol/src/parser.ts:187:21)
at Parser.parse (/var/task/node_modules/pg-protocol/src/parser.ts:102:30)
at Socket.<anonymous> (/var/task/node_modules/pg-protocol/src/index.ts:7:48)
at Socket.emit (node:events:524:28)
at Socket.runInContextCb (/opt/nodejs/node_modules/newrelic/lib/shim/shim.js:1251:20)
at AsyncLocalStorage.run (node:async_hooks:346:14)
at AsyncLocalContextManager.runInContext (/opt/nodejs/node_modules/newrelic/lib/context-manager/async-local-context-manager.js:60:38)
at Socket.wrapped (/opt/nodejs/node_modules/newrelic/lib/transaction/tracer/index.js:273:37)
at Shim.applyContext (/opt/nodejs/node_modules/newrelic/lib/shim/shim.js:1254:66) {

Another line in the error log suggests its something to do with s3 configuration:
routine: 'timescaledb_osm::storage::s3_fetcher::S3Client::setup_s3_client::{{closure}}',""",
            Subject="Unable to query data from tiered storage",
            Id="500Nv00000aG5v7IAC",
            CaseNumber="00037039",
        )

        await insert_event(
            pool=self._pool, event=SalesforceNewCaseEvent(case=case).model_dump()
        )

        await self._trigger.put(True)

    async def handle_updated_case_assignee(self, case: CaseData):
        logfire.info("Case assignee changed", extra={"case": case.model_dump()})

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
