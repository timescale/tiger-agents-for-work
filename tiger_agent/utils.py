from datetime import timedelta

from psycopg_pool import AsyncConnectionPool


def _get_user_requests_query(table_name: str) -> str:
    """Generate SQL query to count user requests from specified table."""
    return f"""select COUNT(*) from {table_name}
                          where event->>'user' = %s
                          and event_ts >= now() - %s"""


async def _count_user_requests(con, table_name: str, user_id: str, interval: timedelta) -> int:
    """Count user requests in specified table within the given interval."""
    query = _get_user_requests_query(table_name)
    result = await con.execute(query, (user_id, interval))
    row = await result.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


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
        return True

    async with pool.connection() as con:
        num_current_requests = await _count_user_requests(con, "agent.event", user_id, interval)

        if num_current_requests > allowed_requests:
            return True

        num_historic_requests = await _count_user_requests(con, "agent.event_hist", user_id, interval)

        return (num_current_requests + num_historic_requests) > allowed_requests