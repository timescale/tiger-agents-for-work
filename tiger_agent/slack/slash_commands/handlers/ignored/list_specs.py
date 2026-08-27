from unittest.mock import MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.ignored.list import handle


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestIgnoredListHandler:
    async def test_returns_empty_message_when_no_ignored_users(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = []
        result = await handle(ctx, [])
        assert result == "No users are currently ignored."

    async def test_lists_ignored_users_with_slack_mentions(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = [("U111",), ("U222",)]
        result = await handle(ctx, [])
        assert result == "Currently ignored users (2):\n<@U111>\n<@U222>"

    async def test_queries_ignored_users_table(self, ctx):
        await handle(ctx, [])
        sql, *_ = ctx.hctx.pool._cursor.execute.call_args.args
        assert "agent.ignored_users" in sql
