import os

from pydantic_ai.mcp import MCPServer, MCPServerStreamableHTTP

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
    return MCPServerStreamableHTTP(github_mcp_server_url, tool_prefix="github")


def slack_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(slack_mcp_server_url, tool_prefix="slack")


def docs_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(docs_mcp_server_url, tool_prefix="docs")


def salesforce_mcp_server() -> MCPServer:
    return MCPServerStreamableHTTP(salesforce_mcp_server_url, tool_prefix="salesforce")
