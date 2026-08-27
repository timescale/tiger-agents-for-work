from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.command import Command


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    return CommandContext(
        hctx=MagicMock(), command=make_slack_command(), bot_info=make_bot_info()
    )


class TestCommand:
    async def test_calls_func_when_arg_count_matches(self, ctx):
        func = AsyncMock(return_value="ok")
        cmd = Command(key="do", expected_parameters=2, func=func)

        result = await cmd(["a", "b"], ctx)

        assert result == "ok"
        func.assert_awaited_once_with(ctx, ["a", "b"])

    async def test_returns_error_when_arg_count_is_too_low(self, ctx):
        func = AsyncMock()
        cmd = Command(key="do", expected_parameters=2, func=func)

        result = await cmd(["a"], ctx)

        assert result == "Incorrect number of parameters given for <do>"
        func.assert_not_called()

    async def test_returns_error_when_arg_count_is_too_high(self, ctx):
        func = AsyncMock()
        cmd = Command(key="do", expected_parameters=1, func=func)

        result = await cmd(["a", "b"], ctx)

        assert result == "Incorrect number of parameters given for <do>"
        func.assert_not_called()

    async def test_accepts_string_input_and_tokenizes_it(self, ctx):
        func = AsyncMock(return_value="ok")
        cmd = Command(key="do", expected_parameters=2, func=func)

        result = await cmd("a b", ctx)

        assert result == "ok"
        func.assert_awaited_once_with(ctx, ["a", "b"])

    async def test_keeps_angle_bracket_tokens_intact_when_tokenizing(self, ctx):
        func = AsyncMock(return_value="ok")
        cmd = Command(key="do", expected_parameters=1, func=func)

        await cmd("<@U123|nathan>", ctx)

        func.assert_awaited_once_with(ctx, ["<@U123|nathan>"])
