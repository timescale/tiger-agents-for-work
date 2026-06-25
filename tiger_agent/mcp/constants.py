from tiger_agent.mcp.types import McpConfigExtraFields


def get_all_fields(cls) -> set:
    """Get all field names from a class and its base classes."""
    fields = set()
    for klass in cls.__mro__:  # Method Resolution Order - includes base classes
        if hasattr(klass, "__annotations__"):
            fields.update(klass.__annotations__.keys())
    return fields


# fields supported in mcp_config.json items that map to MCPToolset init kwargs
# (plus 'url' which is passed positionally and 'tool_prefix' which we apply via PrefixedToolset).
VALID_MCP_SERVER_FIELDS: set[str] = {
    "url",
    "tool_prefix",
    "headers",
    "auth",
    "verify",
    "http_client",
    "init_timeout",
    "read_timeout",
    "max_retries",
    "tool_error_behavior",
    "cache_tools",
    "cache_resources",
    "cache_prompts",
    "include_instructions",
    "include_return_schema",
    "log_level",
    "client_info",
    "id",
}

# additional fields that we support in our mcp_config.json
VALID_EXTRA_FIELDS = get_all_fields(McpConfigExtraFields)

# all of the fields that are supported in mcp_config.json items
ALL_VALID_FIELDS = VALID_MCP_SERVER_FIELDS | VALID_EXTRA_FIELDS
