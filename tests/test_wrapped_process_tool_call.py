"""Tests for create_wrapped_process_tool_call in tiger_agent.utils."""

import asyncio
import json

from tiger_agent.utils import create_wrapped_process_tool_call

LARGE_PAYLOAD = json.dumps(
    [
        {"id": i, "status": "open", "description": "x" * 400, "title": f"Issue {i}"}
        for i in range(20)
    ]
)


def call_tool_returning(result):
    async def call_tool(name, tool_args, metadata):
        return result

    return call_tool


def run(wrapped, call_tool, name="some_tool", tool_args=None):
    return asyncio.run(wrapped(None, call_tool, name, tool_args or {}))


def test_passthrough_without_compression():
    wrapped = create_wrapped_process_tool_call(None)
    result = run(wrapped, call_tool_returning(LARGE_PAYLOAD))
    assert result is LARGE_PAYLOAD


def test_large_result_compacted_when_enabled():
    wrapped = create_wrapped_process_tool_call(None, compress_results=True)
    result = run(wrapped, call_tool_returning(LARGE_PAYLOAD))
    assert result is not LARGE_PAYLOAD
    assert len(result) < len(LARGE_PAYLOAD)


def test_small_result_untouched_when_enabled():
    wrapped = create_wrapped_process_tool_call(None, compress_results=True)
    result = run(wrapped, call_tool_returning("small"))
    assert result == "small"


def test_existing_hook_runs_and_result_is_compacted():
    async def existing(ctx, call_tool, name, tool_args):
        return LARGE_PAYLOAD

    async def call_tool(name, tool_args, metadata):
        raise AssertionError("call_tool should not be reached when a hook exists")

    wrapped = create_wrapped_process_tool_call(existing, compress_results=True)
    result = run(wrapped, call_tool)
    assert result is not LARGE_PAYLOAD
    assert len(result) < len(LARGE_PAYLOAD)


def test_standard_exception_is_caught():
    # Regression: `ex.message or ex` raised AttributeError on standard
    # exceptions, letting the original exception escape the wrapper.
    async def call_tool(name, tool_args, metadata):
        raise ValueError("kapow")

    wrapped = create_wrapped_process_tool_call(None)
    result = run(wrapped, call_tool)
    assert result == "Tool call failed, could not retrieve information. Error: kapow"


def test_message_bearing_exception_uses_message_attr():
    class McpStyleError(Exception):
        def __init__(self):
            super().__init__("str form")
            self.message = "mcp-style message attr"

    async def call_tool(name, tool_args, metadata):
        raise McpStyleError()

    wrapped = create_wrapped_process_tool_call(None)
    result = run(wrapped, call_tool)
    assert result == (
        "Tool call failed, could not retrieve information. "
        "Error: mcp-style message attr"
    )
