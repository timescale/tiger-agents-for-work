import json
import os
import re
from pathlib import Path
from typing import Any

import logfire
from pydantic_ai.mcp import MCPToolset

from tiger_agent.mcp.constants import ALL_VALID_FIELDS, VALID_MCP_SERVER_FIELDS
from tiger_agent.mcp.types import McpConfig, MCPDict


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
            logfire.exception(
                "MCP server is unresponsive, removing from available servers",
                server_name=name,
                server_url=mcp_config.url,
            )

    return filtered_mcp_servers


def drop_internal_only_mcp_servers(mcp_servers: MCPDict) -> MCPDict:
    """Keep only the servers that may be used in front of an external audience."""
    kept: MCPDict = {
        name: mcp_config
        for name, mcp_config in mcp_servers.items()
        if not mcp_config.internal_only
    }
    removed = [name for name in mcp_servers if name not in kept]
    if removed:
        logfire.info(
            "Internal-only MCP servers were removed for an external audience",
            removed_count=len(removed),
            removed=removed,
        )
    return kept


@logfire.instrument("filter_mcp_servers", extract_args=["include_internal"])
async def filter_mcp_servers(mcp_servers: MCPDict, include_internal: bool) -> MCPDict:
    """The servers one run may use: responsive, and internal-only ones only when allowed.

    Args:
        mcp_servers: A dictionary of {name: McpConfig}
        include_internal: True when the run's audience is internal. False drops
            every server marked ``internal_only`` (a customer question, or a
            Slack channel shared with external users).
    """
    filtered_mcp_servers = await filter_unresponsive_mcp_servers(
        mcp_servers=mcp_servers
    )
    if not include_internal:
        filtered_mcp_servers = drop_internal_only_mcp_servers(filtered_mcp_servers)
    return filtered_mcp_servers


@logfire.instrument("create_mcp_servers", extract_args=False)
def create_mcp_servers(mcp_config: dict[str, dict[str, Any]]) -> MCPDict:
    """Create MCP server instances from configuration.

    Supports two types of MCP servers:
    - MCPToolset: For HTTP-based MCP servers (uses 'url')

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

        url = server_cfg.pop("url", None)
        tool_prefix = server_cfg.pop("tool_prefix", None) or name
        allowed_tools: list[str] | None = cfg.get("allowed_tools")

        if not url:
            raise ValueError(f"MCP server '{name}' is missing a 'url'")

        mcp_server = MCPToolset(url, **server_cfg)

        mcp_servers[name] = McpConfig(
            internal_only=internal_only,
            mcp_server=mcp_server,
            url=url,
            tool_prefix=tool_prefix,
            allowed_tools=allowed_tools,
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
    if not mcp_config:
        return {}

    # this regex replacement allows us to reference env vars
    # from the mcp_config provided e.g. ${SOME_BEARER_TOKEN}
    text = re.sub(
        r"\$\{(\w+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        mcp_config.read_text(),
    )
    return json.loads(text)


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
