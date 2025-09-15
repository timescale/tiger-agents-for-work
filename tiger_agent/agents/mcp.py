import os

import logfire

from tiger_agent.agents import AGENT_NAME
from tiger_agent.agents.data_types import SlackUserResult, SlackUsersResponse, Memory, \
    MemoriesResponse
from tiger_agent.agents.mcp_servers import slack_mcp_server, memory_mcp_server


async def get_user_metadata(user_id: str) -> SlackUserResult | None:
    try:
        result = await slack_mcp_server().direct_call_tool(
            "getUsers", {"keyword": user_id, "includeTimezone": True}
        )
        response = SlackUsersResponse.model_validate(result)
        return response.results[0] if response.results else None
    except Exception as e:
        logfire.exception("Failed to get user metadata", error=e)
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
        logfire.exception("Failed to get memories", error=e)
        return None
