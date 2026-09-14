from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from tiger_agent.agent.limits import (
    FINALIZE_PROMPT,
    FINALIZE_PROMPT_INVESTIGATOR,
    run_and_return_partial,
)
from tiger_agent.agent.partial_agent import PartialAnswerAgent
from tiger_agent.agent.types import InvestigationReport

TIGHT_LIMITS = UsageLimits(request_limit=3)


async def probe() -> str:
    return "more data"


def _finalize_prompt_in(messages: list[ModelMessage]) -> str | None:
    """The finalize prompt arrives merged into the same request as the last tool
    return, so look at every part of the last request, not just the first."""
    last = messages[-1]
    if not isinstance(last, ModelRequest):
        return None
    for part in last.parts:
        if isinstance(part, UserPromptPart) and FINALIZE_PROMPT in str(part.content):
            return str(part.content)
    return None


class _Probe:
    """A model that never stops calling tools until it is told to finalize."""

    def __init__(self):
        self.requests = 0
        self.finalize_prompts: list[str] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests += 1
        if (prompt := _finalize_prompt_in(messages)) is not None:
            self.finalize_prompts.append(prompt)
            return self.final(info)
        return ModelResponse(parts=[ToolCallPart(tool_name="probe", args={})])

    def final(self, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="partial answer")])


class _ReportingProbe(_Probe):
    def final(self, info: AgentInfo) -> ModelResponse:
        report = InvestigationReport(
            answer="ran out of budget",
            confidence="low",
            confidence_reason="budget",
            dropped_steps=[
                {"step": "check metrics", "reason": "budget", "tried": ["probe"]}
            ],
            budget_exhausted=True,
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args=report.model_dump(),
                )
            ]
        )


# The keywords the sub-agent harness passes to a delegate's run().
def _harness_kwargs() -> dict:
    return dict(
        model=None,
        model_settings=None,
        usage=None,
        toolsets=[FunctionToolset()],
        capabilities=None,
        event_stream_handler=None,
    )


class TestRunAndReturnPartial:
    async def test_finalizes_even_when_the_caller_supplied_toolsets_and_usage(self):
        """Regression: the finalize call used to pass toolsets=[] alongside the
        caller's toolsets, a duplicate keyword that raised TypeError right when
        the budget had just run out."""
        probe = _Probe()
        agent = Agent(FunctionModel(probe), tools=[probe_tool()])

        result = await run_and_return_partial(
            agent,
            user_prompt="dig",
            deps=None,
            usage_limits=TIGHT_LIMITS,
            toolsets=[FunctionToolset()],
            usage=RunUsage(),
        )

        assert result.output == "partial answer"
        # The limit was really hit: three probing requests, then the finalize one.
        assert probe.requests == TIGHT_LIMITS.request_limit + 1

    async def test_uses_the_given_finalize_prompt(self):
        probe = _Probe()
        agent = Agent(FunctionModel(probe), tools=[probe_tool()])

        await run_and_return_partial(
            agent,
            user_prompt="dig",
            deps=None,
            usage_limits=TIGHT_LIMITS,
            finalize_prompt=FINALIZE_PROMPT_INVESTIGATOR,
        )

        assert probe.finalize_prompts == [FINALIZE_PROMPT_INVESTIGATOR]


class TestPartialAnswerAgent:
    async def test_budget_exhaustion_yields_a_structured_report(self):
        probe = _ReportingProbe()
        agent = PartialAnswerAgent(
            Agent(
                FunctionModel(probe),
                output_type=InvestigationReport,
                tools=[probe_tool()],
            ),
            finalize_prompt=FINALIZE_PROMPT_INVESTIGATOR,
        )

        result = await agent.run(
            "dig", deps=None, usage_limits=TIGHT_LIMITS, **_harness_kwargs()
        )

        assert isinstance(result.output, InvestigationReport)
        assert result.output.budget_exhausted
        assert result.output.dropped_steps[0].step == "check metrics"
        assert probe.finalize_prompts == [FINALIZE_PROMPT_INVESTIGATOR]

    async def test_a_run_within_budget_is_untouched(self):
        def answer_immediately(messages, info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(content="done")])

        agent = PartialAnswerAgent(Agent(FunctionModel(answer_immediately)))

        result = await agent.run(
            "hi", deps=None, usage_limits=TIGHT_LIMITS, **_harness_kwargs()
        )

        assert result.output == "done"

    def test_proxies_identity_so_the_harness_resolves_the_delegate(self):
        inner = Agent(
            FunctionModel(_static_answer),
            name="investigator",
            description="digs",
        )
        wrapped = PartialAnswerAgent(inner)

        assert wrapped.name == "investigator"
        assert wrapped.description == "digs"
        assert wrapped.output_type is inner.output_type


def probe_tool():
    return probe


def _static_answer(_messages, _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="x")])
