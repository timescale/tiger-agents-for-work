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
        return True
    
    async with pool.connection() as con:
        current_request_result = await con.execute("""select COUNT(*) from agent.event
                          where event->>'user' = %s
                          and event_ts >= now() - %s; """, (user_id, interval))
        current_request_row = await current_request_result.fetchone()
        
        num_current_requests_for_user_in_interval = int(current_request_row[0]) if current_request_row and current_request_row[0] is not None else 0
        
        if num_current_requests_for_user_in_interval > allowed_requests:
            return True
        
        historic_request_result = await con.execute("""select COUNT(*) from agent.event_hist
                          where event->>'user' = %s
                          and event_ts >= now() - %s; """, (user_id, interval))
        historic_request_row = await historic_request_result.fetchone()
        num_historic_requests_for_user_in_interval = int(historic_request_row[0]) if historic_request_row and historic_request_row[0] is not None else 0
        
        return (num_current_requests_for_user_in_interval + num_historic_requests_for_user_in_interval) > allowed_requests