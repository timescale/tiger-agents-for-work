from datetime import timedelta

from psycopg_pool import AsyncConnectionPool


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