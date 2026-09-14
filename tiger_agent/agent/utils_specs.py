from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from tiger_agent.agent.constants import AGENT_MAX_DELEGATIONS, AGENT_MAX_REQUESTS
from tiger_agent.agent.limits import FINALIZE_PROMPT_INVESTIGATOR
from tiger_agent.agent.partial_agent import PartialAnswerAgent
from tiger_agent.agent.types import InvestigationReport
from tiger_agent.agent.utils import budget_capabilities, build_investigator


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


class TestBudgetCapabilities:
    """Coordinator and delegates use the same builder, but the capabilities
    hold per-agent state, so each call must hand out its own instances."""

    def test_each_call_returns_fresh_instances(self):
        first, second = budget_capabilities(), budget_capabilities()

        assert len(first) == len(second) == 2
        assert all(a is not b for a, b in zip(first, second, strict=True))
