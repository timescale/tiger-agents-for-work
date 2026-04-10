"""Tiger Agent - AI-powered Slack bot using Pydantic-AI with MCP server integration.

This module provides the TigerAgent class, which serves as an EventProcessor for the
EventHarness system. It integrates multiple technologies to create an intelligent
Slack bot:

- **Pydantic-AI**: For LLM interaction and structured responses
- **MCP Servers**: For extending capabilities with external tools and data sources
- **Jinja2 Templates**: For dynamic prompt generation based on context
- **Slack Integration**: For rich interaction patterns with reactions and threading

The TigerAgent processes Slack app_mention events by:
1. Loading context (user info, bot info, event details)
2. Rendering system and user prompts from Jinja2 templates
3. Creating a Pydantic-AI agent with MCP server toolsets
4. Generating AI responses with access to external tools
5. Posting responses to Slack with appropriate visual feedback
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import logfire
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader
from pydantic import BaseModel
from pydantic_ai import Agent, UsageLimits, models
from pydantic_ai.messages import UserContent
from slack_sdk.web.async_client import (
    AsyncChatStream,
)

from tiger_agent.agent.types import (
    AgentResponseContext,
    ExtraContextDict,
)
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import (
    get_salesforce_account_id_for_channel,
    usage_limit_reached,
    user_ignored,
)
from tiger_agent.events.types import Event, HarnessContext
from tiger_agent.mcp.types import MCPDict
from tiger_agent.mcp.utils import MCPLoader
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_ENABLE_SPAM_FILTERING,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
    SalesforceCreateNewCaseEvent,
)
from tiger_agent.salesforce.utils import create_case, create_case_url
from tiger_agent.slack.types import (
    BotInfo,
    SlackAppMentionEvent,
    SlackMessage,
    SlackMessageEvent,
)
from tiger_agent.slack.utils import (
    add_reaction,
    download_private_file,
    post_response,
    send_feedback_rating_prompt,
    set_status,
    stream_response_to_mention,
)
from tiger_agent.utils import file_type_supported

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_REGEX = r"^system_prompt.*\.md$"
USER_PROMPT_REGEX = r"^user_prompt.*\.md$"


class TigerAgent:
    """AI-powered Slack bot using Pydantic-AI with MCP server integration.

    TigerAgent serves as an EventProcessor for the EventHarness system, processing
    Slack app_mention events by generating AI responses with access to external tools
    via MCP servers. It provides a complete interaction flow with visual feedback
    through Slack reactions.

    Key Features:
    - **Dynamic Prompting**: Uses Jinja2 templates for context-aware system/user prompts
    - **MCP Integration**: Connects to multiple MCP servers for extended capabilities
    - **Rich Slack Interaction**: Provides visual feedback with reactions and threading
    - **Error Handling**: Graceful failure handling with user-friendly error messages
    - **Context Awareness**: Incorporates user info, timezone, and event context

    Args:
        model: Pydantic-AI model specification (defaults to configured model)
        prompt_config: Sequence of PromptPackage instances or Path objects for extra prompt templates
        jinja_env: Pre-configured Jinja2 Environment (mutually exclusive with prompt_config)
        mcp_config_path: Path to MCP server configuration JSON file
        max_attempts: Maximum retry attempts for failed events (defaults to 3)
        rate_limit_allowed_requests: Maximum requests allowed per interval for rate limiting
        rate_limit_interval: Time interval for rate limiting (defaults to 1 minute)

    Raises:
        ValueError: If jinja_env is provided but not async-enabled, or if both jinja_env and prompt_config are provided
    """

    def __init__(
        self,
        model: models.Model | models.KnownModelName | str | None = None,
        prompt_config: Sequence[PromptPackage | Path] | None = None,
        jinja_env: Environment | None = None,
        mcp_config_path: Path | None = None,
        max_attempts: int = 3,
        rate_limit_allowed_requests: int | None = None,
        rate_limit_interval: timedelta = timedelta(minutes=1),
    ):
        self.bot_info: BotInfo | None = None
        self.model = model
        self.extra_context: dict[str, BaseModel] = {}

        if jinja_env is not None and prompt_config is not None:
            raise ValueError(
                "jinja_env and prompt_config cannot both be given, choose one or the other"
            )

        if jinja_env is not None:
            if not jinja_env.is_async:
                raise ValueError("jinja_env must have `enable_async=True`")
            self.jinja_env = jinja_env
        else:
            # The purpose of this section is to provide a core/default prompt
            # that can be overrided by the given prompt_config. A ChoiceLoader is
            # used to control the order of precendence as it will find the first
            # match in the loaders and return.
            #
            # Example: if there are three prompt loaders with system_prompt.md
            # the ChoiceLoader will return the value from the first loader in the list
            loaders = []

            if prompt_config is not None:
                for config in prompt_config:
                    if isinstance(config, PromptPackage):
                        loaders.append(PackageLoader(**config.model_dump()))
                    elif isinstance(config, Path):
                        loaders.append(FileSystemLoader(config))
                    else:
                        logfire.warning(
                            "Received invalid prompt_config item", config=config
                        )

            # we load the default, core prompts at the end so that the provided
            # prompts can override them
            loaders.append(PackageLoader("tiger_agent", "prompts"))

            self.jinja_env = Environment(
                enable_async=True, loader=ChoiceLoader(loaders)
            )

        self.mcp_loader = MCPLoader(mcp_config_path)
        self.max_attempts = max_attempts
        self.rate_limit_allowed_requests = rate_limit_allowed_requests
        self.rate_limit_interval = rate_limit_interval

    async def render_prompts(
        self,
        regex: str,
        ctx: AgentResponseContext,
        extra_ctx: ExtraContextDict | None = None,
    ) -> Sequence[str]:
        """Render all Jinja2 templates matching a regex pattern.

        Discovers all available templates in the Jinja2 environment, filters them
        using the provided regex pattern, and renders each matching template with
        the given context. This enables flexible prompt composition by allowing
        multiple templates to be processed dynamically.

        Args:
            regex: Regular expression pattern to match template names
            ctx: Template context containing event, user, bot info, and other data

        Returns:
            List of rendered template strings, one for each matching template
        """
        all_templates = self.jinja_env.list_templates()
        prompt_templates_matching_regex = [
            tmpl_name for tmpl_name in all_templates if re.match(regex, tmpl_name)
        ]

        # Sort: shortest name first, then alphabetically by name without .md extension
        prompt_templates_matching_regex.sort(
            key=lambda tmpl: (len(tmpl), tmpl.rsplit(".md", 1)[0].lower())
        )

        extra_context: dict[str, Any] = (
            {
                k: v.model_dump() if isinstance(v, BaseModel) else v
                for k, v in extra_ctx.items()
            }
            if self.extra_context is not None and isinstance(self.extra_context, dict)
            else {}
        )

        rendered_prompts = await asyncio.gather(
            *[
                self.jinja_env.get_template(tmpl_name).render_async(
                    **extra_context, **ctx.model_dump()
                )
                for tmpl_name in prompt_templates_matching_regex
            ]
        )

        return rendered_prompts

    @logfire.instrument("make_system_prompt", extract_args=False)
    async def make_system_prompt(
        self, ctx: AgentResponseContext, extra_ctx: ExtraContextDict | None = None
    ) -> str | Sequence[str]:
        """Generate system prompt from Jinja2 templates matching *system_prompt.md.

        Renders template with the provided context,
        creating a dynamic system prompt that can include event details,
        user information, bot capabilities, and other contextual data.

        Args:
            ctx: Template context containing event, user, bot info, etc.

        Returns:
            Rendered system prompt strings
        """

        rendered_system_prompts = await self.render_prompts(
            SYSTEM_PROMPT_REGEX, ctx, extra_ctx
        )

        return rendered_system_prompts

    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(
        self, ctx: AgentResponseContext, extra_ctx: ExtraContextDict | None = None
    ) -> str | Sequence[UserContent]:
        """Generate system prompt from Jinja2 templates matching *user_prompt.md

        Renders the user prompt templates with the provided context,
        creating a dynamic user prompt that typically contains the actual
        Slack message content and relevant contextual information.

        If the mention contains attached files, downloads them and returns
        a sequence of UserContent objects (text + binary files) for multimodal
        processing by the AI agent.

        Args:
            ctx: Template context containing event, user, bot info, etc.

        Returns:
            Rendered user prompt string if no files are attached, or a sequence
            of UserContent objects (text prompt + file contents) if files are present
        """
        rendered_user_prompts = await self.render_prompts(
            USER_PROMPT_REGEX, ctx, extra_ctx
        )

        if (
            isinstance(ctx.mention, SalesforceBaseEvent)
            or ctx.mention.files is None
            or not len(ctx.mention.files)
        ):
            return rendered_user_prompts

        user_contents: list[UserContent] = [
            await download_private_file(
                url_private_download=file.url_private_download,
                slack_bot_token=ctx.slack_bot_token,
            )
            for file in ctx.mention.files
            if file_type_supported(file.mimetype)
        ]
        return [*user_contents, *rendered_user_prompts]

    def augment_mcp_servers(self, mcp_servers: MCPDict):
        """Hook to augment loaded MCP servers before use.

        This method can be overridden in subclasses to modify or add to the
        MCP servers created from configuration in-place. This can be useful
        for adding a `process_tool_call` callback on servers for example.

        Args:
            mcp_servers: Dictionary of loaded MCP servers
        """

    async def augment_context(
        self, ctx: AgentResponseContext, extra_ctx: ExtraContextDict
    ) -> None:
        """Hook to augment context with additional BaseModel objects.

        This method can be overridden in subclasses to modify or add to the
        extra context dictionary in-place. This can be useful for adding
        custom BaseModel instances that will be available in Jinja2 templates.

        Args:
            ctx: Agent response context containing event data, user info, and bot info
            extra_ctx: Dictionary of BaseModel objects keyed by name for template access
        """

    async def handle_create_salesforce_case(
        self,
        hctx: HarnessContext,
        event: SalesforceCreateNewCaseEvent,
        channel_to_respond: str,
    ) -> None:
        account_id_for_channel = await get_salesforce_account_id_for_channel(
            pool=hctx.pool, channel_id=channel_to_respond
        )

        if not account_id_for_channel:
            logfire.warn(
                "Skipping Salesforce case creation. No Salesforce account associated with the channel.",
                channel=channel_to_respond,
                user=event.user,
            )
            return

        new_case = create_case(
            salesforce_client=hctx.salesforce_client,
            subject=event.subject,
            description=event.description,
            severity=event.severity,
            account_id=account_id_for_channel,
            project_id=event.project_id,
            service_id=event.service_id,
        )
        await post_response(
            client=hctx.app.client,
            channel=channel_to_respond,
            thread_ts=None,
            text=f"*Support Case Created*\nCase Number: {new_case.CaseNumber}\nSubject: {new_case.Subject} \nDescription: {new_case.Description}",
        )

    async def handle_salesforce_event(
        self,
        hctx: HarnessContext,
        event: SalesforceBaseEvent,
        agent: Agent,
        user_prompt: str | list,
        ctx: AgentResponseContext,
        channel_to_respond: str,
    ) -> SlackMessage | None:

        response = await agent.run(
            user_prompt=user_prompt,
            deps=ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        )

        if response.output.is_spam:
            logfire.info(
                "Salesforce case identified as spam",
                extra={"filtering_enabled": SALESFORCE_ENABLE_SPAM_FILTERING},
            )
            if SALESFORCE_ENABLE_SPAM_FILTERING:
                return

        original_message = await post_response(
            client=hctx.app.client,
            channel=channel_to_respond,
            thread_ts=None,
            text=f"*New Case* <{create_case_url(event.case)}|{event.case.CaseNumber}> - _{event.case.Subject}_{f', assigned to <@{response.output.case_owner_slack_user_id}>' if response.output.case_owner_slack_user_id else ''}:thread: \n```\n{response.output.short_description_of_case}\n```",
        )

        message_to_link_to = SlackMessage(
            channel_id=channel_to_respond,
            ts=original_message.data.get("ts"),
            text=response.output.message,
            thread_ts=None,
            to_user_id=response.output.case_owner_slack_user_id,
        )

        if not response.output.is_spam:
            # this is the detailed message
            await post_response(
                client=hctx.app.client,
                channel=channel_to_respond,
                thread_ts=message_to_link_to.ts,
                text=response.output.message,
            )

        if message_to_link_to and SALESFORCE_SLACK_THREAD_FIELD:
            if event.update_link_to_thread:
                result = await hctx.app.client.chat_getPermalink(
                    channel=message_to_link_to.channel_id,
                    message_ts=message_to_link_to.ts,
                )
                permalink = result.data.get("permalink")

                hctx.salesforce_client.Case.update(
                    event.case.Id,
                    {SALESFORCE_SLACK_THREAD_FIELD: permalink},
                    headers={"Sforce-Auto-Assign": "false"},
                )

                logfire.info(
                    "Updated Salesforce case to include the thread link",
                    extra={"permalink": permalink},
                )

            if message_to_link_to.to_user_id:
                await send_feedback_rating_prompt(hctx.app.client, message_to_link_to)

        return message_to_link_to

    async def handle_slack_event(
        self,
        hctx: HarnessContext,
        event: SlackAppMentionEvent | SlackMessageEvent,
        agent: Agent,
        user_prompt: str | list,
        ctx: AgentResponseContext,
        channel_to_respond: str,
    ) -> SlackMessage | None:
        if await usage_limit_reached(
            pool=hctx.pool,
            user_id=event.user,
            interval=self.rate_limit_interval,
            allowed_requests=self.rate_limit_allowed_requests,
        ):
            logfire.info(
                "User interaction limited due to usage",
                allowed_requests=self.rate_limit_allowed_requests,
                interval=self.rate_limit_interval,
                user_id=event.user,
            )

            await post_response(
                client=hctx.app.client,
                channel=channel_to_respond,
                thread_ts=event.thread_ts or event.ts,
                text="I cannot process your request at this time due to usage limits. Please ask me again later.",
            )

            return

        response_message: SlackMessage | None = None
        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=True,
        )
        slack_stream: AsyncChatStream | None = None

        # my first attempt was using `run_stream`, however, there is a known 'issue'
        # that that will return before tool calls are made: https://github.com/pydantic/pydantic-ai/issues/3574
        async for stream_event in agent.run_stream_events(
            user_prompt=user_prompt,
            deps=ctx,
            usage_limits=UsageLimits(output_tokens_limit=9_000),
        ):
            slack_stream = await stream_response_to_mention(
                client=hctx.app.client,
                slack_stream=slack_stream,
                stream_event=stream_event,
                channel_id=event.channel,
                recipient_user_id=event.user,
                recipient_team_id=self.bot_info.team_id,
                ts=event.ts,
                thread_ts=event.thread_ts,
            )

        if slack_stream is not None and slack_stream._state != "completed":
            rest = await slack_stream.stop()
            response_message = SlackMessage(
                text=rest.data.get("message").get("text"),
                ts=rest.data.get("ts"),
                channel_id=rest.data.get("channel"),
                thread_ts=None,
                to_user_id=event.user,
            )
            logfire.info("ended", extra={"res": rest})

        # clear the status widget
        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=False,
        )

        await add_reaction(
            hctx.app.client,
            event.channel,
            event.ts,
            "white_check_mark",
        )

        return response_message

    @logfire.instrument("generate_response", extract_args=False)
    async def create_and_send_response(
        self, hctx: HarnessContext, stream_event: Event
    ) -> SlackMessage | None:
        event = stream_event.event
        channel_to_respond = (
            event.channel
            if not isinstance(event, SalesforceBaseEvent)
            or isinstance(event, SalesforceCreateNewCaseEvent)
            else SALESFORCE_CASE_CHANNEL
        )

        if isinstance(event, SalesforceCreateNewCaseEvent):
            await self.handle_create_salesforce_case(
                hctx=hctx,
                event=event,
                channel_to_respond=channel_to_respond,
            )
            return

        agent_and_ctx, self.bot_info = await create_agent_and_context(
            hctx=hctx,
            stream_event=stream_event,
            model=self.model,
            bot_info=self.bot_info,
            channel_to_respond=channel_to_respond,
            mcp_loader=self.mcp_loader,
            augment_mcp_servers=self.augment_mcp_servers,
            augment_context=self.augment_context,
            make_system_prompt=self.make_system_prompt,
            make_user_prompt=self.make_user_prompt,
        )
        agent = agent_and_ctx.agent
        user_prompt = agent_and_ctx.user_prompt
        ctx = agent_and_ctx.ctx
        channel_to_respond = agent_and_ctx.channel_to_respond
        event = ctx.mention

        if isinstance(event, SalesforceBaseEvent):
            return await self.handle_salesforce_event(
                hctx=hctx,
                event=event,
                agent=agent,
                user_prompt=user_prompt,
                ctx=ctx,
                channel_to_respond=channel_to_respond,
            )

        return await self.handle_slack_event(
            hctx=hctx,
            event=event,
            agent=agent,
            user_prompt=user_prompt,
            ctx=ctx,
            channel_to_respond=channel_to_respond,
        )

    async def __call__(self, hctx: HarnessContext, event: Event) -> None:
        """Processes various events.

        This method implements the complete EventProcessor interface for the
        EventHarness system. It provides rich Slack interaction patterns:

        Success Flow:
        1. Generate AI response using MCP tools with real-time streaming
        2. Show dynamic status messages during processing
        3. Stream response updates directly to Slack thread
        4. Add :white_check_mark: reaction for success

        Failure Flow:
        1. Clear any active status messages
        2. Add :x: reaction to indicate failure
        3. Post user-friendly error message
        4. Re-raise exception for EventHarness retry logic

        The error message adapts based on retry attempts:
        - During retries: "I will try again."
        - Final failure: "I give up. Sorry."

        Args:
            hctx: Harness context providing Slack app and database access
            event: The Slack event to process

        Raises:
            Exception: Re-raises any processing exceptions for EventHarness retry handling
        """
        event_to_handle = event.event
        try:
            if not isinstance(
                event_to_handle, SalesforceBaseEvent
            ) and await user_ignored(pool=hctx.pool, user_id=event_to_handle.user):
                logfire.info("Ignore user", user_id=event_to_handle.user)
                return

            message = await self.create_and_send_response(hctx, event)

            if message:
                logfire.info("Reponse sent", extra={"message": message})

        except Exception as e:
            logger.exception("response failed", exc_info=e)
            if isinstance(event_to_handle, SalesforceBaseEvent):
                return

            await add_reaction(
                hctx.app.client, event_to_handle.channel, event_to_handle.ts, "x"
            )
            await post_response(
                client=hctx.app.client,
                channel=event_to_handle.channel,
                thread_ts=event_to_handle.thread_ts
                if event_to_handle.thread_ts
                else event_to_handle.ts,
                text="I experienced an issue trying to respond. I will try again."
                if event.attempts < self.max_attempts
                else " I give up. Sorry.",
            )
            raise e
