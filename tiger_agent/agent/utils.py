from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.messages import UserContent
from pydantic_ai.models.anthropic import AnthropicModel

from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
)
from tiger_agent.db.utils import (
    delete_user_defined_rule,
    insert_user_defined_rule,
    list_user_defined_rules,
)
from tiger_agent.events import EVENT_TYPE_REGISTRY
from tiger_agent.mcp.utils import filter_mcp_servers
from tiger_agent.prompts.utils import format_thread_history
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
    UserDefinedRule,
    UserDefinedRuleMatch,
)
from tiger_agent.slack.types import SlackBaseEvent, SlackFile
from tiger_agent.slack.utils import (
    download_slack_hosted_file,
    fetch_bot_info,
    fetch_thread_messages,
    fetch_user_info,
)
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import wrap_mcp_servers_with_exception_handling

if TYPE_CHECKING:
    from tiger_agent.agent.tiger_agent import TigerAgent


@dataclass
class AgentAndContext:
    agent: Agent
    user_prompt: str | Sequence[UserContent]
    ctx: AgentResponseContext
    channel_to_respond: str


async def create_agent_and_context(
    hctx: HarnessContext,
    task: Task,
    agent: TigerAgent,
    channel_to_respond: str,
) -> AgentAndContext:
    event = task.event

    if not hctx.bot_info:
        hctx.bot_info = await fetch_bot_info(hctx.app.client)

    all_mcp_servers = agent.mcp_loader()
    agent.augment_mcp_servers(all_mcp_servers)

    mcp_servers = await filter_mcp_servers(
        mcp_servers=all_mcp_servers,
        client=hctx.app.client,
        channel_id=channel_to_respond,
        filter_internal_only=not isinstance(event, UserDefinedRuleMatch),
    )

    wrap_mcp_servers_with_exception_handling(mcp_servers=mcp_servers)

    ctx = AgentResponseContext(
        task=task,
        mention=event,
        bot=hctx.bot_info,
        user=await fetch_user_info(client=hctx.app.client, user_id=event.user)
        if not isinstance(event, SalesforceBaseEvent)
        else None,
        mcp_servers=mcp_servers,
    )

    extra_ctx: ExtraContextDict = {}
    await agent.augment_context(ctx=ctx, extra_ctx=extra_ctx)

    if not isinstance(event, SalesforceBaseEvent) and event.thread_ts and hctx.bot_info:
        thread_messages = await fetch_thread_messages(
            client=hctx.app.client,
            channel=event.channel,
            thread_ts=event.thread_ts,
        )
        extra_ctx["thread_history"] = format_thread_history(
            thread_messages, hctx.bot_info, [event.ts]
        )

    system_prompt = await agent.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
    user_prompt = await agent.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)

    toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

    async def _download_slack_hosted_file(
        file: SlackFile,
    ) -> BinaryContent | str | None:
        return await download_slack_hosted_file(file=file)

    _event_type_by_name: dict[str, type] = {
        cls.__name__: cls for cls in EVENT_TYPE_REGISTRY
    }

    async def _list_user_defined_rules() -> list[UserDefinedRule]:
        assert isinstance(event, SlackBaseEvent)
        return await list_user_defined_rules(pool=hctx.pool, owner_slack_id=event.user)

    async def _delete_user_defined_rule(rule_id: int) -> bool:
        assert isinstance(event, SlackBaseEvent)
        return await delete_user_defined_rule(
            pool=hctx.pool, rule_id=rule_id, owner_slack_id=event.user
        )

    async def _create_user_defined_rule(
        name: str,
        event_type: str,
        criteria: str,
        action_prompt: str,
        criteria_examples: list[str] | None = None,
    ) -> UserDefinedRule:
        assert isinstance(event, SlackBaseEvent)
        if event_type not in _event_type_by_name:
            raise ValueError(
                f"Unknown event_type {event_type!r}. "
                f"Valid options: {', '.join(_event_type_by_name)}"
            )
        cls = _event_type_by_name[event_type]
        subtype_field = cls.model_fields.get("subtype")
        event_subtype = (
            subtype_field.default
            if subtype_field and isinstance(subtype_field.default, str)
            else None
        )
        return await insert_user_defined_rule(
            pool=hctx.pool,
            name=name,
            owner_slack_id=event.user,
            event_type=cls.model_fields["type"].default,
            event_subtype=event_subtype,
            criteria=criteria,
            action_prompt=action_prompt,
            criteria_examples=criteria_examples,
        )

    event_type_options = "\n".join(
        f"- {cls.__name__}: {cls.event_description}" for cls in EVENT_TYPE_REGISTRY
    )

    pydantic_agent = Agent(
        model=agent.model,
        deps_type=dict[str, Any],
        system_prompt=system_prompt,
        output_type=AgentSalesforceResponse
        if isinstance(event, SalesforceBaseEvent)
        else str,
        tools=[
            Tool(
                _download_slack_hosted_file,
                takes_ctx=False,
                name="download_slack_hosted_file",
                description="This will download a file associated with a Slack message and return its contents. Note: only images, text, or PDFs are supported.",
            ),
            *(
                [
                    Tool(
                        _list_user_defined_rules,
                        takes_ctx=False,
                        name="list_user_defined_rules",
                        description=(
                            "List all user-defined rules owned by the current user. "
                            'Use when the user asks things like "show me my rules", '
                            '"what rules do I have set up?", or "list my custom rules".'
                        ),
                    ),
                    Tool(
                        _delete_user_defined_rule,
                        takes_ctx=False,
                        name="delete_user_defined_rule",
                        description=(
                            "Delete a user-defined rule by its ID. Only rules owned by the current user "
                            "can be deleted. Returns True if deleted, False if not found. Use when the user asks things like "
                            '"delete rule 3", "remove my rule with ID 7", or "turn off rule 12".'
                        ),
                    ),
                    Tool(
                        _create_user_defined_rule,
                        takes_ctx=False,
                        name="create_user_defined_rule",
                        description=(
                            "Call this when the user wants to be notified, alerted, or asks to create a rule or automation. "
                            "Creates a persistent rule that triggers a custom action when a matching event occurs. "
                            "Infer all parameters from the user's request.\n"
                            f"event_type must be one of:\n{event_type_options}\n"
                            "criteria_examples are optional but improve matching accuracy."
                        ),
                    ),
                ]
                if isinstance(event, SlackBaseEvent)
                else []
            ),
        ],
        toolsets=toolsets,
        model_settings={"extra_headers": {"anthropic-beta": "context-1m-2025-08-07"}}
        if (isinstance(agent.model, str) and agent.model.startswith("anthropic:"))
        or isinstance(agent.model, AnthropicModel)
        else None,
    )

    return AgentAndContext(
        agent=pydantic_agent,
        user_prompt=user_prompt,
        ctx=ctx,
        channel_to_respond=channel_to_respond,
    )
