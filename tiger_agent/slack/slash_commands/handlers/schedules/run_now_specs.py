from asyncio import Queue
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import UserDefinedRule
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules import (
    common as common_module,
)
from tiger_agent.slack.slash_commands.handlers.schedules import (
    run_now as run_now_module,
)
from tiger_agent.slack.slash_commands.handlers.schedules.run_now import handle

RULE = UserDefinedRule(
    id=3,
    name="triage-cse-feedback",
    owner_slack_id="U_OWNER",
    event_type="schedule",
    action_prompt="x",
    repeat=True,
    period=timedelta(hours=168),
    channel="C_RULE",
)


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.trigger = Queue()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    mocks = {
        "insert_event": AsyncMock(),
        "upsert_scheduled_rule": AsyncMock(
            return_value=RULE.model_copy(update={"period": None, "repeat": False})
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(run_now_module, name, mock)
    return mocks


class TestSchedulesRunNow:
    async def test_existing_rule_runs_once_without_rescheduling(
        self, ctx, patched, monkeypatch
    ):
        monkeypatch.setattr(
            common_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
        )

        result = await handle(ctx, ["triage-cse-feedback"])

        kwargs = patched["insert_event"].await_args.kwargs
        assert kwargs["event"]["reschedule"] is False
        assert kwargs["event"]["channel"] == "C_RULE"
        assert kwargs["event"]["window_hours"] == 168.0
        assert kwargs.get("vt") is None
        assert not ctx.hctx.trigger.empty()
        assert "<#C_RULE>" in result
        patched["upsert_scheduled_rule"].assert_not_awaited()

    async def test_channel_argument_overrides_the_rules_channel(
        self, ctx, patched, monkeypatch
    ):
        monkeypatch.setattr(
            common_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
        )

        await handle(ctx, ["triage-cse-feedback", "<#C0OTHER123|elsewhere>"])

        assert (
            patched["insert_event"].await_args.kwargs["event"]["channel"]
            == "C0OTHER123"
        )

    async def test_unknown_rule_is_created_on_demand_in_the_current_channel(
        self, ctx, patched, monkeypatch
    ):
        monkeypatch.setattr(
            common_module, "list_scheduled_rules", AsyncMock(return_value=[])
        )

        await handle(ctx, ["triage-cse-feedback"])

        upsert = patched["upsert_scheduled_rule"].await_args.kwargs
        assert upsert["channel"] == "C_CHAN"
        assert upsert["period"] is None
        assert (
            patched["insert_event"].await_args.kwargs["event"]["window_hours"] == 168.0
        )
