from datetime import UTC, datetime, timedelta

import logfire
from logfire.query_client import AsyncLogfireQueryClient

from tiger_agent.logfire.constants import LOGFIRE_READ_TOKEN
from tiger_agent.slack.types import SlackBaseEvent


async def query_logfire_spans(
    sql: str,
    lookback_hours: float = 1.0,
    limit: int = 100,
) -> list[dict[str, any]]:
    """Query Logfire spans using SQL and return results as JSON.

    The records table has columns like: start_timestamp, end_timestamp, span_name,
    message, trace_id, span_id, parent_span_id, level, attributes, otel_links,
    otel_events, is_exception, otel_status_code, otel_status_message.
    Example: SELECT span_name, message, attributes FROM records WHERE is_exception = true
    """
    assert LOGFIRE_READ_TOKEN
    min_timestamp = datetime.now(tz=UTC) - timedelta(hours=lookback_hours)
    async with AsyncLogfireQueryClient(read_token=LOGFIRE_READ_TOKEN) as client:
        results = await client.query_json_rows(
            sql=sql,
            min_timestamp=min_timestamp,
            limit=limit,
        )
    return results["rows"]


async def get_trace_ids_for_event(
    event: SlackBaseEvent, lookback_hours: float = 24.0
) -> list[str]:
    assert isinstance(event, SlackBaseEvent)
    event_ts = event.ts
    thread_ts = event.thread_ts or event.ts

    # Step 1: find the trace_id(s) of process_task spans for this event.
    # Also match spans where event.ts = thread_ts to capture the original
    # message in the thread (which has thread_ts=None but ts=thread_ts).
    find_process_task_sql = f"""
SELECT trace_id
FROM records
WHERE
span_name = 'process_task'
AND (
    attributes->'task'->'event'->>'ts' = '{event_ts}'
    OR attributes->'task'->'event'->>'thread_ts' = '{thread_ts}'
    OR attributes->'task'->'event'->>'ts' = '{thread_ts}'
)
ORDER BY start_timestamp DESC
"""
    trace_ids_rows = await query_logfire_spans(
        sql=find_process_task_sql, lookback_hours=lookback_hours
    )
    if not trace_ids_rows:
        logfire.info("Could not find trace ids for event", event=event)
        return []

    return [row["trace_id"] for row in trace_ids_rows]


async def get_tool_calls_for_traces(
    trace_ids: list[str], lookback_hours: float = 24.0
) -> list[dict[str, any]]:

    if not trace_ids:
        return []

    # Step 2: find all tool-execution spans in those traces
    find_tool_calls_sql = f"""
SELECT
    start_timestamp,
    attributes->>'gen_ai.tool.name' AS tool_name,
    attributes->>'gen_ai.tool.call.id' AS tool_call_id,
    attributes->'gen_ai.tool.call.arguments' AS tool_arguments,
    attributes->'gen_ai.tool.call.result' AS tool_response,
    is_exception,
    otel_status_message
FROM records
WHERE
    trace_id IN ({",".join(f"'{trace_id}'" for trace_id in trace_ids)})
    AND span_name LIKE 'execute_tool%'
ORDER BY start_timestamp ASC
"""
    return await query_logfire_spans(
        sql=find_tool_calls_sql, lookback_hours=lookback_hours
    )


async def get_tool_calls_for_event(
    event: SlackBaseEvent, lookback_hours: float = 24.0
) -> dict[str, any] | None:
    trace_ids = await get_trace_ids_for_event(
        event=event, lookback_hours=lookback_hours
    )

    if not trace_ids:
        return None

    tool_calls = await get_tool_calls_for_traces(
        trace_ids=trace_ids, lookback_hours=lookback_hours
    )

    if not tool_calls:
        logfire.info(
            "No tool calls found for trace ids",
            trace_ids=trace_ids,
            lookback_hours=lookback_hours,
        )
    return tool_calls


async def get_logs_for_trace(
    trace_id: str,
    lookback_hours: float = 24.0,
    errors_only: bool = False,
    limit: int = 100,
) -> list[dict[str, any]]:
    """Return a condensed list of records (spans + logs) for a trace.

    Set ``errors_only=True`` to restrict results to exception/error rows.
    Each row includes ``span_id`` which can be passed to :func:`get_log_by_id`
    to fetch the full record.
    """
    if not trace_id:
        return []

    error_filter = "AND (is_exception = true OR level >= 'error')" if errors_only else ""
    sql = f"""
SELECT
    start_timestamp,
    span_id,
    span_name,
    level,
    message,
    is_exception,
    otel_status_message
FROM records
WHERE
    trace_id = '{trace_id}'
    {error_filter}
ORDER BY start_timestamp ASC
"""
    return await query_logfire_spans(sql=sql, lookback_hours=lookback_hours, limit=limit)


async def get_log_by_id(
    span_id: str, lookback_hours: float = 24.0
) -> dict[str, any] | None:
    """Return the full record for a single ``span_id`` (or None if not found)."""
    if not span_id:
        return None

    sql = f"""
SELECT *
FROM records
WHERE span_id = '{span_id}'
LIMIT 1
"""
    rows = await query_logfire_spans(sql=sql, lookback_hours=lookback_hours, limit=1)
    return rows[0] if rows else None


async def find_errors(
    lookback_hours: float = 24.0, limit: int = 100
) -> list[dict[str, any]]:
    """Return error/exception records over the given timespan.

    Each row includes ``trace_id``, ``span_id``, ``span_name``, and ``message`` —
    pass ``span_id`` to :func:`get_log_by_id` or ``trace_id`` to
    :func:`get_logs_for_trace` to drill in.
    """
    sql = """
SELECT
    start_timestamp,
    trace_id,
    span_id,
    span_name,
    message
FROM records
WHERE is_exception = true OR level >= 'error'
ORDER BY start_timestamp DESC
"""
    return await query_logfire_spans(sql=sql, lookback_hours=lookback_hours, limit=limit)
