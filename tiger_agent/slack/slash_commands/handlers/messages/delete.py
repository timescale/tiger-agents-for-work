import logfire

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.utils import parse_slack_url


async def handle(ctx: CommandContext, args: list[str]) -> str:
    url_parts = parse_slack_url(args[0])

    if (not url_parts.channel_id) or (not url_parts.ts):
        raise Exception("Not a valid Slack url")

    try:
        await ctx.hctx.app.client.chat_delete(
            channel=url_parts.channel_id, ts=url_parts.ts, as_user=True
        )
    except Exception:
        logfire.exception("Could not delete message", extra={"message_url": args[0]})
        return f"Failed to delete message: {args[0]}"
    logfire.info("Deleted agent message", extra={"message": url_parts})
    return f"Deleted message: {args[0]}"
