from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.utils import get_channel_link


async def handle(ctx: CommandContext, _: list[str]) -> str:
    async with (
        ctx.hctx.pool.connection() as con,
        con.cursor() as cur,
    ):
        await cur.execute(
            "select channel_id from agent.limited_channels where enabled order by updated_at"
        )
        rows = await cur.fetchall()

        if not rows:
            return "No channels are currently limited."

        channel_list = [get_channel_link(row[0]) for row in rows]
        return f"Currently limited channels ({len(channel_list)}):\n" + "\n".join(
            channel_list
        )
