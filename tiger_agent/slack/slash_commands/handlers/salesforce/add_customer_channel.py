from tiger_agent.db.utils import upsert_salesforce_account_id_for_channel
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.utils import (
    get_handle_link,
)


async def handle(ctx: CommandContext, args: list[str]) -> str:
    [channel_id, salesforce_account_id] = args
    await upsert_salesforce_account_id_for_channel(
        ctx.hctx.pool,
        channel_id=channel_id,
        salesforce_account_id=salesforce_account_id,
    )
    bot_name = ctx.bot_info.name if ctx.bot_info else "Support Bot"
    bot_user_id = ctx.bot_info.user_id if ctx.bot_info else None
    bot_mention = get_handle_link(bot_user_id) if bot_user_id else bot_name

    await ctx.hctx.app.client.chat_postMessage(
        channel=channel_id,
        text=(
            f"Hi there! I'm {bot_name}. I'm here to help — you can get assistance by "
            f"@mentioning {bot_mention} in this channel. If you need to open a support ticket, "
            f"you can just ask {bot_mention} to help you create one."
        ),
    )
    return f"Assigned channel {channel_id} to Salesforce account id {salesforce_account_id}"
