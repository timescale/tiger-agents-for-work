from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import UserDefinedRule, UserDefinedRuleExecution
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules import list as list_module
from tiger_agent.slack.slash_commands.handlers.schedules.list import handle
from tiger_agent.tasks.types import Task

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


def _queued(attempts: int) -> Task:
    return Task(
        id=9,
        event_ts=datetime(2026, 1, 1, tzinfo=UTC),
        attempts=attempts,
        vt=datetime(2026, 1, 8, tzinfo=UTC),
        claimed=[],
        event=UserDefinedRuleExecution(
            rule_id=3, rule_name=RULE.name, owner_slack_id="U_OWNER", trigger="schedule"
        ),
    )


@pytest.fixture
def ctx(make_slack_command, make_bot_info):
    return CommandContext(
        hctx=MagicMock(), command=make_slack_command(), bot_info=make_bot_info()
    )


class TestSchedulesList:
    async def test_no_rules(self, ctx, monkeypatch):
        monkeypatch.setattr(
            list_module, "list_scheduled_rules", AsyncMock(return_value=[])
        )

        assert await handle(ctx, []) == "No scheduled rules."

    async def test_shows_period_channel_and_next_run(self, ctx, monkeypatch):
        monkeypatch.setattr(
            list_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
        )
        monkeypatch.setattr(
            list_module, "list_events_matching", AsyncMock(return_value=[_queued(0)])
        )

        result = await handle(ctx, [])

        assert "`triage-cse-feedback`" in result
        assert "every 168h" in result
        assert "<#C_CHAN>" in result
        assert "next:" in result and "running" not in result

    async def test_marks_a_claimed_run_as_running(self, ctx, monkeypatch):
        monkeypatch.setattr(
            list_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
        )
        monkeypatch.setattr(
            list_module, "list_events_matching", AsyncMock(return_value=[_queued(1)])
        )

        assert "attempt 1, running" in await handle(ctx, [])

    async def test_reports_a_rule_with_nothing_queued(self, ctx, monkeypatch):
        monkeypatch.setattr(
            list_module, "list_scheduled_rules", AsyncMock(return_value=[RULE])
        )
        monkeypatch.setattr(
            list_module, "list_events_matching", AsyncMock(return_value=[])
        )

        assert "not queued" in await handle(ctx, [])
