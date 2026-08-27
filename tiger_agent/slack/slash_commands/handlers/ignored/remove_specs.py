from unittest.mock import MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.ignored.remove import handle


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestIgnoredRemoveHandler:
    async def test_returns_error_when_argument_is_not_a_slack_mention(self, ctx):
        result = await handle(ctx, ["not-a-mention"])
        assert result == "Argument needs to be a Slack username"

    async def test_does_not_hit_database_when_argument_is_invalid(self, ctx):
        await handle(ctx, ["not-a-mention"])
        ctx.hctx.pool._cursor.execute.assert_not_called()

    async def test_returns_confirmation_with_username_on_success(self, ctx):
        result = await handle(ctx, ["<@U123|nathan>"])
        assert result == "Unignored <nathan>"

    async def test_calls_delete_ignored_user_sql_function(self, ctx):
        await handle(ctx, ["<@U123|nathan>"])
        sql, _ = ctx.hctx.pool._cursor.execute.call_args.args
        assert "delete_ignored_user" in sql
