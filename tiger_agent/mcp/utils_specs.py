from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.mcp.types import McpConfig
from tiger_agent.mcp.utils import (
    create_mcp_servers,
    drop_internal_only_mcp_servers,
    filter_mcp_servers,
)


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


class TestCreateMcpServers:
    def test_internal_only_defaults_to_true_when_omitted(self):
        servers = create_mcp_servers({"docs": {"url": "http://x"}})
        assert servers["docs"].internal_only is True

    def test_explicit_false_opts_a_server_into_external_audiences(self):
        servers = create_mcp_servers(
            {"docs": {"url": "http://x", "internal_only": False}}
        )
        assert servers["docs"].internal_only is False

    def test_explicit_true_is_preserved(self):
        servers = create_mcp_servers(
            {"salesforce": {"url": "http://x", "internal_only": True}}
        )
        assert servers["salesforce"].internal_only is True

    def test_omitted_flag_is_dropped_for_external_audiences(self):
        servers = create_mcp_servers(
            {
                "docs": {"url": "http://x", "internal_only": False},
                "slab": {"url": "http://y"},
            }
        )
        assert list(drop_internal_only_mcp_servers(servers)) == ["docs"]

    def test_disabled_servers_are_skipped(self):
        servers = create_mcp_servers(
            {"docs": {"url": "http://x", "disabled": True}, "slab": {"url": "http://y"}}
        )
        assert list(servers) == ["slab"]

    def test_tool_prefix_falls_back_to_the_server_name(self):
        servers = create_mcp_servers(
            {
                "docs": {"url": "http://x"},
                "slab": {"url": "http://y", "tool_prefix": "wiki"},
            }
        )
        assert servers["docs"].tool_prefix == "docs"
        assert servers["slab"].tool_prefix == "wiki"

    def test_missing_url_raises(self):
        with pytest.raises(ValueError, match="missing a 'url'"):
            create_mcp_servers({"docs": {"internal_only": False}})

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="invalid key"):
            create_mcp_servers({"docs": {"url": "http://x", "internal-only": False}})
