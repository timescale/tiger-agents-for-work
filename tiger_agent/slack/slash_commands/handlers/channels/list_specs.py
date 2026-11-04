from unittest.mock import MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.channels.list import handle


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestChannelsListHandler:
    async def test_reports_when_no_channels_are_limited(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = []
        result = await handle(ctx, [])
        assert result == "No channels are currently limited."

    async def test_lists_limited_channels_as_links(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = [("C123",), ("C456",)]
        result = await handle(ctx, [])
        assert result == "Currently limited channels (2):\n<#C123>\n<#C456>"

    async def test_only_selects_enabled_rows(self, ctx):
        ctx.hctx.pool._cursor.fetchall.return_value = []
        await handle(ctx, [])
        (sql,) = ctx.hctx.pool._cursor.execute.call_args.args
        assert "where enabled" in sql
