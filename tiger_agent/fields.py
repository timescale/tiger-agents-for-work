from tiger_agent.types import McpConfigExtraFields

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP


def get_all_fields(cls) -> set:
    """Get all field names from a class and its base classes."""
    fields = set()
    for klass in cls.__mro__:  # Method Resolution Order - includes base classes
        if hasattr(klass, "__annotations__"):
            fields.update(klass.__annotations__.keys())
    return fields


# fields that Pydantics MCP-classes are expecting
VALID_MCP_SERVER_FIELDS = get_all_fields(MCPServerStdio) | get_all_fields(
    MCPServerStreamableHTTP
)

# additional fields that we support in our mcp_config.json
VALID_EXTRA_FIELDS = get_all_fields(McpConfigExtraFields)

# all of the fields that are supported in mcp_config.json items
ALL_VALID_FIELDS = VALID_MCP_SERVER_FIELDS | VALID_EXTRA_FIELDS
