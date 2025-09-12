import os
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc, MCPServer, MCPServerStreamableHTTP, ToolResult

from app import AGENT_NAME

# Local instances cache
_github_server_instance: MCPServer | None = None
_slack_server_instance: MCPServer | None = None
_docs_server_instance: MCPServer | None = None
_salesforce_server_instance: MCPServer | None = None
_linear_server_instance: MCPServer | None = None
_memory_server_instance: MCPServer | None = None

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


def github_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_GITHUB_MCP_SERVER"):
        return None
    global _github_server_instance
    if _github_server_instance is None:
        _github_server_instance = MCPServerStreamableHTTP(
            url=github_mcp_server_url, tool_prefix="github"
        )
    return _github_server_instance


def slack_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_SLACK_MCP_SERVER"):
        return None
    global _slack_server_instance
    if _slack_server_instance is None:
        _slack_server_instance = MCPServerStreamableHTTP(
            url=slack_mcp_server_url, tool_prefix="slack"
        )
    return _slack_server_instance


def docs_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_DOCS_MCP_SERVER"):
        return None
    global _docs_server_instance
    if _docs_server_instance is None:
        _docs_server_instance = MCPServerStreamableHTTP(
            url=docs_mcp_server_url, tool_prefix="docs"
        )
    return _docs_server_instance


def salesforce_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_SALESFORCE_MCP_SERVER"):
        return None
    global _salesforce_server_instance
    if _salesforce_server_instance is None:
        _salesforce_server_instance = MCPServerStreamableHTTP(
            url=salesforce_mcp_server_url, tool_prefix="salesforce"
        )
    return _salesforce_server_instance


def linear_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_LINEAR_MCP_SERVER"):
        return None
    global _linear_server_instance
    if _linear_server_instance is None:
        _linear_server_instance = MCPServerStreamableHTTP(
            url=linear_mcp_server_url, tool_prefix="linear"
        )
    return _linear_server_instance


def memory_mcp_server(key_prefix: str = AGENT_NAME) -> MCPServer | None:
    if os.environ.get("DISABLE_MEMORY_MCP_SERVER"):
        return None
    global _memory_server_instance
    if _memory_server_instance is None:

        async def process_memory_tool_calls(
            ctx: RunContext[Any],
            call_tool: CallToolFunc,
            name: str,
            tool_args: dict[str, Any],
        ) -> ToolResult:
            if name in ["forget", "remember", "update"] and (
                not hasattr(ctx.deps, "user_id")
                or not ctx.deps.user_id
                or tool_args.get("key") != f"{key_prefix}:{ctx.deps.user_id}"
            ):
                return (
                    "Tried altering memories for a different user which is not allowed"
                )
            return await call_tool(name, tool_args, None)

        _memory_server_instance = MCPServerStreamableHTTP(
            url=memory_mcp_server_url,
            tool_prefix="memory",
            process_tool_call=process_memory_tool_calls,
        )
    return _memory_server_instance
