from unittest.mock import AsyncMock, MagicMock

from slack_sdk.errors import SlackApiError

from tiger_agent.salesforce.types import CaseData, SalesforceUser
from tiger_agent.tasks.handlers.salesforce_assignment_changed import (
    resolve_case_owner_slack_user_id,
)

USER_ID = "005Nv000007cRRNIA2"
QUEUE_ID = "00GNv000002zSPdMAM"


def _case(owner: SalesforceUser | None) -> CaseData:
    return CaseData(Id="500Nv00000mi2s5IAA", CaseNumber="00044897", Owner=owner)


def _slack_error(code: str) -> SlackApiError:
    return SlackApiError(message=code, response={"ok": False, "error": code})


def _lookup_returning(user_id: str) -> AsyncMock:
    return AsyncMock(return_value=MagicMock(data={"ok": True, "user": {"id": user_id}}))


async def test_resolves_a_user_owner_by_username(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=_lookup_returning("U_OWNER")
    )
    owner = SalesforceUser(
        Id=USER_ID, Username="Owner@new.example", Email="owner@old.example"
    )

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) == "U_OWNER"
    client.users_lookupByEmail.assert_awaited_once_with(email="owner@new.example")


async def test_queue_owner_resolves_to_none_without_a_lookup(
    make_async_web_client_mock,
):
    client = make_async_web_client_mock(users_lookupByEmail=AsyncMock())
    owner = SalesforceUser(Id=QUEUE_ID, Username="Customer Care")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None
    client.users_lookupByEmail.assert_not_awaited()


async def test_missing_owner_or_username_resolves_to_none(
    make_async_web_client_mock,
):
    client = make_async_web_client_mock(users_lookupByEmail=AsyncMock())

    assert await resolve_case_owner_slack_user_id(client, _case(None)) is None
    no_username = SalesforceUser(Id=USER_ID, Username=None, Email="owner@new.example")
    assert await resolve_case_owner_slack_user_id(client, _case(no_username)) is None
    client.users_lookupByEmail.assert_not_awaited()


async def test_username_that_is_not_an_address_resolves_to_none(
    make_async_web_client_mock,
):
    client = make_async_web_client_mock(users_lookupByEmail=AsyncMock())
    owner = SalesforceUser(Id=USER_ID, Username="integration.user")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None
    client.users_lookupByEmail.assert_not_awaited()


async def test_unknown_username_resolves_to_none(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=AsyncMock(side_effect=_slack_error("users_not_found"))
    )
    owner = SalesforceUser(Id=USER_ID, Username="contractor@other.example")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None


async def test_lookup_failure_resolves_to_none(make_async_web_client_mock):
    client = make_async_web_client_mock(
        users_lookupByEmail=AsyncMock(side_effect=_slack_error("ratelimited"))
    )
    owner = SalesforceUser(Id=USER_ID, Username="owner@new.example")

    assert await resolve_case_owner_slack_user_id(client, _case(owner)) is None
