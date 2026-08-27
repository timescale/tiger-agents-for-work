import logfire

from tiger_agent.db.utils import insert_event
from tiger_agent.salesforce.constants import SALESFORCE_CASE_CHANNEL
from tiger_agent.salesforce.types import SalesforceAssignmentChangedEvent
from tiger_agent.slack.slash_commands.base import CommandContext


async def handle(ctx: CommandContext, args: list[str]) -> str:
    _case_id = args[0]
    salesforce_client = ctx.hctx.salesforce_client
    if not salesforce_client:
        return "Salesforce not configured"

    if not SALESFORCE_CASE_CHANNEL:
        return "Salesforce thread channel not configured"

    case = salesforce_client.Case.get(_case_id)

    if not case:
        return "Could not find case"

    logfire.info("Manually added request to generate Salesforce Slack notification")
    await insert_event(
        pool=ctx.hctx.pool,
        event=SalesforceAssignmentChangedEvent(
            case=case,
            update_link_to_thread=False,  # do not update the link on the case
        ).model_dump(),
    )

    await ctx.hctx.trigger.put(True)

    return f"The Slack message will be sent to channel <#{SALESFORCE_CASE_CHANNEL}>"
