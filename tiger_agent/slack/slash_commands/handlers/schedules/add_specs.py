from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.salesforce.types import UserDefinedRule
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules import add as add_module
from tiger_agent.slack.slash_commands.handlers.schedules.add import handle


def _rule(**overrides) -> UserDefinedRule:
    defaults = dict(
        id=3,
        name="triage-cse-feedback",
        owner_slack_id="U_USER",
        event_type="schedule",
        action_prompt="Discover and follow the skill `triage-cse-feedback`.",
        repeat=True,
        period=timedelta(hours=168),
        channel="C_CHAN",
    )
    return UserDefinedRule(**{**defaults, **overrides})


@pytest.fixture
def ctx(make_pool_mock, make_slack_command, make_bot_info):
    hctx = MagicMock()
    hctx.pool = make_pool_mock()
    return CommandContext(
        hctx=hctx, command=make_slack_command(), bot_info=make_bot_info()
    )


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    mocks = {
        "upsert_scheduled_rule": AsyncMock(return_value=_rule()),
        "delete_events_matching": AsyncMock(return_value=1),
        "insert_event": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(add_module, name, mock)
    return mocks


class TestSchedulesAdd:
    async def test_defaults_to_the_channel_the_command_was_typed_in(self, ctx, patched):
        result = await handle(ctx, ["triage-cse-feedback", "168"])

        assert patched["upsert_scheduled_rule"].await_args.kwargs["channel"] == "C_CHAN"
        assert "<#C_CHAN>" in result

    @pytest.mark.parametrize("arg", ["C0ABCDEF123", "<#C0ABCDEF123|ops>"])
    async def test_accepts_a_channel_id_or_mention(self, ctx, patched, arg):
        await handle(ctx, ["triage-cse-feedback", "24", arg])

        assert (
            patched["upsert_scheduled_rule"].await_args.kwargs["channel"]
            == "C0ABCDEF123"
        )

    async def test_queues_the_first_run_one_period_out(self, ctx, patched):
        await handle(ctx, ["triage-cse-feedback", "168"])

        kwargs = patched["insert_event"].await_args.kwargs
        assert kwargs["event"]["trigger"] == "schedule"
        assert kwargs["event"]["rule_id"] == 3
        assert kwargs["event"]["window_hours"] == 168.0
        expected = datetime.now(UTC) + timedelta(hours=168)
        assert abs((kwargs["vt"] - expected).total_seconds()) < 5

    async def test_replaces_any_previously_queued_run(self, ctx, patched):
        await handle(ctx, ["triage-cse-feedback", "168"])

        match = patched["delete_events_matching"].await_args.kwargs["match"]
        assert match["rule_id"] == 3 and match["trigger"] == "schedule"

    async def test_stores_a_prompt_that_names_the_skill(self, ctx, patched):
        await handle(ctx, ["triage-cse-feedback", "168"])

        prompt = patched["upsert_scheduled_rule"].await_args.kwargs["action_prompt"]
        assert "`triage-cse-feedback`" in prompt

    @pytest.mark.parametrize(
        "args, fragment",
        [
            (["Bad_Name", "24"], "skill name"),
            (["ok-skill", "zero"], "Hours must be"),
            (["ok-skill", "0"], "Hours must be"),
            (["ok-skill", "9999"], "Hours must be"),
            (["ok-skill", "24", "not-a-channel"], "channel"),
        ],
    )
    async def test_rejects_bad_arguments_without_touching_the_db(
        self, ctx, patched, args, fragment
    ):
        result = await handle(ctx, args)

        assert fragment in result
        patched["upsert_scheduled_rule"].assert_not_awaited()
        patched["insert_event"].assert_not_awaited()
