from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.salesforce import add_customer_channel


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = MagicMock()
    hctx.app.client.chat_postMessage = AsyncMock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patch_collaborators(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(
        add_customer_channel, "upsert_salesforce_account_id_for_channel", upsert
    )
    return {"upsert": upsert}


class TestSalesforceAddCustomerChannelHandler:
    async def test_returns_confirmation_message(self, ctx):
        result = await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        assert result == "Assigned channel C_CHAN to Salesforce account id 0011x00000ABCDE"

    async def test_upserts_link_between_channel_and_account(self, ctx, patch_collaborators):
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        patch_collaborators["upsert"].assert_awaited_once_with(
            ctx.hctx.pool,
            channel_id="C_CHAN",
            salesforce_account_id="0011x00000ABCDE",
        )

    async def test_posts_greeting_referencing_bot_name(self, ctx, make_bot_info):
        ctx.bot_info = make_bot_info(name="eon")
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        call_kwargs = ctx.hctx.app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C_CHAN"
        assert "I'm eon" in call_kwargs["text"]
        assert "<@U_BOT>" in call_kwargs["text"]

    async def test_falls_back_to_support_bot_when_bot_info_missing(self, ctx):
        ctx.bot_info = None
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        call_kwargs = ctx.hctx.app.client.chat_postMessage.call_args.kwargs
        assert "I'm Support Bot" in call_kwargs["text"]
