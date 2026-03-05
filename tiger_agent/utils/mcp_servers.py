import json
from pathlib import Path
from typing import Any

import logfire
from pydantic_ai.mcp import (
    MCPServerStdio,
    MCPServerStreamableHTTP,
)
from slack_bolt.app.async_app import AsyncApp

from tiger_agent.fields import ALL_VALID_FIELDS, VALID_MCP_SERVER_FIELDS
from tiger_agent.types import McpConfig, MCPDict
from tiger_agent.utils.slack import fetch_channel_info


async def filter_unresponsive_mcp_servers(mcp_servers: MCPDict) -> MCPDict:
    """Filter out MCP servers that are unresponsive.

    Tests each MCP server by calling list_tools() and removes any servers
    that raise exceptions during this call.

    Args:
        mcp_servers: A dictionary of {name: McpConfig}

    Returns:
        Filtered dictionary containing only responsive MCP servers
    """
    filtered_mcp_servers: MCPDict = {}

    for name, mcp_config in mcp_servers.items():
        try:
            await mcp_config.mcp_server.list_tools()
            filtered_mcp_servers[name] = mcp_config
        except Exception:
            logfire.error(
                "MCP server is unresponsive, removing from available servers",
                server_name=name,
                server_url=mcp_config.mcp_server.url,
            )

    return filtered_mcp_servers


async def filter_internal_only_mcp_servers(
    mcp_servers: MCPDict, client: AsyncApp, channel_id: str
) -> MCPDict:
    """Filter MCP servers based on channel sharing status.

    Removes internal-only MCP servers when the channel is shared with external users
    to prevent exposure of sensitive tools and data.

    Args:
        mcp_servers: A dictionary of {name: McpServer}
        client: Slack app client for fetching channel information
        channel_id: ID of the Slack channel to check

    Returns:
        Filtered dictionary containing only MCP servers appropriate for the channel type
    """
    channel_info = await fetch_channel_info(client=client, channel_id=channel_id)

    # if channel is not shared, just return the full list
    if (
        channel_info is not None
        and not channel_info.is_ext_shared
        and not channel_info.is_shared
    ):
        return mcp_servers

    # filter out internal-only tools
    filtered_mcp_servers: MCPDict = {
        name: mcp_config
        for name, mcp_config in mcp_servers.items()
        if not mcp_config.internal_only
    }

    total_tools = len(mcp_servers)
    available_tools = len(filtered_mcp_servers)
    removed_count = total_tools - available_tools
    if removed_count > 0:
        logfire.info(
            "Tools were removed as channel is shared with external users",
            removed_count=removed_count,
            channel_id=channel_id,
        )

    return filtered_mcp_servers


@logfire.instrument("filter_mcp_servers", extract_args=False)
async def filter_mcp_servers(
    mcp_servers: MCPDict, client: AsyncApp, channel_id: str
) -> MCPDict:
    """Filter MCP servers based on responsiveness and channel sharing status.

    First removes unresponsive MCP servers, then removes internal-only MCP servers
    when the channel is shared with external users to prevent exposure of
    sensitive tools and data.

    Args:
        mcp_servers: A dictionary of {name: McpServer}
        client: Slack app client for fetching channel information
        channel_id: ID of the Slack channel to check

    Returns:
        Filtered dictionary containing only responsive MCP servers appropriate for the channel type
    """
    filtered_mcp_servers = await filter_unresponsive_mcp_servers(
        mcp_servers=mcp_servers
    )

    filtered_mcp_servers = await filter_internal_only_mcp_servers(
        mcp_servers=filtered_mcp_servers, client=client, channel_id=channel_id
    )

    return filtered_mcp_servers


@logfire.instrument("create_mcp_servers", extract_args=False)
def create_mcp_servers(mcp_config: dict[str, dict[str, Any]]) -> MCPDict:
    """Create MCP server instances from configuration.

    Supports two types of MCP servers:
    - MCPServerStdio: For command-line MCP servers (uses 'command' and 'args')
    - MCPServerStreamableHTTP: For HTTP-based MCP servers (uses 'url')

    Servers marked with 'disabled': true are skipped.

    Args:
        mcp_config: Dictionary of server configurations

    Returns:
        Dictionary mapping server names to configured MCP server instances
    """
    mcp_servers: MCPDict = {}

    # our mcp_config.json items are Pydantic MCPServer* properties with additional properties to control
    # tiger-agent behavior. These extra properties need to be excluded from the parameters that we pass
    # into the MCPServer* configurations. Also, we want to throw if there are any fields that we are not expecting
    for name, cfg in mcp_config.items():
        if cfg.get("disabled", False):
            continue

        internal_only = cfg.get("internal_only", False)
        invalid_keys = [k for k in cfg if k not in ALL_VALID_FIELDS]

        if len(invalid_keys) > 0:
            logfire.error(
                "Received an invalid key in mcp_config", invalid_keys=invalid_keys
            )
            raise ValueError("Received an invalid key in mcp_config", invalid_keys)

        server_cfg = {k: v for k, v in cfg.items() if k in VALID_MCP_SERVER_FIELDS}

        if not server_cfg.get("tool_prefix"):
            server_cfg["tool_prefix"] = name

        mcp_server: MCPServerStdio | MCPServerStreamableHTTP

        if server_cfg.get("command"):
            mcp_server = MCPServerStdio(**server_cfg)
        elif server_cfg.get("url"):
            mcp_server = MCPServerStreamableHTTP(**server_cfg)
        mcp_servers[name] = McpConfig(
            internal_only=internal_only, mcp_server=mcp_server
        )
    return mcp_servers


@logfire.instrument("load_mcp_config")
def load_mcp_config(mcp_config: Path) -> dict[str, dict[str, Any]]:
    """Load MCP server configuration from a JSON file.

    Args:
        mcp_config: Path to JSON configuration file

    Returns:
        Dictionary mapping server names to their configuration dictionaries
    """
    loaded_mcp_config: dict[str, dict[str, Any]] = (
        json.loads(mcp_config.read_text()) if mcp_config else {}
    )
    return loaded_mcp_config


class MCPLoader:
    """Lazy loader for MCP server configurations.

    This class loads MCP server configuration once during initialization
    and creates fresh server instances each time it's called. This pattern
    allows TigerAgent to reconnect to MCP servers for each request while
    reusing the same configuration.

    Args:
        config: Path to MCP configuration JSON file, or None for no servers
    """

    def __init__(self, config: Path | None):
        self._config = load_mcp_config(config) if config else {}

    def __call__(self) -> MCPDict:
        """Create fresh MCP server instances from the loaded configuration.

        Returns:
            Dictionary of configured MCP server instances ready for use
        """
        return create_mcp_servers(self._config)
