from tiger_agent.types import McpConfigExtraFields

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

def get_all_fields(cls) -> set:
    """Get all field names from a class and its base classes."""
    fields = set()
    for klass in cls.__mro__:  # Method Resolution Order - includes base classes
        if hasattr(klass, "__annotations__"):
            fields.update(klass.__annotations__.keys())
    return fields

# our mcp_config.json items have fields that do not exist on pydantic's MCPServer object
# if we pass them in, an error will be thrown. Previously, we were pop()'ing the parameters
# off, but was destructive -- in other words, an mcp config would only be disabled the first time
# this method was called & and it is called each time an agent handles an event
VALID_MCP_SERVER_FIELDS = get_all_fields(MCPServerStdio) | get_all_fields(MCPServerStreamableHTTP)
VALID_EXTRA_FIELDS = get_all_fields(McpConfigExtraFields)

# Get keys that are not in the intersection of valid fields
ALL_VALID_FIELDS = VALID_MCP_SERVER_FIELDS | VALID_EXTRA_FIELDS