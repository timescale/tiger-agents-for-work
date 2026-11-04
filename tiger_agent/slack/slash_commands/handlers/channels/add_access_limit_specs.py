from unittest.mock import MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.channels.add_access_limit import handle


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestChannelsAddAccessLimitHandler:
    async def test_returns_error_when_argument_is_not_a_channel(self, ctx):
        result = await handle(ctx, ["not-a-channel"])
        assert result == "Argument needs to be a Slack channel"

    async def test_does_not_hit_database_when_argument_is_invalid(self, ctx):
        await handle(ctx, ["not-a-channel"])
        ctx.hctx.pool._connection.execute.assert_not_called()

    async def test_returns_confirmation_with_channel_link_on_success(self, ctx):
        result = await handle(ctx, ["<#C123|support>"])
        assert result == "Limited <#C123> to public MCP servers and tools"

    async def test_enables_the_limit_for_the_channel(self, ctx):
        await handle(ctx, ["<#C123|support>"])
        sql, params = ctx.hctx.pool._connection.execute.call_args.args
        assert "agent.limited_channels" in sql
        assert params == ("C123", True)
