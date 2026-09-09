from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.types import SlackRequestNewCaseFormEvent
from tiger_agent.tasks.handlers import slack_send_new_case_form as handler_module
from tiger_agent.tasks.handlers.slack_send_new_case_form import (
    SlackSendNewCaseFormHandler,
)
from tiger_agent.tasks.types import Task


def _make_task() -> Task:
    return Task(
        id=1,
        event_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempts=0,
        vt=datetime(2026, 1, 1, tzinfo=timezone.utc),
        claimed=[],
        event=SlackRequestNewCaseFormEvent(
            user="U_REQUESTER",
            channel="C_CHAN",
            trigger_message_ts="1700000000.000100",
        ),
    )


@pytest.fixture
def hctx():
    hctx = MagicMock()
    hctx.app.client = MagicMock()
    hctx.pool = MagicMock()
    hctx.salesforce_client = MagicMock()
    return hctx


@pytest.fixture(autouse=True)
def patched_collaborators(monkeypatch):
    send_form = AsyncMock()
    add_reaction = AsyncMock()
    monkeypatch.setattr(
        handler_module, "send_new_salesforce_case_workflow_form", send_form
    )
    monkeypatch.setattr(handler_module, "add_reaction", add_reaction)
    return {"send_form": send_form, "add_reaction": add_reaction}


class TestSlackSendNewCaseFormHandler:
    async def test_sends_form_with_event_fields(self, hctx, patched_collaborators):
        handler = SlackSendNewCaseFormHandler(hctx=hctx, agent=MagicMock())
        await handler.handle(_make_task())
        patched_collaborators["send_form"].assert_awaited_once_with(
            slack_client=hctx.app.client,
            salesforce_client=hctx.salesforce_client,
            channel="C_CHAN",
            user="U_REQUESTER",
            pool=hctx.pool,
        )

    async def test_adds_white_check_mark_reaction_to_trigger_message(
        self, hctx, patched_collaborators
    ):
        handler = SlackSendNewCaseFormHandler(hctx=hctx, agent=MagicMock())
        await handler.handle(_make_task())
        patched_collaborators["add_reaction"].assert_awaited_once_with(
            hctx.app.client, "C_CHAN", "1700000000.000100", "white_check_mark"
        )
