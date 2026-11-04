from tiger_agent.db.utils import set_channel_limited_access
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.utils import get_channel_link, parse_slack_channel


async def handle(ctx: CommandContext, args: list[str]) -> str:
    channel_id = parse_slack_channel(args[0])
    if channel_id is None:
        return "Argument needs to be a Slack channel"
    await set_channel_limited_access(
        ctx.hctx.pool, channel_id=channel_id, enabled=False
    )
    return f"Removed the access limit on {get_channel_link(channel_id)}"
