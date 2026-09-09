from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import ServiceRecord
from tiger_agent.tasks.handlers import utils as utils_module
from tiger_agent.tasks.handlers.utils import send_new_salesforce_case_workflow_form


@pytest.fixture
def slack_client(make_async_web_client_mock):
    return make_async_web_client_mock()


@pytest.fixture
def salesforce_client():
    return MagicMock()


@pytest.fixture
def pool(make_pool_mock):
    return make_pool_mock()


@pytest.fixture
def patch_lookups(monkeypatch):
    """Patch the two lookups so tests can drive the account + services values."""
    get_account = AsyncMock(return_value="0011x00000ABCDE")
    get_services = MagicMock(return_value=[])
    monkeypatch.setattr(
        utils_module, "get_salesforce_account_id_for_channel", get_account
    )
    monkeypatch.setattr(utils_module, "get_services_for_account", get_services)
    return {"get_account": get_account, "get_services": get_services}


class TestSendNewSalesforceCaseWorkflowForm:
    async def test_returns_early_when_salesforce_client_missing(
        self, slack_client, pool, patch_lookups
    ):
        await send_new_salesforce_case_workflow_form(
            slack_client=slack_client,
            salesforce_client=None,
            pool=pool,
            channel="C_CHAN",
            user="U_USER",
        )
        slack_client.chat_postEphemeral.assert_not_awaited()
        patch_lookups["get_account"].assert_not_awaited()

    async def test_raises_when_user_is_missing(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        with pytest.raises(Exception, match="no user is associated"):
            await send_new_salesforce_case_workflow_form(
                slack_client=slack_client,
                salesforce_client=salesforce_client,
                pool=pool,
                channel="C_CHAN",
                user=None,
            )
        slack_client.chat_postEphemeral.assert_not_awaited()

    async def test_raises_when_channel_is_not_linked_to_salesforce_account(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        patch_lookups["get_account"].return_value = None
        with pytest.raises(Exception, match="not linked to a Salesforce account"):
            await send_new_salesforce_case_workflow_form(
                slack_client=slack_client,
                salesforce_client=salesforce_client,
                pool=pool,
                channel="C_CHAN",
                user="U_USER",
            )
        slack_client.chat_postEphemeral.assert_not_awaited()

    async def test_posts_ephemeral_to_requesting_user_in_channel(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        await send_new_salesforce_case_workflow_form(
            slack_client=slack_client,
            salesforce_client=salesforce_client,
            pool=pool,
            channel="C_CHAN",
            user="U_USER",
        )
        slack_client.chat_postEphemeral.assert_awaited_once()
        kwargs = slack_client.chat_postEphemeral.await_args.kwargs
        assert kwargs["channel"] == "C_CHAN"
        assert kwargs["user"] == "U_USER"

    async def test_form_omits_service_block_when_account_has_no_services(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        patch_lookups["get_services"].return_value = []
        await send_new_salesforce_case_workflow_form(
            slack_client=slack_client,
            salesforce_client=salesforce_client,
            pool=pool,
            channel="C_CHAN",
            user="U_USER",
        )
        blocks = slack_client.chat_postEphemeral.await_args.kwargs["blocks"]
        block_ids = {b.get("block_id") for b in blocks if "block_id" in b}
        assert "service_block" not in block_ids

    async def test_form_includes_project_and_service_options_when_services_exist(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        patch_lookups["get_services"].return_value = [
            ServiceRecord(service_id="svc-1", project_id="proj-a"),
            ServiceRecord(service_id="svc-2", project_id="proj-a"),
            ServiceRecord(service_id="svc-3", project_id="proj-b"),
        ]
        await send_new_salesforce_case_workflow_form(
            slack_client=slack_client,
            salesforce_client=salesforce_client,
            pool=pool,
            channel="C_CHAN",
            user="U_USER",
        )
        blocks = slack_client.chat_postEphemeral.await_args.kwargs["blocks"]
        service_block = next(b for b in blocks if b.get("block_id") == "service_block")
        option_values = [opt["value"] for opt in service_block["element"]["options"]]
        # Each unique project appears once as a project-level option, plus one
        # option per service (project|service).
        assert "proj-a" in option_values
        assert "proj-b" in option_values
        assert "proj-a|svc-1" in option_values
        assert "proj-a|svc-2" in option_values
        assert "proj-b|svc-3" in option_values

    async def test_looks_up_account_and_services_for_the_given_channel(
        self, slack_client, salesforce_client, pool, patch_lookups
    ):
        await send_new_salesforce_case_workflow_form(
            slack_client=slack_client,
            salesforce_client=salesforce_client,
            pool=pool,
            channel="C_CHAN",
            user="U_USER",
        )
        patch_lookups["get_account"].assert_awaited_once_with(
            pool=pool, channel_id="C_CHAN"
        )
        patch_lookups["get_services"].assert_called_once_with(
            salesforce_client=salesforce_client, account_id="0011x00000ABCDE"
        )
