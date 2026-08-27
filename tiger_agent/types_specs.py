from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tiger_agent.types import HarnessContext


@pytest.fixture
def stub_external_deps(monkeypatch, make_bot_info):
    """Stub the four boundary calls HarnessContext.create() makes.

    The classmethod does local imports inside its body to avoid a circular
    dependency, so we patch at the module of origin (not the import site).
    """
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")

    fake_app = MagicMock()
    fake_app.client = MagicMock()

    fake_pool = MagicMock()
    fake_salesforce_client = MagicMock()
    fake_bot_info = make_bot_info()

    with (
        patch("tiger_agent.types.AsyncApp", return_value=fake_app) as p_app,
        patch(
            "tiger_agent.db.utils.create_default_pool",
            return_value=fake_pool,
        ) as p_pool,
        patch(
            "tiger_agent.salesforce.clients.get_salesforce_api_client",
            return_value=fake_salesforce_client,
        ) as p_sf,
        patch(
            "tiger_agent.slack.utils.fetch_bot_info",
            new=AsyncMock(return_value=fake_bot_info),
        ) as p_bot,
    ):
        yield {
            "AsyncApp": p_app,
            "create_default_pool": p_pool,
            "get_salesforce_api_client": p_sf,
            "fetch_bot_info": p_bot,
            "fake_app": fake_app,
            "fake_pool": fake_pool,
            "fake_salesforce_client": fake_salesforce_client,
            "fake_bot_info": fake_bot_info,
        }


class TestHarnessContextCreate:
    async def test_wires_up_all_dependencies_with_defaults(self, stub_external_deps):
        hctx = await HarnessContext.create()

        assert hctx.app is stub_external_deps["fake_app"]
        assert hctx.pool is stub_external_deps["fake_pool"]
        assert hctx.bot_info is stub_external_deps["fake_bot_info"]
        assert hctx.salesforce_client is stub_external_deps["fake_salesforce_client"]
        assert hctx.num_workers == 5
        assert hctx.max_attempts == 3
        assert hctx.proactive_prompt_channels is None

    async def test_forwarhds_overrides_to_context_fields(self, stub_external_deps):
        hctx = await HarnessContext.create(
            num_workers=10,
            max_attempts=7,
            proactive_prompt_channels=["C123", "C456"],
            worker_sleep_seconds=30,
        )

        assert hctx.num_workers == 10
        assert hctx.max_attempts == 7
        assert hctx.proactive_prompt_channels == ["C123", "C456"]
        assert hctx.worker_sleep_seconds == 30

    async def test_uses_explicit_slack_token_over_env_var(
        self, stub_external_deps, monkeypatch
    ):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")

        await HarnessContext.create(slack_token="xoxb-explicit")

        call_kwargs = stub_external_deps["AsyncApp"].call_args.kwargs
        assert call_kwargs["token"] == "xoxb-explicit"

    async def test_falls_back_to_env_var_when_no_token_passed(
        self, stub_external_deps, monkeypatch
    ):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")

        await HarnessContext.create()

        call_kwargs = stub_external_deps["AsyncApp"].call_args.kwargs
        assert call_kwargs["token"] == "xoxb-from-env"

    async def test_pool_sized_to_num_workers(self, stub_external_deps):
        await HarnessContext.create(num_workers=12)

        stub_external_deps["create_default_pool"].assert_called_once_with(12)

    async def test_fetches_bot_info_from_slack_client(self, stub_external_deps):
        await HarnessContext.create()

        stub_external_deps["fetch_bot_info"].assert_awaited_once_with(
            stub_external_deps["fake_app"].client
        )
