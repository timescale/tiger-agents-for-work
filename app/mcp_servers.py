import os
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc, MCPServer, MCPServerStreamableHTTP, ToolResult

from app import AGENT_NAME

docs_mcp_server_url = os.environ.get(
    "DOCS_MCP_SERVER_URL", "http://tiger-docs-mcp-server/mcp"
)
github_mcp_server_url = os.environ.get(
    "GITHUB_MCP_SERVER_URL", "http://tiger-gh-mcp-server/mcp"
)
linear_mcp_server_url = os.environ.get(
    "LINEAR_MCP_SERVER_URL", "http://tiger-linear-mcp-server/mcp"
)
memory_mcp_server_url = os.environ.get(
    "MEMORY_MCP_SERVER_URL", "http://tiger-memory-mcp-server/mcp"
)
salesforce_mcp_server_url = os.environ.get(
    "SALESFORCE_MCP_SERVER_URL", "http://tiger-salesforce-mcp-server/mcp"
)
slack_mcp_server_url = os.environ.get(
    "SLACK_MCP_SERVER_URL", "http://tiger-slack-mcp-server/mcp"
)

def github_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(url=github_mcp_server_url, tool_prefix="github")


def slack_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(url=slack_mcp_server_url, tool_prefix="slack")


def docs_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(url=docs_mcp_server_url, tool_prefix="docs")


def salesforce_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(url=salesforce_mcp_server_url, tool_prefix="salesforce")


def linear_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(url=linear_mcp_server_url, tool_prefix="linear")


def memory_mcp_server(key_prefix: str = AGENT_NAME) -> MCPServer:
    async def process_memory_tool_calls(
        ctx: RunContext[Any],
        call_tool: CallToolFunc,
        name: str,
        tool_args: dict[str, Any],
    ) -> ToolResult:
        if name in ["forget", "remember", "update"] and (
            not hasattr(ctx.deps, "user_id") or not ctx.deps.user_id
            or tool_args.get("key") != f"{key_prefix}:{ctx.deps.user_id}"
        ):
            return "Tried altering memories for a different user which is not allowed"
        return await call_tool(name, tool_args, None)
    
    return MCPServerStreamableHTTP(
        url=memory_mcp_server_url,
        tool_prefix="memory",
        process_tool_call=process_memory_tool_calls
    )
