from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.utils import parse_slack_user_name
from tiger_agent.utils import serialize_to_jsonb


async def handle(ctx: CommandContext, args: list[str]) -> str:
    username, user_id = parse_slack_user_name(args[0])
    if username is None or user_id is None:
        return "Argument needs to be a Slack username"
    async with (
        ctx.hctx.pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute(
            "select agent.delete_ignored_user(%s)", (serialize_to_jsonb(ctx.command),)
        )
    return f"Unignored <{username}>"
