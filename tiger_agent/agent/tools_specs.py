from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.agent import tools as tools_module
from tiger_agent.agent.tools import create_tools
from tiger_agent.salesforce.types import (
    SalesforceCreateNewCaseEvent,
    UserDefinedRuleExecution,
)
from tiger_agent.slack.types import ChannelInfo, SlackAppMentionEvent
from tiger_agent.tasks.types import Task

ASSESSMENT_TOOLS = {"get_thread_assessment_context", "list_recent_feedback"}
RULE_ACTION_TOOLS = {"send_dm", "send_channel_message", "get_case_url"}


def _make_rule_execution_task(window_hours: float | None = 168.0) -> Task:
    return _make_task(
        UserDefinedRuleExecution(
            rule_id=3,
            rule_name="triage-cse-feedback",
            owner_slack_id="U_OWNER",
            trigger="schedule",
            channel="C_OUT",
            window_hours=window_hours,
        )
    )


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _make_channel_info(**overrides) -> ChannelInfo:
    defaults: dict = {"id": "C_CHAN", "name": "general"}
    defaults.update(overrides)
    return ChannelInfo(**defaults)


def _make_task(event) -> Task:
    return Task(
        id=1,
        event_ts=datetime(2026, 1, 1, tzinfo=UTC),
        attempts=0,
        vt=datetime(2026, 1, 1, tzinfo=UTC),
        claimed=[],
        event=event,
    )


def _make_slack_task() -> Task:
    return _make_task(
        SlackAppMentionEvent(
            ts="1700000000.000100",
            text="<@U_BOT> hi",
            channel="C_CHAN",
            event_ts="1700000000.000100",
            user="U_USER",
        )
    )


def _make_non_slack_task() -> Task:
    return _make_task(
        SalesforceCreateNewCaseEvent(
            subject="disk full",
            description="prod is down",
            user="U_USER",
            channel="C_CHAN",
            severity="High",
            project_id=None,
            service_id=None,
        )
    )


@pytest.fixture
def hctx():
    return MagicMock()


@pytest.fixture(autouse=True)
def disable_feature_flags(monkeypatch):
    """Default the optional tool groups off so a baseline test knows what to expect.

    Individual tests re-patch these to True to exercise the enabled paths.
    """
    monkeypatch.setattr(tools_module, "USER_DEFINED_EVENTS_ENABLED", False)
    monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "")


class TestCreateToolsExternalChannel:
    def test_ext_shared_channel_linked_to_sf_returns_only_case_form_tool(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=True, is_shared=False)
        tool_list = create_tools(
            hctx,
            _make_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=True,
        )
        assert [t.name for t in tool_list] == ["show_salesforce_case_form"]

    def test_shared_channel_linked_to_sf_returns_only_case_form_tool(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=False, is_shared=True)
        tool_list = create_tools(
            hctx,
            _make_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=True,
        )
        assert [t.name for t in tool_list] == ["show_salesforce_case_form"]

    def test_ext_shared_channel_not_linked_returns_no_tools(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=True, is_shared=False)
        tool_list = create_tools(
            hctx,
            _make_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=False,
        )
        assert tool_list == []

    def test_shared_channel_not_linked_returns_no_tools(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=False, is_shared=True)
        tool_list = create_tools(
            hctx,
            _make_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=False,
        )
        assert tool_list == []

    def test_external_channel_defaults_to_no_tools_when_link_flag_omitted(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=True)
        tool_list = create_tools(hctx, _make_slack_task(), channel_info)
        assert tool_list == []

    def test_external_channel_ignores_feature_flags(self, hctx, monkeypatch):
        monkeypatch.setattr(tools_module, "USER_DEFINED_EVENTS_ENABLED", True)
        monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "token")
        channel_info = _make_channel_info(is_ext_shared=True)
        tool_list = create_tools(
            hctx,
            _make_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=True,
        )
        assert [t.name for t in tool_list] == ["show_salesforce_case_form"]

    def test_external_channel_with_non_slack_event_still_returns_only_case_form(
        self, hctx
    ):
        channel_info = _make_channel_info(is_ext_shared=True)
        tool_list = create_tools(
            hctx,
            _make_non_slack_task(),
            channel_info,
            channel_is_linked_to_salesforce_account=True,
        )
        assert [t.name for t in tool_list] == ["show_salesforce_case_form"]


class TestCreateToolsInternalChannel:
    def test_internal_slack_event_returns_base_tool_set(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=False, is_shared=False)
        names = [t.name for t in create_tools(hctx, _make_slack_task(), channel_info)]
        assert "show_salesforce_case_form" not in names
        assert "download_slack_hosted_file" in names
        assert "download_salesforce_hosted_file" in names
        assert "get_org_calendar_events" in names
        assert "attach_file_to_slack_thread" in names
        assert "attach_salesforce_hosted_file_to_slack_thread" in names
        assert "get_user_ids_in_user_group" in names
        assert "get_user_ids_in_channel" in names

    def test_internal_channel_ignores_salesforce_link_flag(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=False, is_shared=False)
        linked_names = [
            t.name
            for t in create_tools(
                hctx,
                _make_slack_task(),
                channel_info,
                channel_is_linked_to_salesforce_account=True,
            )
        ]
        unlinked_names = [
            t.name
            for t in create_tools(
                hctx,
                _make_slack_task(),
                channel_info,
                channel_is_linked_to_salesforce_account=False,
            )
        ]
        assert linked_names == unlinked_names
        assert "show_salesforce_case_form" not in linked_names

    def test_user_defined_rule_tools_gated_off_by_default(self, hctx):
        channel_info = _make_channel_info()
        names = [t.name for t in create_tools(hctx, _make_slack_task(), channel_info)]
        assert "list_user_defined_rules" not in names
        assert "delete_user_defined_rule" not in names
        assert "create_user_defined_rule" not in names

    def test_user_defined_rule_tools_included_when_flag_on(self, hctx, monkeypatch):
        monkeypatch.setattr(tools_module, "USER_DEFINED_EVENTS_ENABLED", True)
        channel_info = _make_channel_info()
        names = [t.name for t in create_tools(hctx, _make_slack_task(), channel_info)]
        assert "list_user_defined_rules" in names
        assert "delete_user_defined_rule" in names
        assert "create_user_defined_rule" in names

    def test_logfire_tools_gated_off_by_default(self, hctx):
        channel_info = _make_channel_info()
        names = [t.name for t in create_tools(hctx, _make_slack_task(), channel_info)]
        assert "get_tool_calls_for_event" not in names
        assert "get_logs_for_event" not in names
        assert "get_log_by_id" not in names
        assert "find_errors" not in names

    def test_logfire_tools_included_when_token_set(self, hctx, monkeypatch):
        monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "some-token")
        channel_info = _make_channel_info()
        names = [t.name for t in create_tools(hctx, _make_slack_task(), channel_info)]
        assert "get_tool_calls_for_event" in names
        assert "get_logs_for_event" in names
        assert "get_log_by_id" in names
        assert "find_errors" in names

    def test_non_slack_event_omits_slack_event_dependent_tools(self, hctx, monkeypatch):
        monkeypatch.setattr(tools_module, "USER_DEFINED_EVENTS_ENABLED", True)
        monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "some-token")
        channel_info = _make_channel_info()
        names = [
            t.name for t in create_tools(hctx, _make_non_slack_task(), channel_info)
        ]
        # The always-on, event-agnostic tools remain.
        assert names == [
            "download_slack_hosted_file",
            "download_salesforce_hosted_file",
            "get_org_calendar_events",
        ]


class TestRuleExecutionAndAssessmentTools:
    def test_slack_events_get_assessment_and_toggle_tools(self, hctx):
        names = {
            t.name for t in create_tools(hctx, _make_slack_task(), _make_channel_info())
        }
        assert names >= ASSESSMENT_TOOLS
        assert "toggle_user_defined_rule" in names
        assert not (RULE_ACTION_TOOLS & names)

    def test_rule_executions_get_action_and_assessment_tools_only(self, hctx):
        names = {
            t.name
            for t in create_tools(
                hctx, _make_rule_execution_task(), _make_channel_info()
            )
        }
        assert names >= RULE_ACTION_TOOLS and names >= ASSESSMENT_TOOLS
        # Slack-only tools need a requesting user and a thread; a rule run has neither.
        assert "attach_file_to_slack_thread" not in names
        assert "toggle_user_defined_rule" not in names

    def test_salesforce_events_get_neither(self, hctx):
        names = {
            t.name
            for t in create_tools(hctx, _make_non_slack_task(), _make_channel_info())
        }
        assert not (ASSESSMENT_TOOLS & names) and not (RULE_ACTION_TOOLS & names)

    async def test_slack_callers_must_be_admins(self, hctx, monkeypatch):
        monkeypatch.setattr(
            tools_module, "user_is_admin", AsyncMock(return_value=False)
        )
        tools = create_tools(hctx, _make_slack_task(), _make_channel_info())

        context = await _tool(tools, "get_thread_assessment_context").function()
        feedback = await _tool(tools, "list_recent_feedback").function(window_hours=24)

        assert context == "This tool can only be used by admins."
        assert feedback == "This tool can only be used by admins."

    async def test_list_recent_feedback_groups_by_thread_worst_first(
        self, hctx, monkeypatch
    ):
        rows = [
            {
                "channel": "C1",
                "message_ts": "1.1",
                "user": "U1",
                "rating": "5",
                "description": "good",
                "event_ts": "t1",
            },
            {
                "channel": "C1",
                "message_ts": "2.2",
                "user": "U2",
                "rating": "1",
                "description": "wrong",
                "event_ts": "t2",
            },
            {
                "channel": "C1",
                "message_ts": "2.2",
                "user": "U3",
                "rating": "3",
                "description": "meh",
                "event_ts": "t3",
            },
        ]
        monkeypatch.setattr(
            tools_module, "list_feedback_ratings", AsyncMock(return_value=rows)
        )
        hctx.app.client.chat_getPermalink = AsyncMock(side_effect=Exception("no perm"))
        hctx.bot_info.url = "https://acme.slack.com/"
        tools = create_tools(hctx, _make_rule_execution_task(), _make_channel_info())

        result = await _tool(tools, "list_recent_feedback").function()

        assert [t["thread_ts"] for t in result] == ["2.2", "1.1"]
        assert len(result[0]["comments"]) == 2
        assert result[0]["permalink"] == "https://acme.slack.com/archives/C1/p22"
        # The scheduled run's window is the default.
        since = tools_module.list_feedback_ratings.await_args.kwargs["since"]
        until = tools_module.list_feedback_ratings.await_args.kwargs["until"]
        assert (until - since).total_seconds() == pytest.approx(168 * 3600)

    async def test_list_recent_feedback_needs_a_window_outside_a_scheduled_run(
        self, hctx, monkeypatch
    ):
        monkeypatch.setattr(tools_module, "user_is_admin", AsyncMock(return_value=True))
        tools = create_tools(hctx, _make_slack_task(), _make_channel_info())

        result = await _tool(tools, "list_recent_feedback").function()

        assert result.startswith("Pass window_hours")

    async def test_thread_context_defaults_to_the_current_thread_and_names_gaps(
        self, hctx, monkeypatch
    ):
        monkeypatch.setattr(tools_module, "user_is_admin", AsyncMock(return_value=True))
        monkeypatch.setattr(
            tools_module, "fetch_thread_messages", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(
            tools_module, "list_feedback_ratings_for_thread", AsyncMock(return_value=[])
        )
        tools = create_tools(hctx, _make_slack_task(), _make_channel_info())

        result = await _tool(tools, "get_thread_assessment_context").function()

        fetch = tools_module.fetch_thread_messages.await_args.kwargs
        assert (
            fetch["channel"] == "C_CHAN" and fetch["thread_ts"] == "1700000000.000100"
        )
        assert result["drafting_run"] is None
        assert any("No feedback ratings" in g for g in result["gaps"])
        assert any("Logfire" in g for g in result["gaps"])

    async def test_thread_context_parses_a_permalink_and_reads_the_drafting_run(
        self, hctx, monkeypatch
    ):
        monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "token")
        monkeypatch.setattr(
            tools_module, "fetch_thread_messages", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(
            tools_module,
            "list_feedback_ratings_for_thread",
            AsyncMock(return_value=[{"rating": "3"}]),
        )
        runs = [
            {"trace_id": "01a09141ae6c663a5be0ebffb00f8fe0", "handler": "X.handle"},
            {"trace_id": "01a09159baacf7eccf56c40f2849f0b1", "handler": "Y.handle"},
        ]
        monkeypatch.setattr(
            tools_module, "find_drafting_traces", AsyncMock(return_value=runs)
        )
        monkeypatch.setattr(
            tools_module,
            "get_tool_calls_for_traces",
            AsyncMock(return_value=[{"tool_name": "t", "tool_response": "x" * 1000}]),
        )
        tools = create_tools(hctx, _make_rule_execution_task(), _make_channel_info())

        result = await _tool(tools, "get_thread_assessment_context").function(
            "https://acme.slack.com/archives/C0AK9P2V1LK/p1789143575870009?thread_ts=1789143575.595509"
        )

        assert result["channel"] == "C0AK9P2V1LK"
        assert result["thread_ts"] == "1789143575.595509"
        assert result["drafting_run"]["trace_id"] == "01a09141ae6c663a5be0ebffb00f8fe0"
        assert result["other_runs_in_thread"] == runs[1:]
        assert len(result["drafting_run_tool_calls"][0]["tool_response"]) < 600
        assert result["gaps"] == []
