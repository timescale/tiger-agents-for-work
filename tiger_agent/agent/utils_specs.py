from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from tiger_agent.agent.constants import AGENT_MAX_DELEGATIONS, AGENT_MAX_REQUESTS
from tiger_agent.agent.limits import FINALIZE_PROMPT_INVESTIGATOR
from tiger_agent.agent.partial_agent import PartialAnswerAgent
from tiger_agent.agent.types import (
    AgentSalesforceResponse,
    AssessmentReport,
    InvestigationReport,
)
from tiger_agent.agent.utils import (
    _output_type,
    budget_capabilities,
    build_investigator,
)
from tiger_agent.salesforce.types import (
    SalesforceCreateNewCaseEvent,
    UserDefinedRuleExecution,
)
from tiger_agent.slack.types import SlackAppMentionEvent


def _static_answer(_messages, _info) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="x")])


def _investigator():
    return build_investigator(
        model=FunctionModel(_static_answer), system_prompt="be thorough"
    )


class TestInvestigatorBudget:
    """The delegate's numbers must be the enforced ones, like the parent's."""

    def test_runs_on_its_own_request_budget(self):
        sub = _investigator()

        assert sub.usage_limits is not None
        assert sub.usage_limits.request_limit == AGENT_MAX_REQUESTS

    def test_delegations_per_run_are_capped(self):
        assert _investigator().max_calls == AGENT_MAX_DELEGATIONS

    def test_finalizes_instead_of_raising(self):
        sub = _investigator()

        assert isinstance(sub.agent, PartialAnswerAgent)
        assert sub.agent.finalize_prompt == FINALIZE_PROMPT_INVESTIGATOR

    def test_reports_structured_output_under_its_original_name(self):
        sub = _investigator()

        assert sub.resolved_name == "investigator"
        assert sub.agent.output_type is InvestigationReport


def _rule_execution(trigger: str) -> UserDefinedRuleExecution:
    return UserDefinedRuleExecution(
        rule_id=1, rule_name="r", owner_slack_id="U", trigger=trigger
    )


class TestOutputType:
    def test_scheduled_rule_runs_return_an_assessment_report(self):
        assert _output_type(_rule_execution("schedule")) is AssessmentReport

    def test_event_triggered_rule_runs_return_text(self):
        assert _output_type(_rule_execution("event")) is str

    def test_salesforce_and_slack_events_are_unchanged(self):
        salesforce = SalesforceCreateNewCaseEvent(
            subject="s", description="d", user="U", channel="C", severity="High"
        )
        slack = SlackAppMentionEvent(
            ts="1.1", text="hi", channel="C", event_ts="1.1", user="U"
        )
        assert _output_type(salesforce) is AgentSalesforceResponse
        assert _output_type(slack) is str


class TestBudgetCapabilities:
    """Coordinator and delegates use the same builder, but the capabilities
    hold per-agent state, so each call must hand out its own instances."""

    def test_each_call_returns_fresh_instances(self):
        first, second = budget_capabilities(), budget_capabilities()

        assert len(first) == len(second) == 2
        assert all(a is not b for a, b in zip(first, second, strict=True))
