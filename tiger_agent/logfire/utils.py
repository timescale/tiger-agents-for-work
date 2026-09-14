import re
from datetime import UTC, datetime, timedelta
from typing import Any

import logfire
from logfire.query_client import AsyncLogfireQueryClient

from tiger_agent.logfire.constants import LOGFIRE_READ_TOKEN
from tiger_agent.slack.types import SlackBaseEvent

# Logfire refuses query windows wider than this.
MAX_QUERY_WINDOW = timedelta(days=14)

# Every value interpolated into SQL below is checked against one of these first;
# some of them arrive from the model.
_SLACK_TS_RE = re.compile(r"^\d{10}\.\d{6}$")
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CHANNEL_RE = re.compile(r"^[CGD][A-Z0-9]{8,}$")


def _require(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(f"{what} {value!r} is not in the expected format")
    return value


async def query_logfire_spans(
    sql: str,
    lookback_hours: float = 1.0,
    limit: int = 100,
    *,
    min_timestamp: datetime | None = None,
    max_timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """Query Logfire spans using SQL and return results as JSON.

    The records table has columns like: start_timestamp, end_timestamp, span_name,
    message, trace_id, span_id, parent_span_id, level, attributes, otel_links,
    otel_events, is_exception, otel_status_code, otel_status_message.
    Example: SELECT span_name, message, attributes FROM records WHERE is_exception = true

    Explicit ``min_timestamp``/``max_timestamp`` bounds take precedence over
    ``lookback_hours`` (which is measured from now). The window may not exceed
    14 days.
    """
    assert LOGFIRE_READ_TOKEN
    if min_timestamp is None:
        min_timestamp = datetime.now(tz=UTC) - timedelta(hours=lookback_hours)
    upper = max_timestamp or datetime.now(tz=UTC)
    if upper - min_timestamp > MAX_QUERY_WINDOW:
        raise ValueError("Logfire query window may not exceed 14 days")
    async with AsyncLogfireQueryClient(read_token=LOGFIRE_READ_TOKEN) as client:
        results = await client.query_json_rows(
            sql=sql,
            min_timestamp=min_timestamp,
            max_timestamp=max_timestamp,
            limit=limit,
        )
    return results["rows"]


async def get_trace_ids_for_slack_message(
    channel: str,
    ts: str,
    thread_ts: str | None,
    lookback_hours: float = 24.0,
    *,
    min_timestamp: datetime | None = None,
    max_timestamp: datetime | None = None,
) -> list[str]:
    """Trace ids of runs triggered by a Slack message (an @mention or thread reply).

    Also matches the thread parent, which has thread_ts=None but ts=thread_ts.
    Runs triggered by something other than a Slack message (a Salesforce event)
    carry no Slack ts and are not found here -- see :func:`find_drafting_traces`.
    """
    _require(_CHANNEL_RE, channel, "channel")
    _require(_SLACK_TS_RE, ts, "ts")
    parent_ts = _require(_SLACK_TS_RE, thread_ts, "thread_ts") if thread_ts else ts

    sql = f"""
SELECT trace_id
FROM records
WHERE
span_name = 'process_task'
AND attributes->'task'->'event'->>'channel' = '{channel}'
AND (
    attributes->'task'->'event'->>'ts' = '{ts}'
    OR attributes->'task'->'event'->>'thread_ts' = '{parent_ts}'
    OR attributes->'task'->'event'->>'ts' = '{parent_ts}'
)
ORDER BY start_timestamp DESC
"""
    rows = await query_logfire_spans(
        sql=sql,
        lookback_hours=lookback_hours,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp,
    )
    return [row["trace_id"] for row in rows]


async def get_trace_ids_for_event(
    event: SlackBaseEvent, lookback_hours: float = 24.0
) -> list[str]:
    assert isinstance(event, SlackBaseEvent)
    trace_ids = await get_trace_ids_for_slack_message(
        channel=event.channel,
        ts=event.ts,
        thread_ts=event.thread_ts,
        lookback_hours=lookback_hours,
    )
    if not trace_ids:
        logfire.info("Could not find trace ids for event", event=event)
    return trace_ids


async def find_drafting_traces(
    channel: str,
    thread_ts: str,
    *,
    min_timestamp: datetime,
    max_timestamp: datetime,
) -> list[dict[str, Any]]:
    """Runs that posted into a Slack thread, oldest first.

    A run that posts a message records a ``post_response`` span carrying the
    destination channel and thread, which identifies it exactly even when the
    run was triggered by something with no Slack ts. The first result is the
    run that started the thread's bot content; later ones are follow-ups.
    Each row: trace_id, handler (the handler span name), start_timestamp,
    duration.
    """
    _require(_CHANNEL_RE, channel, "channel")
    _require(_SLACK_TS_RE, thread_ts, "thread_ts")

    posts_sql = f"""
SELECT DISTINCT trace_id
FROM records
WHERE span_name = 'post_response'
AND attributes->>'channel' = '{channel}'
AND attributes->>'thread_ts' = '{thread_ts}'
"""
    post_rows = await query_logfire_spans(
        sql=posts_sql, min_timestamp=min_timestamp, max_timestamp=max_timestamp
    )
    trace_ids = [row["trace_id"] for row in post_rows]
    if not trace_ids:
        return []

    for trace_id in trace_ids:
        _require(_TRACE_ID_RE, trace_id, "trace_id")
    handlers_sql = f"""
SELECT trace_id, span_name AS handler, start_timestamp, duration
FROM records
WHERE trace_id IN ({",".join(f"'{t}'" for t in trace_ids)})
AND span_name LIKE '%Handler.handle'
ORDER BY start_timestamp ASC
"""
    return await query_logfire_spans(
        sql=handlers_sql, min_timestamp=min_timestamp, max_timestamp=max_timestamp
    )


async def get_tool_calls_for_traces(
    trace_ids: list[str],
    lookback_hours: float = 24.0,
    *,
    min_timestamp: datetime | None = None,
    max_timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    if not trace_ids:
        return []
    for trace_id in trace_ids:
        _require(_TRACE_ID_RE, trace_id, "trace_id")

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
        sql=find_tool_calls_sql,
        lookback_hours=lookback_hours,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp,
    )


async def get_tool_calls_for_event(
    event: SlackBaseEvent, lookback_hours: float = 24.0
) -> list[dict[str, Any]] | None:
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
) -> list[dict[str, Any]]:
    """Return a condensed list of records (spans + logs) for a trace.

    Set ``errors_only=True`` to restrict results to exception/error rows.
    Each row includes ``span_id`` which can be passed to :func:`get_log_by_id`
    to fetch the full record.
    """
    if not trace_id:
        return []
    _require(_TRACE_ID_RE, trace_id, "trace_id")

    error_filter = (
        "AND (is_exception = true OR level >= 'error')" if errors_only else ""
    )
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
    return await query_logfire_spans(
        sql=sql, lookback_hours=lookback_hours, limit=limit
    )


async def get_log_by_id(
    span_id: str, lookback_hours: float = 24.0
) -> dict[str, Any] | None:
    """Return the full record for a single ``span_id`` (or None if not found)."""
    if not span_id:
        return None
    _require(re.compile(r"^[0-9a-f]{16}$"), span_id, "span_id")

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
) -> list[dict[str, Any]]:
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
    return await query_logfire_spans(
        sql=sql, lookback_hours=lookback_hours, limit=limit
    )
