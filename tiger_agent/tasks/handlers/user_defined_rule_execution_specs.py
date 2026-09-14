from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.agent.types import AssessmentReport
from tiger_agent.salesforce.types import UserDefinedRule, UserDefinedRuleExecution
from tiger_agent.tasks.handlers import user_defined_rule_execution as handler_module
from tiger_agent.tasks.handlers.user_defined_rule_execution import (
    UserDefinedRuleExecutionHandler,
    schedule_match,
)
from tiger_agent.tasks.types import Task

PERIOD = timedelta(hours=168)


def _rule(**overrides) -> UserDefinedRule:
    defaults = dict(
        id=7,
        name="triage-cse-feedback",
        owner_slack_id="U_OWNER",
        event_type="schedule",
        action_prompt="Discover and follow the skill `triage-cse-feedback`.",
        repeat=True,
        period=PERIOD,
        channel="C_OUT",
        execution_profile="full",
    )
    return UserDefinedRule(**{**defaults, **overrides})


def _task(**overrides) -> Task:
    defaults = dict(
        rule_id=7,
        rule_name="triage-cse-feedback",
        owner_slack_id="U_OWNER",
        trigger="schedule",
        channel="C_OUT",
        window_hours=168.0,
    )
    return Task(
        id=41,
        event_ts=datetime(2026, 1, 1, tzinfo=UTC),
        attempts=1,
        vt=datetime(2026, 1, 1, tzinfo=UTC),
        claimed=[],
        event=UserDefinedRuleExecution(**{**defaults, **overrides}),
    )


@pytest.fixture
def hctx():
    hctx = MagicMock()
    hctx.app.client = MagicMock()
    hctx.pool = MagicMock()
    return hctx


@pytest.fixture
def calls():
    return []


@pytest.fixture(autouse=True)
def patched(monkeypatch, calls):
    """Record the order collaborators run in; the re-arm must come first."""

    def recording(name, **kwargs):
        mock = AsyncMock(**kwargs)
        mock.side_effect = _record(name, calls, kwargs.get("return_value"))
        return mock

    header = MagicMock()
    header.data = {"ts": "1700000000.000200"}
    run_result = MagicMock()
    run_result.output = AssessmentReport(
        summary="12 threads assessed", report_markdown="# Report"
    )

    mocks = {
        "get_user_defined_rule": recording("get_rule", return_value=_rule()),
        "insert_event_if_absent": recording("rearm", return_value=True),
        "create_agent_and_context": recording("build_agent", return_value=MagicMock()),
        "run_and_return_partial": recording("run", return_value=run_result),
        "post_response": recording("post", return_value=header),
        "publish_canvas_in_thread": recording("canvas", return_value="https://c"),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(handler_module, name, mock)
    return mocks


def _record(name, calls, return_value):
    async def side_effect(*args, **kwargs):
        calls.append(name)
        return return_value

    return side_effect


class TestRearming:
    async def test_rearms_before_running_the_agent(self, hctx, patched, calls):
        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        assert calls.index("rearm") < calls.index("build_agent")
        kwargs = patched["insert_event_if_absent"].await_args.kwargs
        assert kwargs["match"] == schedule_match(7)
        assert kwargs["exclude_id"] == 41
        assert abs((kwargs["vt"] - (datetime.now(UTC) + PERIOD)).total_seconds()) < 5
        assert kwargs["event"]["trigger"] == "schedule"
        assert kwargs["event"]["window_hours"] == 168.0

    async def test_rearms_even_when_the_run_fails(self, hctx, patched):
        patched["run_and_return_partial"].side_effect = RuntimeError("model down")

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        patched["insert_event_if_absent"].assert_awaited_once()
        # The failure is reported, not re-raised: a retry would re-run the LLM job.
        text = patched["post_response"].await_args.kwargs["text"]
        assert "failed to run" in text and "model down" in text

    @pytest.mark.parametrize(
        "rule_override, task_override",
        [
            ({"repeat": False}, {}),
            ({}, {"reschedule": False}),
            ({}, {"trigger": "event", "matched_event": {"type": "message"}}),
        ],
    )
    async def test_does_not_rearm_when_not_a_repeating_schedule(
        self, hctx, patched, rule_override, task_override
    ):
        patched["get_user_defined_rule"].side_effect = None
        patched["get_user_defined_rule"].return_value = _rule(**rule_override)

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task(**task_override)
        )

        patched["insert_event_if_absent"].assert_not_awaited()

    @pytest.mark.parametrize("rule", [None, _rule(enabled=False)])
    async def test_missing_or_disabled_rule_does_nothing(self, hctx, patched, rule):
        patched["get_user_defined_rule"].side_effect = None
        patched["get_user_defined_rule"].return_value = rule

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        patched["insert_event_if_absent"].assert_not_awaited()
        patched["create_agent_and_context"].assert_not_awaited()
        patched["post_response"].assert_not_awaited()


class TestDelivery:
    async def test_schedule_posts_summary_then_canvas_under_it(self, hctx, patched):
        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        post = patched["post_response"].await_args_list[0].kwargs
        assert post["channel"] == "C_OUT"
        assert post["thread_ts"] is None
        assert post["text"] == "12 threads assessed"
        canvas = patched["publish_canvas_in_thread"].await_args.kwargs
        assert canvas["thread_ts"] == "1700000000.000200"
        assert canvas["markdown"] == "# Report"
        assert canvas["title"].startswith("triage-cse-feedback — ")

    async def test_falls_back_to_a_markdown_file_when_the_canvas_fails(
        self, hctx, patched
    ):
        patched["publish_canvas_in_thread"].side_effect = RuntimeError("missing_scope")

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        fallback = patched["post_response"].await_args_list[-1].kwargs
        assert fallback["thread_ts"] == "1700000000.000200"
        [attachment] = fallback["file_attachments"]
        assert attachment.name.endswith(".md")
        assert attachment.body == b"# Report"

    async def test_event_trigger_posts_plain_text(self, hctx, patched):
        patched["run_and_return_partial"].side_effect = None
        result = MagicMock()
        result.output = "Heads up: Sev-1 landed."
        patched["run_and_return_partial"].return_value = result

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task(trigger="event", matched_event={"type": "salesforce_event"})
        )

        patched["publish_canvas_in_thread"].assert_not_awaited()
        assert (
            patched["post_response"].await_args.kwargs["text"]
            == "Heads up: Sev-1 landed."
        )

    async def test_passes_the_rules_profile_and_destination_to_the_agent(
        self, hctx, patched
    ):
        patched["get_user_defined_rule"].side_effect = None
        patched["get_user_defined_rule"].return_value = _rule(
            execution_profile="limited"
        )

        await UserDefinedRuleExecutionHandler(hctx=hctx, agent=MagicMock()).handle(
            _task()
        )

        kwargs = patched["create_agent_and_context"].await_args.kwargs
        assert kwargs["profile"] == "limited"
        assert kwargs["channel_to_respond"] == "C_OUT"
        assert kwargs["extra_context"]["rule"].id == 7
