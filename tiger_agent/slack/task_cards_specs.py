from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)

from tiger_agent.slack import task_cards as task_cards_module
from tiger_agent.slack.status import DELEGATE_TOOL_NAME, make_subagent_event_handler
from tiger_agent.slack.task_cards import TaskCards, summarize_task

TASK_A = "Search Salesforce cases for machine-generated workloads.\nInclude dates."
TASK_B = "Search meeting transcripts for the same theme."


class _Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


def _delegate_call(call_id: str, task: str, agent: str = "investigator"):
    return FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name=DELEGATE_TOOL_NAME,
            args={"agent_name": agent, "task": task},
            tool_call_id=call_id,
        )
    )


def _delegate_result(call_id: str):
    return FunctionToolResultEvent(
        part=ToolReturnPart(
            tool_name=DELEGATE_TOOL_NAME, content="report", tool_call_id=call_id
        )
    )


def _delegate_failure(call_id: str, reason: str):
    return FunctionToolResultEvent(
        part=RetryPromptPart(
            content=reason, tool_name=DELEGATE_TOOL_NAME, tool_call_id=call_id
        )
    )


def _tool_start(tool_name: str) -> PartStartEvent:
    return PartStartEvent(index=0, part=ToolCallPart(tool_name=tool_name, args={}))


def _chunks(stream) -> list[dict]:
    """Every task_update chunk appended, as plain dicts, in order."""
    out = []
    for call in stream.append.await_args_list:
        for chunk in call.kwargs["chunks"]:
            out.append(chunk.to_dict())
    return out


def _card_log(stream, card_id: str) -> str:
    """What Slack would show as the card's details: every delta concatenated."""
    return "".join(c["details"] for c in _chunks(stream) if c["id"] == card_id)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def stream():
    return SimpleNamespace(append=AsyncMock())


@pytest.fixture
def cards(stream, clock) -> TaskCards:
    return TaskCards(stream, clock=clock)


async def _advance_and_call(cards, clock, tool: str, prompt: str = TASK_A):
    clock.now += task_cards_module.DETAILS_MIN_INTERVAL_SECONDS
    await cards.on_event(_tool_start(tool), subtask="investigator", prompt=prompt)


class TestSummarizeTask:
    def test_uses_the_first_non_empty_line_without_markup(self):
        assert (
            summarize_task("\n\n## **Find** the thing\nmore detail") == "Find the thing"
        )

    def test_clips_long_titles(self):
        title = summarize_task("x" * 200)
        assert len(title) == task_cards_module.TITLE_MAX_CHARS
        assert title.endswith("…")

    def test_falls_back_when_the_task_is_blank(self):
        assert summarize_task("   ") == "Delegated task"


class TestDelegationLifecycle:
    async def test_delegation_start_creates_an_in_progress_card(self, cards, stream):
        await cards.on_event(_delegate_call("c1", TASK_A))

        [chunk] = _chunks(stream)
        assert chunk["type"] == "task_update"
        assert chunk["id"] == "c1"
        assert chunk["status"] == "in_progress"
        assert (
            chunk["title"] == "Search Salesforce cases for machine-generated workloads."
        )
        assert chunk["details"] == "investigator: started"
        assert cards.open_cards == 1

    async def test_result_completes_the_card_with_elapsed_time(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        clock.now += 91

        await cards.on_event(_delegate_result("c1"))

        last = _chunks(stream)[-1]
        assert last["id"] == "c1"
        assert last["status"] == "complete"
        assert last["details"] == "\n✓ done in 91s"
        assert cards.open_cards == 0

    async def test_retry_result_marks_the_card_as_error(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        clock.now += 12

        await cards.on_event(_delegate_failure("c1", "Sub-agent exceeded its budget"))

        last = _chunks(stream)[-1]
        assert last["status"] == "error"
        assert last["details"] == "\n✗ failed after 12s"
        assert "exceeded its budget" in last["output"]

    async def test_non_delegate_tool_calls_do_not_create_cards(self, cards, stream):
        await cards.on_event(
            FunctionToolCallEvent(
                part=ToolCallPart(tool_name="tigerlabs_call_tool", args={})
            )
        )
        await cards.on_event(
            FunctionToolResultEvent(
                part=ToolReturnPart(
                    tool_name="tigerlabs_call_tool", content="x", tool_call_id="zz"
                )
            )
        )
        assert stream.append.await_count == 0

    async def test_a_second_result_for_the_same_call_is_ignored(self, cards, stream):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.on_event(_delegate_result("c1"))
        await cards.on_event(_delegate_result("c1"))

        assert stream.append.await_count == 2


class TestSubagentToolCalls:
    async def test_tool_call_appends_a_line_to_the_matching_card(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.on_event(_delegate_call("c2", TASK_B))

        await _advance_and_call(cards, clock, "tigerlabs_call_tool", prompt=TASK_B)

        last = _chunks(stream)[-1]
        assert last["id"] == "c2"
        assert last["status"] == "in_progress"
        assert last["details"] == "\n• tigerlabs_call_tool"

    async def test_the_card_reads_as_a_log_once_slack_concatenates_details(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await _advance_and_call(cards, clock, "skills_view")
        await _advance_and_call(cards, clock, "slack_get_users")
        clock.now += 40
        await cards.on_event(_delegate_result("c1"))

        assert _card_log(stream, "c1") == (
            "investigator: started\n• skills_view\n• slack_get_users\n✓ done in 46s"
        )

    async def test_single_open_card_is_used_when_the_prompt_is_unknown(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))

        await _advance_and_call(cards, clock, "tigerlabs_discover", prompt=None)

        assert _chunks(stream)[-1]["details"] == "\n• tigerlabs_discover"

    async def test_ambiguous_tool_calls_leave_cards_alone(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.on_event(_delegate_call("c2", TASK_B))
        before = stream.append.await_count

        await _advance_and_call(cards, clock, "tigerlabs_discover", prompt="other")

        assert stream.append.await_count == before

    async def test_tool_lines_are_throttled_and_deduplicated(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        before = stream.append.await_count

        await _advance_and_call(cards, clock, "a")
        assert stream.append.await_count == before + 1

        # a different tool inside the interval: skipped
        clock.now += task_cards_module.DETAILS_MIN_INTERVAL_SECONDS - 1
        await cards.on_event(_tool_start("b"), subtask="investigator", prompt=TASK_A)
        assert stream.append.await_count == before + 1

        # the same tool again after the interval: nothing new to say
        clock.now += 1
        await cards.on_event(_tool_start("a"), subtask="investigator", prompt=TASK_A)
        assert stream.append.await_count == before + 1

        # a different tool after the interval: sent
        await cards.on_event(_tool_start("c"), subtask="investigator", prompt=TASK_A)
        assert stream.append.await_count == before + 2

    async def test_long_logs_are_elided_but_still_finished(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        for i in range(task_cards_module.MAX_TOOL_LINES + 5):
            await _advance_and_call(cards, clock, f"tool_{i}")
        await cards.on_event(_delegate_result("c1"))

        log = _card_log(stream, "c1")
        lines = log.split("\n")
        assert lines[0] == "investigator: started"
        assert lines[1 : task_cards_module.MAX_TOOL_LINES + 1] == [
            f"• tool_{i}" for i in range(task_cards_module.MAX_TOOL_LINES)
        ]
        assert lines[task_cards_module.MAX_TOOL_LINES + 1] == "• …"
        assert lines[-1].startswith("✓ done in")
        assert len(lines) == task_cards_module.MAX_TOOL_LINES + 3

    async def test_every_chunk_fits_slacks_task_update_limit(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", "x" * 500))
        await _advance_and_call(cards, clock, "t" * 400, prompt="x" * 500)
        await cards.on_event(_delegate_failure("c1", "r" * 1000))

        for chunk in _chunks(stream):
            assert len(chunk["details"]) <= task_cards_module.DETAILS_CHUNK_MAX_CHARS
            if chunk.get("output"):
                assert len(chunk["output"]) <= task_cards_module.DETAILS_CHUNK_MAX_CHARS

    async def test_text_events_from_subagents_are_ignored(self, cards, stream):
        await cards.on_event(_delegate_call("c1", TASK_A))
        before = stream.append.await_count

        await cards.on_event(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hi")),
            subtask="investigator",
            prompt=TASK_A,
        )

        assert stream.append.await_count == before

    async def test_card_append_failures_do_not_propagate(self, cards, stream):
        stream.append.side_effect = RuntimeError("slack is down")

        await cards.on_event(_delegate_call("c1", TASK_A))

        assert cards.open_cards == 1


class TestHandlerWiring:
    async def test_handler_passes_the_run_prompt_to_every_sink(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.on_event(_delegate_call("c2", TASK_B))
        clock.now += task_cards_module.DETAILS_MIN_INTERVAL_SECONDS
        seen = []

        class _Recorder:
            async def on_event(self, event, *, subtask=None, prompt=None):
                seen.append((subtask, prompt))

        handler = make_subagent_event_handler(cards, _Recorder())
        ctx = SimpleNamespace(agent=SimpleNamespace(name="investigator"), prompt=TASK_B)

        async def events():
            yield _tool_start("tigerlabs_call_tool")

        await handler(ctx, events())

        assert seen == [("investigator", TASK_B)]
        assert _chunks(stream)[-1]["id"] == "c2"
