from unittest.mock import AsyncMock, MagicMock

from slack_sdk.errors import SlackApiError

from tiger_agent.salesforce.types import CaseData, SalesforceUser
from tiger_agent.tasks.handlers.salesforce_assignment_changed import (
    resolve_case_owner_slack_user_id,
)


def _case(owner: SalesforceUser | None) -> CaseData:
    return CaseData(Id="500Nv00000mi2s5IAA", CaseNumber="00044897", Owner=owner)


def _slack_error(code: str) -> SlackApiError:
    return SlackApiError(message=code, response={"ok": False, "error": code})


async def test_resolves_a_user_owner_by_email(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=AsyncMock(
            return_value=MagicMock(data={"ok": True, "user": {"id": "U_OWNER"}})
        )
    )
    owner = SalesforceUser(Id="005Nv000007cRRNIA2", Email="owner@tigerdata.com")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) == "U_OWNER"
    client.users_lookupByEmail.assert_awaited_once_with(email="owner@tigerdata.com")


async def test_queue_owner_resolves_to_none_without_a_lookup(
    make_async_web_client_mock,
):
    client = make_async_web_client_mock(users_lookupByEmail=AsyncMock())
    owner = SalesforceUser(Id="00GNv000002zSPdMAM", Email="queue@tigerdata.com")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None
    client.users_lookupByEmail.assert_not_awaited()


async def test_missing_owner_or_email_resolves_to_none(make_async_web_client_mock):
    client = make_async_web_client_mock(users_lookupByEmail=AsyncMock())

    assert await resolve_case_owner_slack_user_id(client, _case(None)) is None
    no_email = SalesforceUser(Id="005Nv000007cRRNIA2", Email=None)
    assert await resolve_case_owner_slack_user_id(client, _case(no_email)) is None
    client.users_lookupByEmail.assert_not_awaited()


async def test_unknown_email_resolves_to_none(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=AsyncMock(side_effect=_slack_error("users_not_found"))
    )
    owner = SalesforceUser(Id="005Nv000007cRRNIA2", Email="customer@example.com")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None


async def test_lookup_failure_resolves_to_none(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=AsyncMock(side_effect=_slack_error("ratelimited"))
    )
    owner = SalesforceUser(Id="005Nv000007cRRNIA2", Email="owner@tigerdata.com")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None
