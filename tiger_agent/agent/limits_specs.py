import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tiger_agent.agent.constants import (
    AGENT_MAX_CONTEXT_TOKENS,
    AGENT_MAX_REQUEST_INPUT_TOKENS,
)
from tiger_agent.agent.limits import (
    AGENT_USAGE_LIMITS,
    FINALIZE_USAGE_LIMITS,
    _close_dangling_tool_calls,
    make_limit_warner,
)


def _tool_call(tool_name: str, call_id: str) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args={}, tool_call_id=call_id)]
    )


def _tool_return(tool_name: str, call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content="ok", tool_call_id=call_id)]
    )


def _unanswered(messages) -> set[str]:
    """Tool calls with no matching return -- what Anthropic rejects."""
    calls = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    returns = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    return calls - returns


class TestCloseDanglingToolCalls:
    def test_empty_history_is_left_alone(self):
        assert _close_dangling_tool_calls([]) == []

    def test_complete_history_is_unchanged(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content="summarize case 123")]),
            _tool_call("search_docs", "c1"),
            _tool_return("search_docs", "c1"),
            ModelResponse(parts=[TextPart(content="done")]),
        ]

        assert _close_dangling_tool_calls(messages) == messages

    def test_outstanding_calls_get_a_cancellation_result(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content="summarize case 123")]),
            _tool_call("search_docs", "c1"),
            _tool_return("search_docs", "c1"),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="search_docs", args={}, tool_call_id="c2"),
                    ToolCallPart(tool_name="slack_search", args={}, tool_call_id="c3"),
                ]
            ),
        ]

        repaired = _close_dangling_tool_calls(messages)

        # One appended request answering both orphaned calls, nothing dropped.
        assert len(repaired) == len(messages) + 1
        assert repaired[: len(messages)] == messages
        assert {part.tool_call_id for part in repaired[-1].parts} == {"c2", "c3"}

    def test_repaired_history_satisfies_the_tool_result_invariant(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content="summarize case 123")]),
            _tool_call("search_docs", "c1"),
        ]

        assert _unanswered(messages) == {"c1"}
        assert _unanswered(_close_dangling_tool_calls(messages)) == set()

    def test_context_is_preserved_rather_than_truncated(self):
        """The point of the fallback is the gathered context; it must survive."""
        messages = [
            ModelRequest(parts=[UserPromptPart(content="summarize case 123")]),
            *[_tool_call("search_docs", f"c{i}") for i in range(5)],
        ]

        repaired = _close_dangling_tool_calls(messages)

        assert all(original in repaired for original in messages)


class TestLimits:
    def test_backstop_matches_the_documented_budget(self):
        assert AGENT_USAGE_LIMITS.request_limit == 150
        assert AGENT_USAGE_LIMITS.output_tokens_limit == 40_000

    def test_warner_fires_before_the_backstop(self):
        warner = make_limit_warner()

        assert warner.max_iterations == AGENT_USAGE_LIMITS.request_limit
        assert 0 < warner.warning_threshold < 1
        # Warnings must start strictly before the hard limit, with room to finish.
        assert warner.max_iterations * warner.warning_threshold < warner.max_iterations
        assert warner.critical_remaining_iterations > 0


class TestPerRequestContextCeiling:
    """The proactive half: stop before the provider rejects the prompt.

    A provider 400 is the worst available shape -- the oversized request is
    billed, nothing is salvaged, and the retry rebuilds it. Tripping
    UsageLimitExceeded one turn earlier routes the same situation into the
    warner / partial-answer / ack path that already exists.
    """

    def test_a_per_request_ceiling_is_set(self):
        assert AGENT_USAGE_LIMITS.per_request_input_tokens_limit == (
            AGENT_MAX_REQUEST_INPUT_TOKENS
        )

    def test_it_leaves_room_for_one_more_turn_under_a_1m_window(self):
        """A single tool result is capped near 50K, so one turn cannot clear it."""
        headroom = 1_000_000 - AGENT_MAX_REQUEST_INPUT_TOKENS

        assert headroom >= 100_000

    def test_it_sits_above_the_compaction_trigger(self):
        """Context management gets first attempt; this is the wall behind it."""
        compaction_trigger = AGENT_MAX_CONTEXT_TOKENS * 0.9

        assert compaction_trigger < AGENT_MAX_REQUEST_INPUT_TOKENS

    def test_the_warner_still_warns_before_the_ceiling(self):
        warner = make_limit_warner()

        first_warning_at = warner.max_context_tokens * warner.warning_threshold
        assert first_warning_at < AGENT_MAX_REQUEST_INPUT_TOKENS

    def test_it_raises_the_exception_the_fallback_already_handles(self):
        """The whole point: overflow becomes UsageLimitExceeded, not a 400."""
        from pydantic_ai import UsageLimitExceeded

        with pytest.raises(UsageLimitExceeded):
            AGENT_USAGE_LIMITS.check_per_request_input_tokens(
                AGENT_MAX_REQUEST_INPUT_TOKENS + 1
            )

    def test_a_normal_request_passes(self):
        AGENT_USAGE_LIMITS.check_per_request_input_tokens(324_132)  # observed p95

    def test_the_finalize_pass_is_not_subject_to_it(self):
        """The salvage call must not be blocked by the budget that just tripped."""
        assert FINALIZE_USAGE_LIMITS.per_request_input_tokens_limit is None


class TestWarnerMatchesEnforcedLimits:
    """The warner interpolates its numbers straight into the model's context.

    "Context window: 812345/850000 tokens used" is read as fact. If the figure
    is not the one that actually stops the run, the model is either warned
    about a ceiling that never arrives or blindsided by one it was never told
    about -- and warnings that prove empty devalue the ones that do not.
    """

    def test_iteration_warning_matches_the_enforced_request_limit(self):
        warner = make_limit_warner()

        assert warner.max_iterations == AGENT_USAGE_LIMITS.request_limit

    def test_context_warning_matches_the_enforced_per_request_ceiling(self):
        warner = make_limit_warner()

        assert warner.max_context_tokens == (
            AGENT_USAGE_LIMITS.per_request_input_tokens_limit
        )

    def test_no_countdown_against_an_unenforced_total(self):
        """AGENT_SOFT_TOTAL_TOKENS is a proposal, not a limit."""
        warner = make_limit_warner()

        assert AGENT_USAGE_LIMITS.total_tokens_limit is None
        assert AGENT_USAGE_LIMITS.input_tokens_limit is None
        assert warner.max_total_tokens is None

    def test_every_warned_dimension_is_actually_enforced(self):
        """Guards against drift as limits are tuned."""
        warner = make_limit_warner()
        enforced = {
            "max_iterations": AGENT_USAGE_LIMITS.request_limit,
            "max_context_tokens": AGENT_USAGE_LIMITS.per_request_input_tokens_limit,
            "max_total_tokens": AGENT_USAGE_LIMITS.total_tokens_limit,
        }

        for field, enforced_value in enforced.items():
            warned_value = getattr(warner, field)
            if warned_value is not None:
                assert warned_value == enforced_value, (
                    f"warner {field}={warned_value} but nothing enforces that value"
                )
