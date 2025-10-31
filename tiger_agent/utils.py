import re
from datetime import timedelta

import logfire
from psycopg_pool import AsyncConnectionPool


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
