from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.messages.delete import handle

SLACK_URL = "https://tigerdata.slack.com/archives/C_CHAN/p1700000000000100"


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.app.client.chat_delete = AsyncMock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


class TestMessagesDeleteHandler:
    async def test_returns_confirmation_on_success(self, ctx):
        result = await handle(ctx, [SLACK_URL])
        assert result == f"Deleted message: {SLACK_URL}"

    async def test_calls_chat_delete_with_parsed_channel_and_ts(self, ctx):
        await handle(ctx, [SLACK_URL])
        ctx.hctx.app.client.chat_delete.assert_awaited_once_with(
            channel="C_CHAN", ts="1700000000.000100", as_user=True
        )

    async def test_returns_failure_message_when_chat_delete_raises(self, ctx):
        ctx.hctx.app.client.chat_delete.side_effect = Exception("slack api down")
        result = await handle(ctx, [SLACK_URL])
        assert result == f"Failed to delete message: {SLACK_URL}"

    async def test_raises_on_unparseable_slack_url(self, ctx):
        with pytest.raises(ValueError, match="Could not parse Slack URL"):
            await handle(ctx, ["not-a-slack-url"])
