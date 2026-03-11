from typing import Any

import logfire
from pydantic_ai import RunContext
from pydantic_ai.mcp import (
    CallToolFunc,
    ProcessToolCallback,
)

from tiger_agent.agent.types import AgentResponseContext
from tiger_agent.mcp.types import MCPDict


def create_wrapped_process_tool_call(
    existing_func: ProcessToolCallback | None,
) -> ProcessToolCallback:
    async def process_tool_call(
        ctx: RunContext[AgentResponseContext],
        call_tool: CallToolFunc,
        name: str,
        tool_args: dict[str, Any],
    ):
        try:
            if existing_func is not None:
                return await existing_func(ctx, call_tool, name, tool_args)

            return await call_tool(name, tool_args, None)
        except Exception as ex:
            logfire.exception(
                "Exception occurred during tool call", name=name, tool_args=tool_args
            )
            message = f"Tool call failed, could not retrieve information. Error: {ex.message or ex}"
            return message

    return process_tool_call


def wrap_mcp_servers_with_exception_handling(mcp_servers: MCPDict) -> MCPDict:
    """Wrap MCP servers with exception handling for tool calls.

    Creates wrapper functions around existing process_tool_call methods
    to add consistent error handling and logging.

    Args:
        mcp_servers: Dictionary of MCP server configurations

    Returns:
        Modified dictionary with wrapped process_tool_call functions
    """
    for value in mcp_servers.values():
        existing_process_tool_call = value.mcp_server.process_tool_call

        value.mcp_server.process_tool_call = create_wrapped_process_tool_call(
            existing_process_tool_call
        )

    return mcp_servers
