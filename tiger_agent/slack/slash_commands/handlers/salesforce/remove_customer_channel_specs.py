from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.salesforce import remove_customer_channel


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = MagicMock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patch_remove(monkeypatch):
    fn = AsyncMock()
    monkeypatch.setattr(
        remove_customer_channel, "remove_salesforce_account_id_for_channel", fn
    )
    return fn


class TestSalesforceRemoveCustomerChannelHandler:
    async def test_returns_confirmation_message(self, ctx):
        result = await remove_customer_channel.handle(ctx, ["C_CHAN"])
        assert result == "Removed Salesforce channel C_CHAN"

    async def test_calls_removal_function_with_channel_id(self, ctx, patch_remove):
        await remove_customer_channel.handle(ctx, ["C_CHAN"])
        patch_remove.assert_awaited_once_with(ctx.hctx.pool, channel_id="C_CHAN")
