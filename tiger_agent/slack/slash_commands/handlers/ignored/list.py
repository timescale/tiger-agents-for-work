from tiger_agent.slack.slash_commands.base import CommandContext


async def handle(ctx: CommandContext, _: list[str]) -> str:
    async with (
        ctx.hctx.pool.connection() as con,
        con.cursor() as cur,
    ):
        await cur.execute("select * from agent.ignored_users")
        rows = await cur.fetchall()

        if not rows:
            return "No users are currently ignored."

        user_list = []
        for row in rows:
            user_id = row[0]
            user_list.append(f"<@{user_id}>")

        return f"Currently ignored users ({len(user_list)}):\n" + "\n".join(user_list)
