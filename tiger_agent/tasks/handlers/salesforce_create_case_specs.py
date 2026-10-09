from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import CaseData, SalesforceCreateNewCaseEvent
from tiger_agent.tasks.handlers import salesforce_create_case as handler_module
from tiger_agent.tasks.handlers.salesforce_create_case import (
    SalesforceCreateCaseHandler,
)
from tiger_agent.tasks.types import Task

NEW_CASE = CaseData(Id="500Nv00000ABCDE", CaseNumber="00012345")


def _make_task() -> Task:
    return Task(
        id=1,
        event_ts=datetime(2026, 1, 1, tzinfo=UTC),
        attempts=0,
        vt=datetime(2026, 1, 1, tzinfo=UTC),
        claimed=[],
        event=SalesforceCreateNewCaseEvent(
            subject="Cannot connect",
            description="Details",
            user="U_REQUESTER",
            channel="C_CHAN",
        ),
    )


@pytest.fixture
def hctx():
    hctx = MagicMock()
    hctx.pool = MagicMock()
    hctx.salesforce_client = MagicMock()
    return hctx


@pytest.fixture(autouse=True)
def collaborators(monkeypatch):
    stubs = {
        "get_salesforce_account_id_for_channel": AsyncMock(
            return_value="0011x00000ACCNT"
        ),
        "create_case": MagicMock(return_value=NEW_CASE),
        "create_slack_thread_for_case": AsyncMock(),
        "get_handle_link": MagicMock(return_value="<@U_REQUESTER>"),
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(handler_module, name, stub)
    return stubs


class TestSalesforceCreateCaseHandler:
    async def test_posts_a_thread_for_the_created_case(self, hctx, collaborators):
        handler = SalesforceCreateCaseHandler(hctx=hctx, agent=MagicMock())

        await handler.handle(_make_task())

        create_kwargs = collaborators["create_case"].call_args.kwargs
        assert create_kwargs["account_id"] == "0011x00000ACCNT"
        assert create_kwargs["origin"] == "Slack"
        collaborators["create_slack_thread_for_case"].assert_awaited_once_with(
            hctx=hctx,
            case=NEW_CASE,
            channel="C_CHAN",
            submitter="<@U_REQUESTER>",
        )

    async def test_does_not_post_when_case_creation_fails(self, hctx, collaborators):
        collaborators["create_case"].return_value = None
        handler = SalesforceCreateCaseHandler(hctx=hctx, agent=MagicMock())

        await handler.handle(_make_task())

        collaborators["create_slack_thread_for_case"].assert_not_awaited()

    async def test_skips_when_the_channel_has_no_account(self, hctx, collaborators):
        collaborators["get_salesforce_account_id_for_channel"].return_value = None
        handler = SalesforceCreateCaseHandler(hctx=hctx, agent=MagicMock())

        await handler.handle(_make_task())

        collaborators["create_case"].assert_not_called()
        collaborators["create_slack_thread_for_case"].assert_not_awaited()
