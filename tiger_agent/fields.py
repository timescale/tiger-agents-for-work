from tiger_agent.types import McpConfigExtraFields
from tiger_agent.utils import get_all_fields

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

# our mcp_config.json items have fields that do not exist on pydantic's MCPServer object
# if we pass them in, an error will be thrown. Previously, we were pop()'ing the parameters
# off, but was destructive -- in other words, an mcp config would only be disabled the first time
# this method was called & and it is called each time an agent handles an event
VALID_MCP_SERVER_FIELDS = get_all_fields(MCPServerStdio) | get_all_fields(MCPServerStreamableHTTP)
VALID_EXTRA_FIELDS = get_all_fields(McpConfigExtraFields)

# Get keys that are not in the intersection of valid fields
ALL_VALID_FIELDS = VALID_MCP_SERVER_FIELDS | VALID_EXTRA_FIELDS