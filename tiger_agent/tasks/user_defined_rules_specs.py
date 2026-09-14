from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import UserDefinedRule
from tiger_agent.tasks import user_defined_rules as module
from tiger_agent.tasks.user_defined_rules import (
    UserDefinedRuleCriteriaMatchResult,
    evaluate_user_defined_rules,
)

RULE = UserDefinedRule(
    id=5,
    name="sev1-heads-up",
    owner_slack_id="U_OWNER",
    event_type="salesforce_event",
    event_subtype="new_assignee",
    criteria="severity 1 cases",
    action_prompt="DM the owner",
    channel="C_ALERTS",
    execution_profile="limited",
)


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "get_matching_user_defined_rules": AsyncMock(return_value=[RULE]),
        "_evaluate_event_criteria": AsyncMock(
            return_value=UserDefinedRuleCriteriaMatchResult(
                matches=True, reason="Severity 1"
            )
        ),
        "insert_event": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(module, name, mock)
    return mocks


class TestMatchEnqueuesAnExecution:
    async def test_carries_trigger_channel_and_profile_from_the_rule(self, patched):
        await evaluate_user_defined_rules(
            pool=MagicMock(),
            event_type="salesforce_event",
            event_dict={"type": "salesforce_event", "subtype": "new_assignee"},
        )

        event = patched["insert_event"].await_args.kwargs["event"]
        assert event["type"] == "user_defined_rule_execution"
        assert event["trigger"] == "event"
        assert event["rule_id"] == 5
        assert event["channel"] == "C_ALERTS"
        assert event["execution_profile"] == "limited"
        assert event["match_reason"] == "Severity 1"
        # Reactive matches are immediate; only schedules carry a window.
        assert event["window_hours"] is None

    async def test_non_match_enqueues_nothing(self, patched):
        patched[
            "_evaluate_event_criteria"
        ].return_value = UserDefinedRuleCriteriaMatchResult(
            matches=False, reason="Sev 3"
        )

        await evaluate_user_defined_rules(
            pool=MagicMock(),
            event_type="salesforce_event",
            event_dict={"type": "salesforce_event"},
        )

        patched["insert_event"].assert_not_awaited()


class TestScheduledRulesStayOutOfTheJudge:
    def test_schedule_is_not_a_real_event_type(self):
        """The judge prefilters on event_type; no incoming event carries 'schedule'."""
        from tiger_agent.events import EVENT_TYPE_REGISTRY
        from tiger_agent.salesforce.types import SCHEDULED_RULE_EVENT_TYPE

        real_types = {cls.model_fields["type"].default for cls in EVENT_TYPE_REGISTRY}
        assert SCHEDULED_RULE_EVENT_TYPE not in real_types

    def test_a_scheduled_rule_has_a_period_but_no_criteria(self):
        rule = UserDefinedRule(
            id=1,
            name="triage",
            owner_slack_id="U",
            event_type="schedule",
            action_prompt="x",
            repeat=True,
            period=timedelta(hours=24),
        )
        assert rule.criteria is None and rule.period == timedelta(hours=24)
