from dataclasses import dataclass
from typing import Any

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP


@dataclass
class McpConfigExtraFields:
    """
    This represents the custom-properties on the config items in the mcp_config.json file.
    Each item can use properties from MCPServerStreamableHTTP or MCPServerStdio, plus these fields
    Attributes:
        internal_only: Specifies if this can be used in externally shared channels

    """

    internal_only: bool
    disabled: bool


@dataclass
class McpConfig:
    """
    Attributes:
        internal_only: Specifies if this can be used in externally shared channels
        mcp_server: The MCP server instance
    """

    internal_only: bool
    mcp_server: MCPServerStreamableHTTP | MCPServerStdio
    headers: dict[str, Any] | None = None


type MCPDict = dict[str, McpConfig]
