from datetime import UTC, datetime, timedelta

import pytest

from tiger_agent.db.utils import (
    delete_events_matching,
    insert_event_if_absent,
    list_feedback_ratings,
    list_feedback_ratings_for_thread,
    upsert_scheduled_rule,
)


def _rule_row(**overrides) -> dict:
    defaults = dict(
        id=3,
        name="triage-cse-feedback",
        owner_slack_id="U_OWNER",
        event_type="schedule",
        event_subtype=None,
        criteria=None,
        criteria_examples=[],
        action_prompt="Discover and follow the skill `triage-cse-feedback`.",
        enabled=True,
        repeat=True,
        period=timedelta(hours=168),
        channel="C_CHAN",
        execution_profile="full",
    )
    return {**defaults, **overrides}


@pytest.fixture
def pool(make_pool_mock):
    return make_pool_mock()


class TestQueueHelpers:
    async def test_delete_events_matching_returns_the_count(self, pool):
        pool._connection.execute.return_value.fetchone.return_value = (2,)

        removed = await delete_events_matching(pool, {"type": "x", "rule_id": 3})

        sql, params = pool._connection.execute.await_args.args
        assert "agent.delete_events_matching" in sql
        assert params[0].obj == {"type": "x", "rule_id": 3}
        assert removed == 2

    async def test_insert_event_if_absent_passes_match_and_excluded_id(self, pool):
        pool._connection.execute.return_value.fetchone.return_value = (True,)
        vt = datetime.now(UTC)

        inserted = await insert_event_if_absent(
            pool, event={"type": "x"}, vt=vt, match={"type": "x"}, exclude_id=41
        )

        sql, params = pool._connection.execute.await_args.args
        assert "agent.insert_event_if_absent" in sql
        assert params[1] == vt and params[2].obj == {"type": "x"} and params[3] == 41
        assert inserted is True


class TestScheduledRuleUpsert:
    async def test_updates_an_existing_rule_and_keeps_its_prompt(self, pool):
        pool._cursor.fetchone.return_value = _rule_row(action_prompt="edited by hand")

        rule = await upsert_scheduled_rule(
            pool,
            name="triage-cse-feedback",
            owner_slack_id="U_OWNER",
            action_prompt="generated",
            period=timedelta(hours=24),
            channel="C_CHAN",
        )

        sql, params = pool._cursor.execute.await_args.args
        assert sql.lstrip().startswith("UPDATE")
        assert "action_prompt" not in sql
        assert params[0] == timedelta(hours=24)
        assert rule.action_prompt == "edited by hand"

    async def test_inserts_when_no_rule_of_that_name_exists(self, pool):
        pool._cursor.fetchone.side_effect = [None, _rule_row()]

        rule = await upsert_scheduled_rule(
            pool,
            name="triage-cse-feedback",
            owner_slack_id="U_OWNER",
            action_prompt="generated",
            period=timedelta(hours=168),
            channel="C_CHAN",
        )

        insert_sql, params = pool._cursor.execute.await_args_list[1].args
        assert insert_sql.lstrip().startswith("INSERT")
        assert params[2] == "schedule" and params[3] == "generated"
        assert rule.id == 3

    async def test_a_rule_without_a_period_does_not_repeat(self, pool):
        pool._cursor.fetchone.return_value = _rule_row(period=None, repeat=False)

        await upsert_scheduled_rule(
            pool,
            name="triage-cse-feedback",
            owner_slack_id="U_OWNER",
            action_prompt="generated",
            period=None,
            channel="C_CHAN",
        )

        _, params = pool._cursor.execute.await_args.args
        assert params[3] is False


class TestFeedbackRatings:
    async def test_window_query_filters_on_type_and_time(self, pool):
        since, until = (
            datetime(2026, 9, 1, tzinfo=UTC),
            datetime(2026, 9, 8, tzinfo=UTC),
        )
        pool._cursor.fetchall.return_value = [
            {
                "event_ts": since,
                "event": {"type": "agent_feedback_rating", "rating": "2", "user": "U1"},
            }
        ]

        rows = await list_feedback_ratings(pool, since, until)

        sql, params = pool._cursor.execute.await_args.args
        assert "event_hist" in sql and "event->>'type' = %s" in sql
        assert params[:3] == ("agent_feedback_rating", since, until)
        assert rows == [
            {
                "event_ts": since,
                "type": "agent_feedback_rating",
                "rating": "2",
                "user": "U1",
            }
        ]

    async def test_thread_query_filters_on_channel_and_message_ts(self, pool):
        await list_feedback_ratings_for_thread(pool, "C_CHAN", "1.1")

        sql, params = pool._cursor.execute.await_args.args
        assert "event->>'channel' = %s" in sql and "event->>'message_ts' = %s" in sql
        assert params == ("agent_feedback_rating", "C_CHAN", "1.1")
