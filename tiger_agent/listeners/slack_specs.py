from asyncio import Queue
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.listeners import slack as slack_listener_module
from tiger_agent.listeners.slack import SlackListener
from tiger_agent.types import HarnessContext


@pytest.fixture
def hctx(make_bot_info) -> HarnessContext:
    return HarnessContext(
        app=MagicMock(),
        pool=MagicMock(),
        trigger=Queue(),
        bot_info=make_bot_info(),
        num_workers=1,
    )


@pytest.fixture
def listener(hctx):
    return SlackListener(hctx=hctx, task_processor=MagicMock())


@pytest.fixture(autouse=True)
def patch_insert_event(monkeypatch):
    """Patch the module-level insert_event import so we don't hit the pool."""
    stub = AsyncMock()
    monkeypatch.setattr(slack_listener_module, "insert_event", stub)
    return stub


class TestOnMessageBotMessagePseudoSlashCommand:
    """The bot_message branch translates a workflow-posted pseudo-slash-command
    (`/support-case-form <@U…>`) into a SlackRequestNewCaseForm event.
    """

    async def test_matching_pseudo_command_enqueues_request_form_event(
        self, listener, patch_insert_event
    ):
        event = {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C_CHAN",
            "channel_type": "channel",
            "ts": "1700000000.000100",
            "text": "/support-case-form <@U086NRW4PEK>",
        }
        await listener._on_message(ack=AsyncMock(), event=event)

        patch_insert_event.assert_awaited_once()
        payload = patch_insert_event.await_args.args[1]
        assert payload["type"] == "request_new_case_form"
        assert payload["user"] == "U086NRW4PEK"
        assert payload["channel"] == "C_CHAN"
        assert payload["trigger_message_ts"] == "1700000000.000100"

    async def test_matching_pseudo_command_triggers_task_worker(self, listener, hctx):
        event = {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C_CHAN",
            "channel_type": "channel",
            "ts": "1700000000.000100",
            "text": "/support-case-form <@U086NRW4PEK>",
        }
        await listener._on_message(ack=AsyncMock(), event=event)
        assert hctx.trigger.qsize() == 1
        assert hctx.trigger.get_nowait() is True

    async def test_bot_message_without_pseudo_command_does_not_enqueue(
        self, listener, patch_insert_event, hctx
    ):
        event = {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C_CHAN",
            "channel_type": "channel",
            "text": "hello from a workflow",
        }
        await listener._on_message(ack=AsyncMock(), event=event)
        patch_insert_event.assert_not_awaited()
        assert hctx.trigger.qsize() == 0

    async def test_pseudo_command_with_wrong_prefix_does_not_enqueue(
        self, listener, patch_insert_event
    ):
        event = {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C_CHAN",
            "channel_type": "channel",
            "text": "/some-other-command <@U086NRW4PEK>",
        }
        await listener._on_message(ack=AsyncMock(), event=event)
        patch_insert_event.assert_not_awaited()


class TestOnMessageEarlyReturns:
    async def test_ignores_messages_sent_by_the_bot_itself(
        self, listener, patch_insert_event, hctx
    ):
        event = {
            "type": "message",
            "user": hctx.bot_info.user_id,
            "channel": "C_CHAN",
            "channel_type": "channel",
            "text": "anything",
        }
        await listener._on_message(ack=AsyncMock(), event=event)
        patch_insert_event.assert_not_awaited()
        assert hctx.trigger.qsize() == 0

    async def test_ignores_userless_events_that_are_not_bot_messages(
        self, listener, patch_insert_event, hctx
    ):
        # e.g. message_changed / message_deleted arrive with no `user`
        event = {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C_CHAN",
            "channel_type": "channel",
            "text": "edited",
        }
        await listener._on_message(ack=AsyncMock(), event=event)
        patch_insert_event.assert_not_awaited()
        assert hctx.trigger.qsize() == 0
