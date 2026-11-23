from asyncio import Queue
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.listeners import slack as slack_listener_module
from tiger_agent.listeners.slack import SlackListener
from tiger_agent.salesforce.constants import DEFAULT_NEW_CASE_SEVERITY
from tiger_agent.tasks.handlers.types import NewSalesforceCaseFormSubmission
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


class TestHandleNewSalesforceCaseWorkflowFormSubmit:
    @pytest.fixture
    def body(self):
        return {"user": {"id": "U_USER"}, "channel": {"id": "C_CHAN"}}

    async def test_defaults_severity_when_no_cloud_impact(
        self, listener, patch_insert_event, monkeypatch, body
    ):
        monkeypatch.setattr(
            slack_listener_module,
            "parse_new_case_form_data",
            lambda body: NewSalesforceCaseFormSubmission(
                subject="Cannot connect",
                description="Details",
                service="proj-a|svc-1",
                customer_impact=None,
            ),
        )

        await listener._handle_new_salesforce_case_workflow_form_submit(
            ack=AsyncMock(), body=body, respond=AsyncMock()
        )

        patch_insert_event.assert_awaited_once()
        payload = patch_insert_event.await_args.args[1]
        assert payload["cloud_impact"] is None
        assert payload["severity"] == DEFAULT_NEW_CASE_SEVERITY

    async def test_omits_severity_when_cloud_impact_provided(
        self, listener, patch_insert_event, monkeypatch, body
    ):
        monkeypatch.setattr(
            slack_listener_module,
            "parse_new_case_form_data",
            lambda body: NewSalesforceCaseFormSubmission(
                subject="Cannot connect",
                description="Details",
                service="proj-a|svc-1",
                customer_impact="High",
            ),
        )

        await listener._handle_new_salesforce_case_workflow_form_submit(
            ack=AsyncMock(), body=body, respond=AsyncMock()
        )

        patch_insert_event.assert_awaited_once()
        payload = patch_insert_event.await_args.args[1]
        assert payload["cloud_impact"] == "High"
        assert payload["severity"] is None


class TestOnMessageDeleted:
    """A deleted message stops the run answering it and drops queued copies."""

    async def test_message_deleted_cancels_and_drops_queued_tasks(
        self, listener, hctx, monkeypatch, patch_insert_event
    ):
        delete_unclaimed = AsyncMock(return_value=1)
        monkeypatch.setattr(
            slack_listener_module, "delete_unclaimed_slack_events", delete_unclaimed
        )
        with hctx.cancellations.track("C_CHAN", "1700000000.000100") as run:
            event = {
                "type": "message",
                "subtype": "message_deleted",
                "hidden": True,
                "channel": "C_CHAN",
                "ts": "1700000000.000500",
                "deleted_ts": "1700000000.000100",
            }
            await listener._on_message(ack=AsyncMock(), event=event)

            assert run.cancel_requested.is_set()
            assert run.reason == "message_deleted"
        delete_unclaimed.assert_awaited_once_with(
            hctx.pool, channel="C_CHAN", ts="1700000000.000100"
        )
        # a deletion is not a new task
        patch_insert_event.assert_not_awaited()
        assert hctx.trigger.qsize() == 0

    async def test_message_deleted_for_an_unknown_message_is_quiet(
        self, listener, hctx, monkeypatch
    ):
        delete_unclaimed = AsyncMock(return_value=0)
        monkeypatch.setattr(
            slack_listener_module, "delete_unclaimed_slack_events", delete_unclaimed
        )
        event = {
            "type": "message",
            "subtype": "message_deleted",
            "channel": "C_CHAN",
            "ts": "1700000000.000500",
            "deleted_ts": "1700000000.000999",
        }

        await listener._on_message(ack=AsyncMock(), event=event)

        delete_unclaimed.assert_awaited_once()


class TestAgentSessionStopped:
    async def test_sessions_stop_is_persisted_to_database_as_utc(self, listener, hctx, monkeypatch):
        from datetime import UTC, datetime

        record_cancel = AsyncMock()
        monkeypatch.setattr(
            slack_listener_module, "record_session_cancel", record_cancel
        )
        event = {
            "type": "agent_session_stopped",
            "channel": "C_CHAN",
            "event_ts": "1700000000.000100",
            "thread_ts": "1700000000.000050",
            "user": "U_USER",
        }
        await listener._on_slack_cancel_agent(ack=AsyncMock(), event=event)

        record_cancel.assert_awaited_once()
        kwargs = record_cancel.await_args.kwargs
        assert kwargs["channel_id"] == "C_CHAN"
        assert kwargs["thread_ts"] == "1700000000.000050"
        assert kwargs["user_id"] == "U_USER"
        assert kwargs["event_ts"] == datetime.fromtimestamp(1700000000.000100, tz=UTC)
        assert kwargs["event_ts"].tzinfo is UTC

    async def test_missing_ids_are_quietly_skipped(self, listener, hctx, monkeypatch):
        record_cancel = AsyncMock()
        monkeypatch.setattr(
            slack_listener_module, "record_session_cancel", record_cancel
        )
        await listener._on_slack_cancel_agent(
            ack=AsyncMock(), event={"channel": "C_CHAN"}
        )
        record_cancel.assert_not_awaited()

    async def test_in_memory_runs_are_cancelled_and_session_reset(
        self, listener, hctx, monkeypatch
    ):
        """The listener calls cancel_for_user with the stop event timestamp,
        which cancels tracked runs in memory and resets the session when no
        local runs remain."""
        record_cancel = AsyncMock()
        monkeypatch.setattr(
            slack_listener_module, "record_session_cancel", record_cancel
        )
        set_status_mock = AsyncMock()
        monkeypatch.setattr(
            slack_listener_module, "set_status", set_status_mock
        )
        with hctx.cancellations.track(
            "C_CHAN", "1700000000.000100", user="U_USER", thread_ts="1700000000.000050"
        ) as run:
            event = {
                "type": "agent_session_stopped",
                "channel": "C_CHAN",
                "event_ts": "1700000000.000500",
                "thread_ts": "1700000000.000050",
                "user": "U_USER",
            }
            await listener._on_slack_cancel_agent(ack=AsyncMock(), event=event)

            assert run.cancel_requested.is_set()
            assert run.reason == "user_request"
        # No local runs remain after the tracked run exits, so session resets
        assert set_status_mock.await_args.kwargs["is_busy"] is False
