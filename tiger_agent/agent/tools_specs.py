from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tiger_agent.agent import tools as tools_module
from tiger_agent.agent.tools import create_tools
from tiger_agent.salesforce.types import SalesforceCreateNewCaseEvent
from tiger_agent.slack.types import ChannelInfo, SlackAppMentionEvent
from tiger_agent.tasks.types import Task


def _make_channel_info(**overrides) -> ChannelInfo:
    defaults: dict = {"id": "C_CHAN", "name": "general"}
    defaults.update(overrides)
    return ChannelInfo(**defaults)


def _make_task(event) -> Task:
    return Task(
        id=1,
        event_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempts=0,
        vt=datetime(2026, 1, 1, tzinfo=timezone.utc),
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
    def test_ext_shared_channel_returns_only_case_form_tool(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=True, is_shared=False)
        tool_list = create_tools(hctx, _make_slack_task(), channel_info)
        assert len(tool_list) == 1
        assert tool_list[0].name == "show_salesforce_case_form"

    def test_shared_channel_returns_only_case_form_tool(self, hctx):
        channel_info = _make_channel_info(is_ext_shared=False, is_shared=True)
        tool_list = create_tools(hctx, _make_slack_task(), channel_info)
        assert len(tool_list) == 1
        assert tool_list[0].name == "show_salesforce_case_form"

    def test_external_channel_ignores_feature_flags(self, hctx, monkeypatch):
        monkeypatch.setattr(tools_module, "USER_DEFINED_EVENTS_ENABLED", True)
        monkeypatch.setattr(tools_module, "LOGFIRE_READ_TOKEN", "token")
        channel_info = _make_channel_info(is_ext_shared=True)
        tool_list = create_tools(hctx, _make_slack_task(), channel_info)
        assert [t.name for t in tool_list] == ["show_salesforce_case_form"]

    def test_external_channel_with_non_slack_event_still_returns_only_case_form(
        self, hctx
    ):
        channel_info = _make_channel_info(is_ext_shared=True)
        tool_list = create_tools(hctx, _make_non_slack_task(), channel_info)
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
        assert "get_user_ids_in_user_group" in names
        assert "get_user_ids_in_channel" in names

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
        names = [t.name for t in create_tools(hctx, _make_non_slack_task(), channel_info)]
        # The always-on, event-agnostic tools remain.
        assert names == [
            "download_slack_hosted_file",
            "download_salesforce_hosted_file",
            "get_org_calendar_events",
        ]
