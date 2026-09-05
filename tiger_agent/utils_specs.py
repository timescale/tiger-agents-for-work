from tiger_agent.utils import (
    RepeatedToolCallTracker,
    create_wrapped_process_tool_call,
    split_markdown_text_into_blocks,
)


class TestSplitMarkdownTextIntoBlocks:
    def test_empty_string_returns_empty_list(self):
        assert split_markdown_text_into_blocks("", max_string_length=100) == []

    def test_text_shorter_than_cap_returns_single_chunk(self):
        text = "hello world"
        assert split_markdown_text_into_blocks(text, max_string_length=100) == [text]

    def test_text_exactly_at_cap_returns_single_chunk(self):
        text = "a" * 50
        assert split_markdown_text_into_blocks(text, max_string_length=50) == [text]

    def test_two_paragraphs_fitting_together_stay_in_one_chunk(self):
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        # A single chunk should preserve the "\n\n" separator between paragraphs.
        assert result == [text]

    def test_two_paragraphs_split_when_second_wont_fit_preserves_content(self):
        # Even when the two paragraphs go to different chunks, joining chunks
        # back with "\n\n" must reproduce the input verbatim.
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=20)
        assert "\n\n".join(result) == text

    def test_two_paragraphs_split_when_second_wont_fit(self):
        text = "first paragraph\n\nsecond paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=20)
        assert result == ["first paragraph", "second paragraph"]

    def test_three_paragraphs_split_at_paragraph_boundaries(self):
        text = "aaaa\n\nbbbb\n\ncccc"
        result = split_markdown_text_into_blocks(text, max_string_length=10)
        # "aaaa\n\nbbbb" is 10 chars, exactly the cap. "cccc" opens the next chunk.
        assert result == ["aaaa\n\nbbbb", "cccc"]

    def test_chunk_length_never_exceeds_cap(self):
        paragraphs = [f"paragraph number {i} with some content" for i in range(50)]
        text = "\n\n".join(paragraphs)
        max_len = 100
        result = split_markdown_text_into_blocks(text, max_string_length=max_len)
        for chunk in result:
            assert len(chunk) <= max_len

    def test_no_content_is_lost_across_chunks(self):
        paragraphs = [f"paragraph {i}" for i in range(20)]
        text = "\n\n".join(paragraphs)
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        # Rejoin the chunks with the same separator the function uses internally.
        rejoined = "\n\n".join(result)
        assert rejoined == text

    def test_trailing_double_newline_does_not_produce_empty_chunk(self):
        text = "hello\n\n"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        assert "" not in result
        assert result == ["hello\n\n"]

    def test_input_without_paragraph_breaks_returns_one_chunk_when_under_cap(self):
        text = "line one\nline two\nline three"
        result = split_markdown_text_into_blocks(text, max_string_length=100)
        assert result == [text]

    def test_single_section_larger_than_cap_is_not_further_split(self):
        # Known limitation: the chunker does not further split a paragraph
        # that is itself larger than the cap. It should return the section
        # unchanged as its own chunk (Slack will reject it, but that's a
        # separate concern from content preservation).
        text = "x" * 200
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        assert result == [text]

    def test_no_empty_chunks_are_emitted(self):
        # A leading oversized section previously produced an empty chunk
        # at the head of the result. Guard against that regression.
        text = "x" * 200 + "\n\nnormal paragraph"
        result = split_markdown_text_into_blocks(text, max_string_length=50)
        assert "" not in result

    def test_padding_between_paragraphs_is_only_added_when_needed(self):
        # First chunk should not start with "\n\n"; second chunk should not either.
        text = "aaaa\n\nbbbb\n\ncccc"
        result = split_markdown_text_into_blocks(text, max_string_length=6)
        for chunk in result:
            assert not chunk.startswith("\n\n")


RUN = "run-parent"


class _Ctx:
    """Minimal stand-in for RunContext -- only run_id is read."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


def _ctx(run_id: str) -> _Ctx:
    return _Ctx(run_id)


class TestRepeatedToolCallTracker:
    """Bounds the loops that prose caps demonstrably do not."""

    def test_counts_identical_calls(self):
        t = RepeatedToolCallTracker()
        args = {"case_id": "500Nv00000ZI2OzIAL"}

        assert t.record(RUN, "get_case_summary", args) == 1
        assert t.record(RUN, "get_case_summary", args) == 2
        assert t.record(RUN, "get_case_summary", args) == 3

    def test_allows_two_then_blocks_the_third(self):
        t = RepeatedToolCallTracker()
        args = {"user_id": "005Nv000008h9cvIAA"}

        assert not t.exceeds_limit(t.record(RUN, "get_user_details", args))
        assert not t.exceeds_limit(t.record(RUN, "get_user_details", args))
        assert t.exceeds_limit(t.record(RUN, "get_user_details", args))

    def test_argument_order_does_not_create_a_new_key(self):
        t = RepeatedToolCallTracker()

        t.record(RUN, "search", {"a": 1, "b": 2})
        assert t.record(RUN, "search", {"b": 2, "a": 1}) == 2

    def test_different_arguments_are_tracked_separately(self):
        t = RepeatedToolCallTracker()

        assert t.record(RUN, "search_docs", {"query": "privatelink"}) == 1
        assert t.record(RUN, "search_docs", {"query": "pgbouncer"}) == 1

    def test_a_swept_parameter_counts_as_a_different_call(self):
        """Exact matching by design -- parameter sweeps are a prompt-side fix."""
        t = RepeatedToolCallTracker()

        assert t.record(RUN, "search_docs", {"q": "x", "semanticWeight": 0.7}) == 1
        assert t.record(RUN, "search_docs", {"q": "x", "semanticWeight": 0.3}) == 1

    def test_unserialisable_arguments_still_key_stably(self):
        t = RepeatedToolCallTracker()
        args = {"obj": object()}

        assert t.record(RUN, "weird", args) == 1
        assert t.record(RUN, "weird", args) == 2

    def test_describe_names_the_proxied_tool(self):
        """Through tigerlabs nearly every call arrives as call_tool."""
        described = RepeatedToolCallTracker.describe(
            "call_tool", {"toolName": "pg-aiguide::search_docs", "args": {}}
        )

        assert "pg-aiguide::search_docs" in described

    def test_describe_falls_back_to_the_raw_name(self):
        assert RepeatedToolCallTracker.describe("get_users", {"keyword": "x"}) == (
            "get_users"
        )


class TestRepeatedCallBlocking:
    async def test_third_identical_call_is_not_executed(self):
        calls: list[str] = []

        async def call_tool(name, tool_args):
            calls.append(name)
            return "real result"

        wrapped = create_wrapped_process_tool_call(
            None, tracker=RepeatedToolCallTracker()
        )
        args = {"case_id": "500x"}

        assert (
            await wrapped(_ctx(RUN), call_tool, "get_case_summary", args)
            == "real result"
        )
        assert (
            await wrapped(_ctx(RUN), call_tool, "get_case_summary", args)
            == "real result"
        )
        blocked = await wrapped(_ctx(RUN), call_tool, "get_case_summary", args)

        assert len(calls) == 2, "the third call reached the tool"
        assert "REPEATED_TOOL_CALL" in blocked

    async def test_the_block_message_points_at_the_earlier_result(self):
        async def call_tool(name, tool_args):
            return "ok"

        wrapped = create_wrapped_process_tool_call(
            None, tracker=RepeatedToolCallTracker()
        )
        args = {"user_id": "005x"}
        for _ in range(2):
            await wrapped(_ctx(RUN), call_tool, "get_user_details", args)

        blocked = await wrapped(_ctx(RUN), call_tool, "get_user_details", args)

        assert "already in" in blocked
        assert "unresolved" in blocked

    async def test_distinct_calls_are_unaffected(self):
        calls: list[dict] = []

        async def call_tool(name, tool_args):
            calls.append(tool_args)
            return "ok"

        wrapped = create_wrapped_process_tool_call(
            None, tracker=RepeatedToolCallTracker()
        )
        for i in range(5):
            await wrapped(_ctx(RUN), call_tool, "search", {"q": f"query-{i}"})

        assert len(calls) == 5

    async def test_without_a_tracker_nothing_is_blocked(self):
        calls: list[str] = []

        async def call_tool(name, tool_args):
            calls.append(name)
            return "ok"

        wrapped = create_wrapped_process_tool_call(None)
        for _ in range(5):
            await wrapped(_ctx(RUN), call_tool, "same", {"a": 1})

        assert len(calls) == 5

    async def test_one_tracker_is_shared_across_servers(self):
        """The budget is per run; a run fans out across several MCP servers."""
        calls: list[str] = []

        async def call_tool(name, tool_args):
            calls.append(name)
            return "ok"

        tracker = RepeatedToolCallTracker()
        server_a = create_wrapped_process_tool_call(None, tracker=tracker)
        server_b = create_wrapped_process_tool_call(None, tracker=tracker)
        args = {"toolName": "shared::tool"}

        await server_a(_ctx(RUN), call_tool, "call_tool", args)
        await server_b(_ctx(RUN), call_tool, "call_tool", args)
        blocked = await server_b(_ctx(RUN), call_tool, "call_tool", args)

        assert len(calls) == 2
        assert "REPEATED_TOOL_CALL" in blocked


class TestRunScoping:
    """A sub-agent has its own context, so it must have its own budget.

    SubAgents inherits the parent's toolsets by wrapping the same MCPToolset,
    so coordinator and investigator pass through one wrapper instance.
    """

    def test_runs_do_not_share_a_budget(self):
        t = RepeatedToolCallTracker()
        args = {"case_id": "500x"}

        t.record("run-parent", "get_case_summary", args)
        t.record("run-parent", "get_case_summary", args)

        assert t.record("run-investigator", "get_case_summary", args) == 1

    async def test_a_sub_agent_first_call_is_not_blocked_by_the_parent(self):
        """The regression: the parent exhausting its budget must not starve
        the investigator, whose context has never seen that result."""
        calls: list[str] = []

        async def call_tool(name, tool_args):
            calls.append(name)
            return "real result"

        tracker = RepeatedToolCallTracker()
        wrapped = create_wrapped_process_tool_call(None, tracker=tracker)
        args = {"case_id": "500x"}

        for _ in range(3):
            await wrapped(_ctx("run-parent"), call_tool, "get_case_summary", args)
        assert len(calls) == 2, "parent should be capped at two"

        result = await wrapped(
            _ctx("run-investigator"), call_tool, "get_case_summary", args
        )

        assert result == "real result"
        assert len(calls) == 3

    async def test_a_sub_agent_is_still_capped_within_its_own_run(self):
        calls: list[str] = []

        async def call_tool(name, tool_args):
            calls.append(name)
            return "ok"

        wrapped = create_wrapped_process_tool_call(
            None, tracker=RepeatedToolCallTracker()
        )
        args = {"case_id": "500x"}
        for _ in range(4):
            await wrapped(_ctx("run-investigator"), call_tool, "x", args)

        assert len(calls) == 2

    def test_run_id_falls_back_to_conversation_id(self):
        class OnlyConversation:
            run_id = None
            conversation_id = "conv-7"

        assert RepeatedToolCallTracker.run_id(OnlyConversation()) == "conv-7"

    def test_run_id_tolerates_a_context_without_either(self):
        assert RepeatedToolCallTracker.run_id(object()) == "unknown-run"
