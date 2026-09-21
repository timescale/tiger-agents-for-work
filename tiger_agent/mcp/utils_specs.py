from unittest.mock import AsyncMock, MagicMock

from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import drop_internal_only_mcp_servers, filter_mcp_servers


def _server(internal_only: bool, responsive: bool = True) -> McpConfig:
    toolset = MagicMock()
    toolset.list_tools = AsyncMock(
        side_effect=None if responsive else ConnectionError("down")
    )
    return McpConfig(internal_only=internal_only, mcp_server=toolset, url="http://x")


class TestDropInternalOnlyMcpServers:
    def test_keeps_only_public_servers(self):
        servers = {"docs": _server(False), "salesforce": _server(True)}
        assert list(drop_internal_only_mcp_servers(servers)) == ["docs"]

    def test_nothing_to_drop_returns_the_same_servers(self):
        servers = {"docs": _server(False)}
        assert drop_internal_only_mcp_servers(servers) == servers


class TestFilterMcpServers:
    async def test_internal_audience_keeps_internal_servers(self):
        servers = {"docs": _server(False), "salesforce": _server(True)}
        kept = await filter_mcp_servers(servers, include_internal=True)
        assert sorted(kept) == ["docs", "salesforce"]

    async def test_external_audience_drops_internal_servers(self):
        servers = {"docs": _server(False), "salesforce": _server(True)}
        kept = await filter_mcp_servers(servers, include_internal=False)
        assert list(kept) == ["docs"]

    async def test_unresponsive_servers_are_dropped_either_way(self):
        servers = {"docs": _server(False, responsive=False), "slab": _server(True)}
        assert list(await filter_mcp_servers(servers, include_internal=True)) == [
            "slab"
        ]
        assert await filter_mcp_servers(servers, include_internal=False) == {}
