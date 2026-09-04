"""Run budgets and graceful degradation for agent runs.

Three layers cooperate so that a long run winds down instead of dying:

1. ``make_limit_warner()`` injects an escalating warning as the run approaches
   its budget, so the model converges on an answer by itself.
2. ``AGENT_USAGE_LIMITS`` is the hard backstop. It stops a genuine runaway.
3. ``run_and_return_partial()`` catches that backstop and spends one tool-free call
   turning the accumulated context into a partial answer, rather than losing
   the whole run.

This module deliberately imports nothing from ``tiger_agent`` so that both
``tiger_agent.agent`` and ``tiger_agent.tasks`` can depend on it.
"""

from __future__ import annotations

import logfire
from pydantic_ai import UsageLimitExceeded, UsageLimits, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_summarization import LimitWarnerCapability

from tiger_agent.agent.constants import (
    AGENT_CRITICAL_REMAINING_REQUESTS,
    AGENT_MAX_CONTEXT_TOKENS,
    AGENT_MAX_OUTPUT_TOKENS,
    AGENT_MAX_REQUESTS,
    AGENT_SOFT_TOTAL_TOKENS,
    AGENT_WARNING_THRESHOLD,
    FINALIZE_MAX_REQUESTS,
)

# Stop a run before its context reaches the model's ceiling.
#
# A run was observed dying on "prompt is too long: 1002326 tokens > 1000000
# maximum". That surfaces as a provider 400, which is the worst shape available:
# the oversized request is billed, nothing is salvaged, and because the prompt
# is a deterministic function of the event, every requeue rebuilds it and fails
# identically.
#
# pydantic-ai checks this against each response's input tokens as it arrives.
# Context grows monotonically, so catching "this turn was 850K" ends the run
# before the next turn would breach 1M -- and it raises UsageLimitExceeded, so
# the warner already counts down toward it and run_and_return_partial still
# turns it into a partial answer.
#
# Sized to sit above the compaction trigger (0.9 x 800K = 720K) so context
# management gets first attempt, and far enough below a 1M window that one more
# turn cannot clear it: a single tool result is capped at 50K tokens by
# ContextManagerCapability and ~50K by MAX_TOOL_RESULT_CHARS. Only 39 of 20,652
# calls in a six-day sample exceeded 720K at all, so routine work never sees it.
AGENT_MAX_REQUEST_INPUT_TOKENS = 850_000

AGENT_USAGE_LIMITS = UsageLimits(
    output_tokens_limit=AGENT_MAX_OUTPUT_TOKENS,
    request_limit=AGENT_MAX_REQUESTS,
    per_request_input_tokens_limit=AGENT_MAX_REQUEST_INPUT_TOKENS,
)

# The fallback call makes exactly one tool-free request, so it needs a budget of
# its own -- the run's original limits are already exhausted by definition.
FINALIZE_USAGE_LIMITS = UsageLimits(
    output_tokens_limit=AGENT_MAX_OUTPUT_TOKENS,
    request_limit=FINALIZE_MAX_REQUESTS,
)

FINALIZE_PROMPT = (
    "You have reached your research budget for this task and no tools are "
    "available for the rest of this turn. Do not ask to continue and do not "
    "request more information.\n\n"
    "Produce your final answer now, in the required output format, using only "
    "what you have already gathered. Where a section is incomplete, say plainly "
    "what you were unable to determine and what you would have checked next, "
    "rather than guessing or leaving it blank."
)


def make_limit_warner() -> LimitWarnerCapability:
    """Warn the model as it approaches its run budget.

    The warning arrives as a trailing user message, which models attend to far
    more reliably than appended system text. Warnings begin at 70% of a limit
    and become critical with three requests to spare, which gives the agent
    room to stop searching and write its answer before anything is enforced.
    """
    return LimitWarnerCapability(
        max_iterations=AGENT_MAX_REQUESTS,
        max_context_tokens=AGENT_MAX_CONTEXT_TOKENS,
        max_total_tokens=AGENT_SOFT_TOTAL_TOKENS,
        warning_threshold=AGENT_WARNING_THRESHOLD,
        critical_remaining_iterations=AGENT_CRITICAL_REMAINING_REQUESTS,
    )


def _close_dangling_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Answer any tool calls the aborted run left outstanding.

    A run cut short by a usage limit can end on a ``ModelResponse`` whose tool
    calls never received results. Anthropic rejects a history containing a
    ``tool_use`` with no matching ``tool_result``, so replaying it verbatim
    would fail. Rather than truncating back to the last clean exchange -- which
    would throw away the very context we are trying to preserve -- synthesise a
    cancellation result for each unanswered call.
    """
    if not messages:
        return []

    answered: set[str] = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }

    outstanding = [
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in answered
    ]

    if not outstanding:
        return list(messages)

    logfire.info(
        "Closing dangling tool calls before finalizing",
        outstanding=len(outstanding),
        tool_names=sorted({part.tool_name for part in outstanding}),
    )

    cancellations: list[ModelRequestPart] = [
        ToolReturnPart(
            tool_name=part.tool_name,
            content="Cancelled: the run's budget was exhausted before this tool ran.",
            tool_call_id=part.tool_call_id,
        )
        for part in outstanding
    ]

    return [*messages, ModelRequest(parts=cancellations)]


@logfire.instrument("run_and_return_partial", extract_args=False)
async def run_and_return_partial(
    agent, *, user_prompt, deps, usage_limits=None, **kwargs
):
    """Run ``agent``, degrading to a partial answer if it exhausts its budget.

    On ``UsageLimitExceeded`` the accumulated history is replayed once with no
    toolsets and a prompt telling the model to finalise. The caller therefore
    gets the agent's usual output type either way, and only sees the exception
    if the fallback attempt also fails.
    """
    limits = usage_limits if usage_limits is not None else AGENT_USAGE_LIMITS

    with capture_run_messages() as messages:
        try:
            return await agent.run(
                user_prompt=user_prompt, deps=deps, usage_limits=limits, **kwargs
            )
        except UsageLimitExceeded as exc:
            if not messages:
                # Nothing was gathered, so there is no partial answer to give.
                raise

            logfire.warn(
                "Agent hit its usage limit; falling back to a partial answer",
                exc_info=exc,
                messages=len(messages),
            )

            history = _close_dangling_tool_calls(list(messages))
            history.append(
                ModelRequest(parts=[UserPromptPart(content=FINALIZE_PROMPT)])
            )

            result = await agent.run(
                message_history=history,
                deps=deps,
                usage_limits=FINALIZE_USAGE_LIMITS,
                toolsets=[],
                **kwargs,
            )
            logfire.info("Returned a partial answer after hitting the usage limit")
            return result
