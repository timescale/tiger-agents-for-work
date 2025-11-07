import re
from datetime import timedelta

import logfire
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from tiger_agent.slack import fetch_channel_info
from tiger_agent.types import MCPDict

from slack_bolt.app.async_app import AsyncApp


def serialize_to_jsonb(model: BaseModel) -> Jsonb:
    """Convert a Pydantic BaseModel to a PostgreSQL Jsonb object."""
    return Jsonb(model.model_dump())


def parse_slack_user_name(mention_string: str) -> tuple[str, str] | None:
    """Parse Slack user mention format <@USER_ID|username> and return (username, user_id).

    Args:
        mention_string: String in format '<@U06S8H0V94P|nathan>'

    Returns:
        Tuple of (username, user_id) or None if pattern doesn't match.

    Example:
        parse_slack_user_name('<@U06S8H0V94P|nathan>') -> ('nathan', 'U06S8H0V94P')
    """
    match = re.match(r"<@([A-Z0-9]+)\|([^>]+)>", mention_string)
    if match:
        user_id, username = match.groups()
        return (username, user_id)
    logfire.warning("Argument was not of expected format for a <@USER_ID|username> formatted Slack username + user id")
    return (None, None)


def get_all_fields(cls) -> set:
    """Get all field names from a class and its base classes."""
    fields = set()
    for klass in cls.__mro__:  # Method Resolution Order - includes base classes
        if hasattr(klass, "__annotations__"):
            fields.update(klass.__annotations__.keys())
    return fields

async def usage_limit_reached(pool: AsyncConnectionPool, user_id: str, interval: timedelta, allowed_requests: int | None) -> bool:
    """Determine if the user's request should be processed."""
    if allowed_requests is None:
        return False

    async with pool.connection() as con:
        """Count user requests in both agent.event and agent.event_hist tables within the given interval."""
        result = await con.execute("""SELECT COUNT(*) FROM (
                SELECT 1 FROM agent.event
                WHERE event->>'user' = %s AND event_ts >= now() - %s
                UNION ALL
                SELECT 1 FROM agent.event_hist
                WHERE event->>'user' = %s AND event_ts >= now() - %s
            ) combined""", (user_id, interval, user_id, interval))
        row = await result.fetchone()
        total_requests =  int(row[0]) if row and row[0] is not None else 0
        return total_requests > allowed_requests


async def user_ignored(pool: AsyncConnectionPool, user_id: str) -> bool:
    """Check if a user is currently ignored."""
    async with pool.connection() as con:
        result = await con.execute("SELECT agent.is_user_ignored(%s)", (user_id,))
        row = await result.fetchone()
        return bool(row[0]) if row and row[0] is not None else False


async def user_is_admin(pool: AsyncConnectionPool, user_id: str) -> bool:
    """Check if a user is an admin."""
    async with pool.connection() as con:
        result = await con.execute("SELECT EXISTS(SELECT 1 FROM agent.admin_users WHERE user_id = %s)", (user_id,))
        row = await result.fetchone()
        return bool(row[0]) if row and row[0] is not None else False

def file_type_supported(mimetype: str) -> bool:
    return mimetype == "application/pdf" or mimetype.startswith(("text/", "image/"))

async def filter_mcp_servers(client: AsyncApp, channel_id: str, mcp_servers: MCPDict) -> MCPDict:
    """Filter MCP servers based on channel sharing status.

    Removes internal-only MCP servers when the channel is shared with external users
    to prevent exposure of sensitive tools and data.

    Args:
        client: Slack app client for fetching channel information
        channel_id: ID of the Slack channel to check
        mcp_servers: Dictionary of MCP server configurations to filter

    Returns:
        Filtered dictionary containing only MCP servers appropriate for the channel type
    """
    # determine if channel is shared
    channel_info = await fetch_channel_info(client=client, channel_id=channel_id)
    if channel_info is None:
        # default to shared if we can't fetch channel info to be conservative
        channel_is_shared = True
    else:
        channel_is_shared = channel_info.is_ext_shared or channel_info.is_shared
    
    
    # filter out internal-only tools when event is from a shared channel
    filtered_mcp_servers: MCPDict = {
        name: mcp_config 
        for name, mcp_config in mcp_servers.items() 
        if not channel_is_shared or not mcp_config.internal_only
    }
    
    if channel_is_shared:
        total_tools = len(mcp_servers)
        available_tools = len(filtered_mcp_servers)
        removed_count = total_tools - available_tools
        if removed_count > 0:
            logfire.info("Tools were removed as channel is shared with external users", removed_count=removed_count, channel_id=channel_id)
            
    return filtered_mcp_servers