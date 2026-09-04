from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tiger_agent.agent.limits import (
    AGENT_USAGE_LIMITS,
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
