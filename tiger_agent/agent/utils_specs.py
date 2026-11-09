from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from tiger_agent.agent.constants import AGENT_MAX_DELEGATIONS, AGENT_MAX_REQUESTS
from tiger_agent.agent.limits import FINALIZE_PROMPT_INVESTIGATOR
from tiger_agent.agent.partial_agent import PartialAnswerAgent
from tiger_agent.agent.types import InvestigationReport
from tiger_agent.agent.utils import budget_capabilities, build_investigator


def _static_answer(_messages, _info) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="x")])


def _investigator():
    return build_investigator(
        model=FunctionModel(_static_answer), system_prompt="be thorough"
    )


class TestInvestigatorBudget:
    """The delegate's numbers must be the enforced ones, like the parent's."""

    def test_runs_on_its_own_request_budget(self):
        sub = _investigator()

        assert sub.usage_limits is not None
        assert sub.usage_limits.request_limit == AGENT_MAX_REQUESTS

    def test_delegations_per_run_are_capped(self):
        assert _investigator().max_calls == AGENT_MAX_DELEGATIONS

    def test_finalizes_instead_of_raising(self):
        sub = _investigator()

        assert isinstance(sub.agent, PartialAnswerAgent)
        assert sub.agent.finalize_prompt == FINALIZE_PROMPT_INVESTIGATOR

    def test_reports_structured_output_under_its_original_name(self):
        sub = _investigator()

        assert sub.resolved_name == "investigator"
        assert sub.agent.output_type is InvestigationReport


class TestBudgetCapabilities:
    """Coordinator and delegates use the same builder, but the capabilities
    hold per-agent state, so each call must hand out its own instances."""

    def test_each_call_returns_fresh_instances(self):
        first, second = budget_capabilities(), budget_capabilities()

        assert len(first) == len(second) == 2
        assert all(a is not b for a, b in zip(first, second, strict=True))


# --- create_agent_and_context: destination and audience come from the event ---

from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from tiger_agent.agent import utils as utils_module  # noqa: E402
from tiger_agent.agent.types import LinkedChannelInfo  # noqa: E402
from tiger_agent.agent.utils import create_agent_and_context  # noqa: E402
from tiger_agent.customer.types import CustomerQuestionEvent  # noqa: E402
from tiger_agent.salesforce.types import SalesforceAssignmentChangedEvent  # noqa: E402
from tiger_agent.slack import utils as slack_utils  # noqa: E402
from tiger_agent.slack.types import (  # noqa: E402
    SlackAppMentionEvent,
    SlackMessageEvent,
    UserProfile,
)
from tiger_agent.slack.utils import (  # noqa: E402
    slack_profile_has_internal_email_domain,
)
from tiger_agent.tasks.types import Task  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _task(event) -> Task:
    return Task(id=1, event_ts=NOW, attempts=0, vt=NOW, claimed=[], event=event)


def _slack_task(channel="C_CHAN", thread_ts=None) -> Task:
    return _task(
        SlackAppMentionEvent(
            ts="1700000000.000100",
            event_ts="1700000000.000100",
            thread_ts=thread_ts,
            text="<@U_BOT> hi",
            channel=channel,
            user="U_USER",
        )
    )


def _channel(**overrides) -> LinkedChannelInfo:
    return LinkedChannelInfo(id="C_CHAN", name="general", **overrides)


@pytest.fixture
def wrapper(monkeypatch):
    """Stub everything around create_agent_and_context and capture what it hands
    the core: the decision under test is the include_internal_mcps flag and the tools."""
    stubs = SimpleNamespace(
        fetch_linked_channel_info=AsyncMock(return_value=_channel()),
        build=AsyncMock(return_value="built"),
        create_tools=MagicMock(return_value=["tool"]),
        fetch_user_info=AsyncMock(return_value=None),
        fetch_thread_messages=AsyncMock(return_value=[]),
        channel_has_limited_access=AsyncMock(return_value=False),
        # True is the unset-INTERNAL_DOMAIN default: the predicate vouches for
        # everyone, so the channel checks alone decide.
        slack_profile_has_internal_email_domain=MagicMock(return_value=True),
    )
    monkeypatch.setattr(
        utils_module, "fetch_linked_channel_info", stubs.fetch_linked_channel_info
    )
    monkeypatch.setattr(utils_module, "build_agent_and_context", stubs.build)
    monkeypatch.setattr(utils_module, "create_tools", stubs.create_tools)
    monkeypatch.setattr(utils_module, "fetch_user_info", stubs.fetch_user_info)
    monkeypatch.setattr(
        utils_module, "fetch_thread_messages", stubs.fetch_thread_messages
    )
    monkeypatch.setattr(
        utils_module, "channel_has_limited_access", stubs.channel_has_limited_access
    )
    monkeypatch.setattr(
        utils_module,
        "slack_profile_has_internal_email_domain",
        stubs.slack_profile_has_internal_email_domain,
    )
    return stubs


@pytest.fixture
def hctx():
    ctx = MagicMock()
    ctx.bot_info = MagicMock()
    return ctx


class TestCreateAgentAndContext:
    async def test_slack_event_in_an_internal_channel_may_use_internal_servers(
        self, wrapper, hctx
    ):
        result = await create_agent_and_context(
            hctx=hctx, task=_slack_task(), agent=MagicMock()
        )

        assert result == "built"
        wrapper.fetch_linked_channel_info.assert_awaited_once()
        assert wrapper.fetch_linked_channel_info.await_args.kwargs["channel_id"] == (
            "C_CHAN"
        )
        kwargs = wrapper.build.await_args.kwargs
        assert kwargs["include_internal_mcps"] is True
        assert kwargs["tools"] == ["tool"]
        assert wrapper.create_tools.call_args.kwargs["channel_info"] == _channel()

    async def test_slack_event_in_a_shared_channel_is_external(self, wrapper, hctx):
        wrapper.fetch_linked_channel_info.return_value = _channel(is_ext_shared=True)

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False

    async def test_slack_event_in_a_limited_channel_may_not_use_internal_servers(
        self, wrapper, hctx
    ):
        wrapper.channel_has_limited_access.return_value = True

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.channel_has_limited_access.await_args.kwargs["channel_id"] == (
            "C_CHAN"
        )
        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False

    async def test_customer_question_does_not_check_the_limited_channel_table(
        self, wrapper, hctx
    ):
        task = _task(CustomerQuestionEvent(text="How do I add a retention policy?"))

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        wrapper.channel_has_limited_access.assert_not_awaited()

    async def test_customer_question_is_external_and_has_no_slack_side(
        self, wrapper, hctx
    ):
        task = _task(CustomerQuestionEvent(text="How do I add a retention policy?"))

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        wrapper.fetch_linked_channel_info.assert_not_awaited()
        wrapper.fetch_user_info.assert_not_awaited()
        kwargs = wrapper.build.await_args.kwargs
        assert kwargs["include_internal_mcps"] is False
        assert kwargs["user"] is None
        assert wrapper.create_tools.call_args.kwargs["channel_info"] is None

    async def test_assignment_event_uses_the_channel_it_was_enqueued_with(
        self, wrapper, hctx
    ):
        task = _task(
            SalesforceAssignmentChangedEvent(
                case={"Id": "500", "CaseNumber": "1"}, destination_channel="C_CASES"
            )
        )

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        assert wrapper.fetch_linked_channel_info.await_args.kwargs["channel_id"] == (
            "C_CASES"
        )
        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is True
        wrapper.fetch_user_info.assert_not_awaited()

    async def test_an_event_without_a_slack_user_skips_the_email_domain_check(
        self, wrapper, hctx
    ):
        # With INTERNAL_DOMAIN set the predicate is False for a missing user, but a
        # Salesforce event has no asker to check: the channel alone decides.
        wrapper.slack_profile_has_internal_email_domain.return_value = False
        task = _task(
            SalesforceAssignmentChangedEvent(
                case={"Id": "500", "CaseNumber": "1"}, destination_channel="C_CASES"
            )
        )

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        wrapper.slack_profile_has_internal_email_domain.assert_not_called()
        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is True
        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is True

    @pytest.fixture
    def dm_from(self, wrapper, monkeypatch, make_user_info):
        """A DM from a user with the given profile email, decided by the real
        email-domain predicate with INTERNAL_DOMAIN set."""
        monkeypatch.setattr(slack_utils, "INTERNAL_DOMAIN", "tigerdata.com")
        monkeypatch.setattr(
            utils_module,
            "slack_profile_has_internal_email_domain",
            slack_profile_has_internal_email_domain,
        )
        wrapper.fetch_linked_channel_info.return_value = LinkedChannelInfo(
            id="D_DM", is_im=True
        )

        def _dm_from(email: str) -> Task:
            wrapper.fetch_user_info.return_value = make_user_info(
                profile=UserProfile(email=email)
            )
            return _task(
                SlackMessageEvent(
                    ts="1700000000.000100",
                    event_ts="1700000000.000100",
                    text="hi",
                    channel="D_DM",
                    user="U_USER",
                )
            )

        return _dm_from

    async def test_a_dm_from_a_user_on_the_internal_domain_gets_the_full_toolset(
        self, wrapper, hctx, dm_from
    ):
        task = dm_from("alice@tigerdata.com")

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is True
        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is True

    async def test_a_dm_from_a_user_off_the_internal_domain_gets_the_reduced_toolset(
        self, wrapper, hctx, dm_from
    ):
        task = dm_from("alice@gmail.com")

        await create_agent_and_context(hctx=hctx, task=task, agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False
        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is False

    async def test_a_vouched_for_user_may_use_internal_servers(self, wrapper, hctx):
        # True is also what the predicate returns when INTERNAL_DOMAIN is unset.
        wrapper.slack_profile_has_internal_email_domain.return_value = True

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is True

    async def test_a_user_off_the_internal_domain_may_not(self, wrapper, hctx):
        wrapper.slack_profile_has_internal_email_domain.return_value = False

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False

    async def test_the_same_decision_gates_the_local_tools(self, wrapper, hctx):
        # Servers and tools must not disagree about what the run may reach.
        wrapper.slack_profile_has_internal_email_domain.return_value = False

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is False

    async def test_a_full_run_gets_the_full_toolset(self, wrapper, hctx):
        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is True

    async def test_a_limited_channel_still_wins_over_an_internal_user(
        self, wrapper, hctx
    ):
        wrapper.slack_profile_has_internal_email_domain.return_value = True
        wrapper.channel_has_limited_access.return_value = True

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False

    async def test_an_external_channel_still_wins_over_an_internal_user(
        self, wrapper, hctx
    ):
        wrapper.slack_profile_has_internal_email_domain.return_value = True
        wrapper.fetch_linked_channel_info.return_value = _channel(is_ext_shared=True)

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        assert wrapper.build.await_args.kwargs["include_internal_mcps"] is False

    async def test_the_user_is_fetched_before_the_access_decision(self, wrapper, hctx):
        # The gate reads the asker's profile, so the fetch must precede it.
        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        wrapper.fetch_user_info.assert_awaited_once()
        passed = wrapper.slack_profile_has_internal_email_domain.call_args.args[0]
        assert passed is wrapper.fetch_user_info.return_value

    async def test_unreadable_destination_channel_still_answers(self, wrapper, hctx):
        wrapper.fetch_linked_channel_info.return_value = None

        result = await create_agent_and_context(
            hctx=hctx, task=_slack_task(), agent=MagicMock()
        )

        assert result == "built"

    async def test_unreadable_destination_channel_is_not_trusted(self, wrapper, hctx):
        # The bot could not confirm the channel is internal, so it does not get
        # the benefit of the doubt -- it is treated like an external one.
        wrapper.fetch_linked_channel_info.return_value = None
        wrapper.slack_profile_has_internal_email_domain.return_value = True

        await create_agent_and_context(hctx=hctx, task=_slack_task(), agent=MagicMock())

        kwargs = wrapper.build.await_args.kwargs
        assert kwargs["include_internal_mcps"] is False
        assert wrapper.create_tools.call_args.kwargs["use_full_toolset"] is False

    async def test_thread_history_is_passed_as_extra_context(self, wrapper, hctx):
        wrapper.fetch_thread_messages.return_value = []

        await create_agent_and_context(
            hctx=hctx,
            task=_slack_task(thread_ts="1700000000.000001"),
            agent=MagicMock(),
        )

        wrapper.fetch_thread_messages.assert_awaited_once()
        assert "thread_history" in wrapper.build.await_args.kwargs["extra_context"]
