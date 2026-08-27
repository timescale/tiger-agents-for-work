from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.salesforce import add_customer_channel


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = MagicMock()
    hctx.app.client.chat_postMessage = AsyncMock()
    hctx.app.client.pins_add = AsyncMock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patch_collaborators(monkeypatch):
    upsert = AsyncMock()
    send_button = AsyncMock(return_value="1700000000.000100")
    monkeypatch.setattr(
        add_customer_channel, "upsert_salesforce_account_id_for_channel", upsert
    )
    monkeypatch.setattr(
        add_customer_channel, "send_new_case_and_feedback_button", send_button
    )
    return {"upsert": upsert, "send_button": send_button}


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

    async def test_pins_button_message_when_ts_returned(self, ctx, patch_collaborators):
        patch_collaborators["send_button"].return_value = "1700000000.000100"
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        ctx.hctx.app.client.pins_add.assert_awaited_once_with(
            channel="C_CHAN", timestamp="1700000000.000100"
        )

    async def test_does_not_pin_when_button_send_returns_none(self, ctx, patch_collaborators):
        patch_collaborators["send_button"].return_value = None
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        ctx.hctx.app.client.pins_add.assert_not_called()

    async def test_falls_back_to_support_bot_when_bot_info_missing(self, ctx):
        ctx.bot_info = None
        await add_customer_channel.handle(ctx, ["C_CHAN", "0011x00000ABCDE"])
        call_kwargs = ctx.hctx.app.client.chat_postMessage.call_args.kwargs
        assert "I'm Support Bot" in call_kwargs["text"]
