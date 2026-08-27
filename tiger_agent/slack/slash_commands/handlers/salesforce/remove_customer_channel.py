from tiger_agent.db.utils import remove_salesforce_account_id_for_channel
from tiger_agent.slack.slash_commands.base import CommandContext


async def handle(ctx: CommandContext, args: list[str]) -> str:
    [channel_id] = args
    await remove_salesforce_account_id_for_channel(
        ctx.hctx.pool,
        channel_id=channel_id,
    )

    return f"Removed Salesforce channel {channel_id}"
