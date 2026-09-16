from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import UserDefinedRule
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules import (
    common as common_module,
)
from tiger_agent.slack.slash_commands.handlers.schedules import (
    remove as remove_module,
)
from tiger_agent.slack.slash_commands.handlers.schedules.remove import handle

RULE = UserDefinedRule(
    id=3,
    name="triage-cse-feedback",
    owner_slack_id="U_OWNER",
    event_type="schedule",
    action_prompt="x",
    repeat=True,
    period=timedelta(hours=168),
    channel="C_CHAN",
)


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    return CommandContext(
        hctx=MagicMock(), command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    mocks = {
        "delete_events_matching": AsyncMock(return_value=2),
        "toggle_user_defined_rule": AsyncMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(remove_module, name, mock)
    monkeypatch.setattr(
        common_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
    )
    return mocks


class TestSchedulesRemove:
    async def test_clears_queued_runs_and_disables_the_rule(self, ctx, patched):
        result = await handle(ctx, ["triage-cse-feedback"])

        assert (
            patched["delete_events_matching"].await_args.kwargs["match"]["rule_id"] == 3
        )
        toggle = patched["toggle_user_defined_rule"].await_args.kwargs
        assert toggle["rule_id"] == 3 and toggle["enabled"] is False
        assert "Removed 2 queued run(s)" in result

    async def test_unknown_rule_touches_nothing(self, ctx, patched):
        result = await handle(ctx, ["nope"])

        assert result == "No scheduled rule named `nope`."
        patched["delete_events_matching"].assert_not_awaited()
        patched["toggle_user_defined_rule"].assert_not_awaited()
