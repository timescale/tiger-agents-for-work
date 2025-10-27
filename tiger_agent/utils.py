from datetime import timedelta

from psycopg_pool import AsyncConnectionPool


def get_all_fields(cls) -> set:
    """Get all field names from a class and its base classes."""
    fields = set()
    for klass in cls.__mro__:  # Method Resolution Order - includes base classes
        if hasattr(klass, "__annotations__"):
            fields.update(klass.__annotations__.keys())
    return fields

async def should_process_request(pool: AsyncConnectionPool, user_id: str, interval: timedelta, allowed_requests: int | None) -> bool:
    """Determine if the user's request should be processed."""
    if allowed_requests is None:
        return True
    
    async with pool.connection() as con:
        result = await con.execute("""select COUNT(*) from agent.event_hist
                          where event->>'user' = %s
                          and event_ts >= now() - %s; """, (user_id, interval))
        row = await result.fetchone()
        num_requests_for_user_in_interval = int(row[0]) if row and row[0] is not None else 0
        
        return num_requests_for_user_in_interval < allowed_requests