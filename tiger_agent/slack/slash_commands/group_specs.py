from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.command import Command
from tiger_agent.slack.slash_commands.group import CommandGroup


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    return CommandContext(
        hctx=MagicMock(), command=make_slack_command(), bot_info=make_bot_info()
    )


class TestCommandGroupRouting:
    async def test_dispatches_to_matching_leaf_command(self, ctx):
        add_func = AsyncMock(return_value="added")
        group = CommandGroup(
            commands=[Command(key="add", expected_parameters=1, func=add_func)]
        )

        result = await group("add foo", ctx)

        assert result == "added"
        add_func.assert_awaited_once_with(ctx, ["foo"])

    async def test_recurses_into_nested_group(self, ctx):
        add_func = AsyncMock(return_value="added")
        group = CommandGroup(
            commands=[
                CommandGroup(
                    key="users",
                    commands=[
                        Command(key="add", expected_parameters=1, func=add_func),
                    ],
                )
            ]
        )

        result = await group("users add foo", ctx)

        assert result == "added"
        add_func.assert_awaited_once_with(ctx, ["foo"])

    async def test_returns_help_text_when_no_command_matches(self, ctx):
        group = CommandGroup(
            commands=[
                Command(key="add", func=AsyncMock()),
                Command(key="remove", func=AsyncMock()),
            ]
        )

        result = await group("unknown", ctx)

        assert "<unknown> is an invalid command" in result
        assert "Available commands:" in result
        assert "add" in result
        assert "remove" in result

    async def test_returns_help_text_when_no_input_provided(self, ctx):
        group = CommandGroup(
            commands=[Command(key="add", func=AsyncMock())]
        )

        result = await group("", ctx)

        # no "invalid command" preamble when input is empty
        assert "is an invalid command" not in result
        assert "Available commands:" in result

    async def test_help_text_shows_nested_group_subcommands_indented(self, ctx):
        group = CommandGroup(
            commands=[
                CommandGroup(
                    key="users",
                    commands=[
                        Command(key="add", func=AsyncMock()),
                        Command(key="list", func=AsyncMock()),
                    ],
                )
            ]
        )

        result = await group("bogus", ctx)

        assert "users add" in result
        assert "users list" in result

    async def test_uses_name_when_provided_in_help_text(self, ctx):
        group = CommandGroup(
            commands=[
                Command(key="add", name="Add User", func=AsyncMock()),
            ]
        )

        result = await group("bogus", ctx)

        assert "Add User" in result
