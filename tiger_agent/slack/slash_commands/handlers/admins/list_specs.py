from unittest.mock import MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.admins.list import handle


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestAdminsListHandler:
    async def test_returns_empty_message_when_no_admins(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = []
        result = await handle(ctx, [])
        assert result == "No admin users are currently configured."

    async def test_lists_admins_with_slack_mentions(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = [("U111",), ("U222",)]
        result = await handle(ctx, [])
        assert result == "Current admin users (2):\n<@U111>\n<@U222>"

    async def test_queries_admin_users_table(self, ctx):
        await handle(ctx, [])
        sql, *_ = ctx.hctx.pool._cursor.execute.call_args.args
        assert "agent.admin_users" in sql
