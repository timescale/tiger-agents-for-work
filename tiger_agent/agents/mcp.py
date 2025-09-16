import logging
import os

from tiger_agent.agents import AGENT_NAME
from tiger_agent.agents.data_types import (
    MemoriesResponse,
    Memory,
    SlackUserResult,
    SlackUsersResponse,
)
from tiger_agent.agents.mcp_servers import memory_mcp_server, slack_mcp_server

logger = logging.getLogger(__name__)


async def get_user_metadata(user_id: str) -> SlackUserResult | None:
    try:
        result = await slack_mcp_server().direct_call_tool(
            "getUsers", {"keyword": user_id, "includeTimezone": True}
        )
        response = SlackUsersResponse.model_validate(result)
        return response.results[0] if response.results else None
    except Exception as e:
        logger.exception("Failed to get user metadata", exc_info=e)
        return None


async def get_memories(user_id: str) -> list[Memory] | None:
    if os.environ.get("DISABLE_MEMORY_MCP_SERVER"):
        return None
    try:
        result = await memory_mcp_server().direct_call_tool(
            "getMemories", {"key": f"{AGENT_NAME}:{user_id}"}, None
        )
        response = MemoriesResponse.model_validate(result)
        return response.memories
    except Exception as e:
        logger.exception("Failed to get memories", exc_info=e)
        return None
