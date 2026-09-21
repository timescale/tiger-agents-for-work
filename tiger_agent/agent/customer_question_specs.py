"""A CustomerQuestionEvent runs through the same builder as every other event,
on its own prompt branch, with no Slack sections and no internal servers."""

from datetime import UTC, datetime

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import AgentResponseContext
from tiger_agent.agent.utils import build_agent_and_context
from tiger_agent.customer.types import CustomerQuestionEvent
from tiger_agent.slack.types import BotInfo, SlackAppMentionEvent
from tiger_agent.tasks.types import Task

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BOT = BotInfo(
    url="https://example.slack.com/",
    team="Example",
    team_id="T_1",
    bot_id="B_1",
    name="eon",
    app_id="A_1",
    user_id="U_BOT",
)
QUESTION = "How do I add a retention policy to a hypertable?"


def _task(event) -> Task:
    return Task(id=1, event_ts=NOW, attempts=0, vt=NOW, claimed=[], event=event)


def _customer_task() -> Task:
    return _task(
        CustomerQuestionEvent(
            text=QUESTION,
            subject="Retention",
            platform="Tiger Cloud",
            product_area="Data lifecycle",
            source="salesforce_case",
            source_id="500",
        )
    )


def _text(prompt) -> str:
    return prompt if isinstance(prompt, str) else "\n".join(str(p) for p in prompt)


class TestCustomerQuestionPrompts:
    async def test_system_prompt_takes_the_customer_branch(self):
        agent = TigerAgent(model="test")
        ctx = AgentResponseContext(
            task=_customer_task(), mention=_customer_task().event, bot=BOT
        )

        system = _text(await agent.make_system_prompt(ctx=ctx))

        assert "support engineer answering a customer's question" in system
        assert "Customer Question Protocol" in system
        assert "Slack Mention Formatting" not in system
        assert "answers questions posed to you in Slack" not in system
        assert "Salesforce Support Case Triage" not in system

    async def test_user_prompt_carries_the_case_context_and_the_question(self):
        agent = TigerAgent(model="test")
        ctx = AgentResponseContext(
            task=_customer_task(), mention=_customer_task().event, bot=BOT
        )

        user = _text(await agent.make_user_prompt(ctx=ctx))

        assert "A customer has asked a question" in user
        assert "- Subject: Retention" in user
        assert "- Platform: Tiger Cloud" in user
        assert "- Product area: Data lifecycle" in user
        assert QUESTION in user
        assert "A Slack user has sent you a prompt" not in user

    async def test_slack_events_keep_the_slack_sections(self):
        agent = TigerAgent(model="test")
        task = _task(
            SlackAppMentionEvent(
                ts="1", event_ts="1", text="<@U_BOT> hi", channel="C_CHAN", user="U_1"
            )
        )
        ctx = AgentResponseContext(task=task, mention=task.event, bot=BOT)

        system = _text(await agent.make_system_prompt(ctx=ctx))

        assert "Slack Mention Formatting" in system
        assert "Customer Question Protocol" not in system


class TestBuildAgentAndContextForACustomerQuestion:
    async def test_builds_without_slack_or_a_database(self):
        built = await build_agent_and_context(
            agent=TigerAgent(model="test"),
            task=_customer_task(),
            bot=BOT,
            internal_only=False,
        )

        assert built.ctx.user is None
        assert built.ctx.mention.type == "customer_question"
        assert QUESTION in _text(built.user_prompt)
        assert built.agent.output_type is str
