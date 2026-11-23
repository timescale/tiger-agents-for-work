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
from tiger_agent.slack.events import DELEGATE_TOOL_NAME, make_subagent_event_handler
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
        assert chunk["details"] == "started (investigator)"
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
        assert last["details"] == "\n\n✓ done in 91s"
        assert cards.open_cards == 0

    async def test_retry_result_marks_the_card_as_error(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        clock.now += 12

        await cards.on_event(_delegate_failure("c1", "Sub-agent exceeded its budget"))

        last = _chunks(stream)[-1]
        assert last["status"] == "error"
        assert last["details"] == "\n\n✗ failed after 12s"
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
        assert last["details"] == "\n• tigerlabs_call_tool (investigator)"

    async def test_the_card_reads_as_a_log_once_slack_concatenates_details(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await _advance_and_call(cards, clock, "skills_view")
        await _advance_and_call(cards, clock, "slack_get_users")
        clock.now += 40
        await cards.on_event(_delegate_result("c1"))

        assert _card_log(stream, "c1") == (
            "started (investigator)\n• skills_view (investigator)"
            "\n• slack_get_users (investigator)\n\n✓ done in 46s"
        )

    async def test_single_open_card_is_used_when_the_prompt_is_unknown(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))

        await _advance_and_call(cards, clock, "tigerlabs_discover", prompt=None)

        assert _chunks(stream)[-1]["details"] == "\n• tigerlabs_discover (investigator)"

    async def test_ambiguous_tool_calls_leave_cards_alone(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.on_event(_delegate_call("c2", TASK_B))
        before = stream.append.await_count

        await _advance_and_call(cards, clock, "tigerlabs_discover", prompt="other")

        assert stream.append.await_count == before

    async def test_calls_inside_the_interval_are_batched_not_dropped(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        before = stream.append.await_count

        await _advance_and_call(cards, clock, "a")
        assert stream.append.await_count == before + 1

        # two more inside the interval: queued, not sent yet
        clock.now += task_cards_module.DETAILS_MIN_INTERVAL_SECONDS - 1
        await cards.on_event(_tool_start("b"), subtask="investigator", prompt=TASK_A)
        await cards.on_event(_tool_start("c"), subtask="investigator", prompt=TASK_A)
        assert stream.append.await_count == before + 1

        # the next call past the interval flushes everything queued, in order
        clock.now += 1
        await cards.on_event(_tool_start("d"), subtask="investigator", prompt=TASK_A)
        assert stream.append.await_count == before + 2
        assert _card_log(stream, "c1").endswith(
            "\n• a (investigator)\n• b (investigator)"
            "\n• c (investigator)\n• d (investigator)"
        )

    async def test_repeated_tool_names_are_each_logged(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))

        await _advance_and_call(cards, clock, "a")
        await _advance_and_call(cards, clock, "a")

        assert _card_log(stream, "c1").endswith(
            "\n• a (investigator)\n• a (investigator)"
        )

    async def test_queued_calls_are_flushed_before_the_card_closes(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await _advance_and_call(cards, clock, "a")
        # queued inside the interval, so never flushed by a later call
        await cards.on_event(_tool_start("b"), subtask="investigator", prompt=TASK_A)

        await cards.on_event(_delegate_result("c1"))

        log = _card_log(stream, "c1")
        assert "\n• b (investigator)" in log
        assert log.endswith("\n\n✓ done in 3s")

    async def test_long_logs_are_split_across_chunks_without_loss(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        # Queue many tool calls inside one throttle window so they batch into
        # a single flush that exceeds DETAILS_CHUNK_MAX_CHARS.
        num_tools = 20
        for i in range(num_tools):
            await cards.on_event(
                _tool_start(f"tool_{i}"), subtask="investigator", prompt=TASK_A
            )
        # Finishing the delegation flushes the pending batch.
        await cards.on_event(_delegate_result("c1"))

        c1_chunks = [c for c in _chunks(stream) if c["id"] == "c1"]
        # start chunk + at least two from the split batch
        assert len(c1_chunks) >= 3

        for chunk in c1_chunks:
            assert len(chunk["details"]) <= task_cards_module.DETAILS_CHUNK_MAX_CHARS

        # Only the last chunk carries terminal status and output.
        for chunk in c1_chunks[:-1]:
            assert chunk["status"] == "in_progress"
            assert chunk.get("output") is None
        assert c1_chunks[-1]["status"] == "complete"
        assert c1_chunks[-1].get("output") is None

        # Concatenated log contains every tool line in order.
        log = _card_log(stream, "c1")
        for i in range(num_tools):
            assert f"• tool_{i} (investigator)" in log
        assert log.endswith("✓ done in 0s")

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

    async def test_the_output_tool_is_not_logged_as_a_step(self, cards, stream, clock):
        await cards.on_event(_delegate_call("c1", TASK_A))
        before = stream.append.await_count

        await _advance_and_call(cards, clock, "final_result")

        assert stream.append.await_count == before

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


class TestCoordinatorCard:
    async def test_first_coordinator_tool_call_opens_its_card(self, cards, stream):
        await cards.on_event(_tool_start("tigerlabs_discover"))

        [chunk] = _chunks(stream)
        assert chunk["id"] == task_cards_module.COORDINATOR_CARD_ID
        assert chunk["title"] == "Tool calls"
        assert chunk["status"] == "in_progress"
        # no agent tag: every line on this card is the agent's own call
        assert chunk["details"] == "\n• tigerlabs_discover"

    async def test_coordinator_tool_calls_accumulate_on_one_card(
        self, cards, stream, clock
    ):
        await cards.on_event(_tool_start("a"))
        clock.now += task_cards_module.DETAILS_MIN_INTERVAL_SECONDS
        await cards.on_event(_tool_start("b"))

        assert _card_log(stream, task_cards_module.COORDINATOR_CARD_ID) == "\n• a\n• b"

    async def test_delegate_task_is_logged_on_the_coordinator_card(self, cards, stream):
        # the PartStartEvent announces the call; the FunctionToolCallEvent that
        # follows opens the sub-agent's own card
        await cards.on_event(_tool_start(DELEGATE_TOOL_NAME))
        await cards.on_event(_delegate_call("c1", TASK_A))

        assert (
            _card_log(stream, task_cards_module.COORDINATOR_CARD_ID)
            == f"\n• {DELEGATE_TOOL_NAME}"
        )
        assert _card_log(stream, "c1") == "started (investigator)"

    async def test_the_output_tool_does_not_open_a_coordinator_card(
        self, cards, stream
    ):
        await cards.on_event(_tool_start("final_result"))

        assert stream.append.await_count == 0

    async def test_subagent_calls_never_land_on_the_coordinator_card(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))

        await _advance_and_call(cards, clock, "skills_view")

        assert _chunks(stream)[-1]["id"] == "c1"
        assert task_cards_module.COORDINATOR_CARD_ID not in {
            c["id"] for c in _chunks(stream)
        }


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


class TestClose:
    async def test_the_agents_own_card_is_completed(self, cards, stream, clock):
        """The ⚠️ case: nothing else ever closes the coordinator's card."""
        await cards.on_event(_tool_start("slack_get_conversations_with_user"))
        clock.now += 4
        await cards.close()

        last = _chunks(stream)[-1]
        assert last["id"] == task_cards_module.COORDINATOR_CARD_ID
        assert last["status"] == "complete"
        assert "✓ done in 4s" in last["details"]

    async def test_a_cancelled_run_marks_the_card_errored(self, cards, stream, clock):
        await cards.on_event(_tool_start("slack_search"))
        clock.now += 2
        await cards.close("cancelled")

        last = _chunks(stream)[-1]
        assert last["status"] == "error"
        assert "✗ cancelled after 2s" in last["details"]

    async def test_target_gone_marks_the_card_errored(self, cards, stream, clock):
        await cards.on_event(_tool_start("slack_search"))
        await cards.close("target_gone")
        assert _chunks(stream)[-1]["status"] == "error"
        assert "✗ stopped" in _chunks(stream)[-1]["details"]

    async def test_queued_tool_lines_are_flushed_before_the_closing_line(
        self, cards, stream
    ):
        # two calls inside one throttle window: the second is still pending
        await cards.on_event(_tool_start("first"))
        await cards.on_event(_tool_start("second"))
        await cards.close()

        log = _card_log(stream, task_cards_module.COORDINATOR_CARD_ID)
        assert "• first" in log
        assert "• second" in log, "a queued tool line was dropped on close"

    async def test_an_already_finished_delegation_is_not_closed_twice(
        self, cards, stream, clock
    ):
        await cards.on_event(_delegate_call("c1", TASK_A))
        clock.now += 3
        await cards.on_event(_delegate_result("c1"))
        closed = len([c for c in _chunks(stream) if c["id"] == "c1"])

        await cards.close()
        assert len([c for c in _chunks(stream) if c["id"] == "c1"]) == closed

    async def test_an_open_delegation_is_closed_with_the_run(self, cards, stream):
        await cards.on_event(_delegate_call("c1", TASK_A))
        await cards.close("cancelled")

        last = [c for c in _chunks(stream) if c["id"] == "c1"][-1]
        assert last["status"] == "error"

    async def test_close_is_idempotent(self, cards, stream):
        await cards.on_event(_tool_start("a"))
        await cards.close()
        after_first = len(_chunks(stream))
        await cards.close()
        assert len(_chunks(stream)) == after_first

    async def test_close_with_no_cards_sends_nothing(self, cards, stream):
        await cards.close()
        assert _chunks(stream) == []
