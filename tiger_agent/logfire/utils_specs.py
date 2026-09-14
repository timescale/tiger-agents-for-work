from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tiger_agent.logfire import utils as utils_module
from tiger_agent.logfire.utils import (
    find_drafting_traces,
    get_tool_calls_for_traces,
    get_trace_ids_for_slack_message,
    query_logfire_spans,
)

CHANNEL = "C0AK9P2V1LK"
THREAD_TS = "1789143575.595509"
TRACE = "01a09141ae6c663a5be0ebffb00f8fe0"
ANCHOR = datetime(2026, 9, 11, 16, 19, 35, tzinfo=UTC)


@pytest.fixture
def queries(monkeypatch):
    """Capture the SQL each helper builds; nothing reaches Logfire."""
    captured: list[str] = []

    async def fake_query(sql, *args, **kwargs):
        captured.append(sql)
        return fake_query.rows.pop(0) if fake_query.rows else []

    fake_query.rows = []
    monkeypatch.setattr(utils_module, "query_logfire_spans", fake_query)
    return captured, fake_query


class TestInputValidation:
    """Values land in f-string SQL, and some of them come from the model."""

    @pytest.mark.parametrize(
        "channel, ts, thread_ts",
        [
            ("C0AK9P2V1LK'; drop table records; --", THREAD_TS, None),
            (CHANNEL, "not-a-ts", None),
            (CHANNEL, THREAD_TS, "1789143575"),
        ],
    )
    async def test_slack_message_lookup_rejects_malformed_values(
        self, queries, channel, ts, thread_ts
    ):
        with pytest.raises(ValueError):
            await get_trace_ids_for_slack_message(channel, ts, thread_ts)
        assert queries[0] == []

    async def test_tool_call_lookup_rejects_a_bad_trace_id(self, queries):
        with pytest.raises(ValueError):
            await get_tool_calls_for_traces(["abc"])

    async def test_window_wider_than_14_days_is_refused(self, monkeypatch):
        monkeypatch.setattr(utils_module, "LOGFIRE_READ_TOKEN", "token")
        monkeypatch.setattr(utils_module, "AsyncLogfireQueryClient", AsyncMock())

        with pytest.raises(ValueError, match="14 days"):
            await query_logfire_spans(
                "select 1",
                min_timestamp=ANCHOR - timedelta(days=15),
                max_timestamp=ANCHOR,
            )


class TestFindDraftingTraces:
    async def test_matches_the_post_response_span_then_lists_handlers(self, queries):
        captured, fake = queries
        fake.rows = [
            [{"trace_id": TRACE}],
            [
                {
                    "trace_id": TRACE,
                    "handler": "SalesforceAssignmentChangedHandler.handle",
                    "start_timestamp": "2026-09-11T16:16:35Z",
                    "duration": 180.4,
                }
            ],
        ]

        runs = await find_drafting_traces(
            CHANNEL,
            THREAD_TS,
            min_timestamp=ANCHOR - timedelta(days=7),
            max_timestamp=ANCHOR + timedelta(days=1),
        )

        posts_sql, handlers_sql = captured
        assert "span_name = 'post_response'" in posts_sql
        assert f"attributes->>'channel' = '{CHANNEL}'" in posts_sql
        assert f"attributes->>'thread_ts' = '{THREAD_TS}'" in posts_sql
        assert f"'{TRACE}'" in handlers_sql and "Handler.handle" in handlers_sql
        assert runs[0]["handler"] == "SalesforceAssignmentChangedHandler.handle"

    async def test_no_posting_run_means_no_second_query(self, queries):
        captured, _ = queries

        runs = await find_drafting_traces(
            CHANNEL,
            THREAD_TS,
            min_timestamp=ANCHOR - timedelta(days=7),
            max_timestamp=ANCHOR,
        )

        assert runs == []
        assert len(captured) == 1


class TestSlackMessageLookup:
    async def test_matches_message_thread_and_parent_and_scopes_to_channel(
        self, queries
    ):
        captured, fake = queries
        fake.rows = [[{"trace_id": TRACE}]]

        ids = await get_trace_ids_for_slack_message(
            CHANNEL, "1789144971.263169", THREAD_TS
        )

        [sql] = captured
        assert f"->>'channel' = '{CHANNEL}'" in sql
        assert "'1789144971.263169'" in sql
        assert sql.count(f"'{THREAD_TS}'") == 2
        assert ids == [TRACE]
