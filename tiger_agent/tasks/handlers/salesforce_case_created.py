import logfire

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_ENABLE_SPAM_FILTERING,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import SalesforceCaseCreatedEvent
from tiger_agent.salesforce.utils import (
    add_internal_case_post,
    create_case_url,
    update_case,
)
from tiger_agent.slack.types import SlackMessage
from tiger_agent.slack.utils import post_response, request_feedback
from tiger_agent.tasks.handlers.base import AGENT_USAGE_LIMITS, TaskHandler
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


class SalesforceCaseCreatedHandler(TaskHandler):
    """
    Runs the agent to determine if the case is spam.
    We handle legitimate new cases with the SalesforceAssignmentChangedHandler
    as, at that point we have a assignee and spam should have been filtered out
    So this handler is strictly to detect spam cases
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

    @logfire.instrument("SalesforceCaseCreatedHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceCaseCreatedEvent = task.event

        if not SALESFORCE_ENABLE_SPAM_FILTERING:
            return

        # at this time we only care about filtering email messages
        if task.event.case.Origin.lower() != "email":
            return

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=SALESFORCE_CASE_CHANNEL,
        )

        response = await agent_and_ctx.agent.run(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=AGENT_USAGE_LIMITS,
        )

        if not response.output.is_spam:
            return

        logfire.info(
            "Salesforce case identified as spam",
            extra={"filtering_enabled": SALESFORCE_ENABLE_SPAM_FILTERING},
        )

        original_message = await post_response(
            client=hctx.app.client,
            channel=SALESFORCE_CASE_CHANNEL,
            thread_ts=None,
            text=f"*Spam Detected* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{event.case.Subject}_",
        )

        message_to_link_to = SlackMessage(
            channel_id=SALESFORCE_CASE_CHANNEL,
            ts=original_message.data.get("ts"),
            text=response.output.message,
            thread_ts=None,
        )

        if message_to_link_to and SALESFORCE_SLACK_THREAD_FIELD:
            result = await hctx.app.client.chat_getPermalink(
                channel=message_to_link_to.channel_id,
                message_ts=message_to_link_to.ts,
            )
            permalink = result.data.get("permalink")
            update_case(
                hctx.salesforce_client,
                event.case.Id,
                {SALESFORCE_SLACK_THREAD_FIELD: permalink},
            )

            logfire.info(
                "Updated Salesforce case to include the thread link",
                extra={"permalink": permalink},
            )

        add_internal_case_post(
            salesforce_client=hctx.salesforce_client,
            case_id=event.case.Id,
            body=response.output.short_description,
        )
        request_feedback(
            hctx.app.client,
            channel=message_to_link_to.channel_id,
            thread_ts=message_to_link_to.ts,
        )

        update_case(
            hctx.salesforce_client,
            event.case.Id,
            {"Status": "Spam", "Type": "Spam"},
        )
