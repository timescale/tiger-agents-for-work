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
from pydantic_ai import Agent, BinaryContent, Tool, UsageLimits, models
from pydantic_ai.messages import (
    UserContent,
)
from pydantic_ai.models.anthropic import AnthropicModel
from slack_sdk.web.async_client import (
    AsyncChatStream,
)

from tiger_agent.agent.types import (
    AgentResponseContext,
    AgentSalesforceResponse,
    ExtraContextDict,
)
from tiger_agent.db.utils import usage_limit_reached, user_ignored
from tiger_agent.events.types import Event, HarnessContext
from tiger_agent.mcp.types import MCPDict
from tiger_agent.mcp.utils import MCPLoader, filter_mcp_servers
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_ENABLE_SPAM_FILTERING,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import SalesforceBaseEvent
from tiger_agent.salesforce.utils import create_case_url
from tiger_agent.slack.types import BotInfo, SlackFile, SlackMessage
from tiger_agent.slack.utils import (
    add_reaction,
    download_private_file,
    download_slack_hosted_file,
    fetch_bot_info,
    fetch_user_info,
    post_response,
    set_status,
    stream_response_to_mention,
)
from tiger_agent.utils import (
    file_type_supported,
    wrap_mcp_servers_with_exception_handling,
)

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
            {k: v.model_dump() for k, v in extra_ctx.items()}
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

    @logfire.instrument("generate_response", extract_args=False)
    async def create_and_send_response(
        self, hctx: HarnessContext, stream_event: Event
    ) -> SlackMessage | None:
        event = stream_event.event

        if not self.bot_info:
            self.bot_info = await fetch_bot_info(hctx.app.client)

        all_mcp_servers = self.mcp_loader()
        self.augment_mcp_servers(all_mcp_servers)

        channel_to_respond = (
            event.channel
            if not isinstance(event, SalesforceBaseEvent)
            else SALESFORCE_CASE_CHANNEL
        )

        mcp_servers = await filter_mcp_servers(
            mcp_servers=all_mcp_servers,
            client=hctx.app.client,
            channel_id=channel_to_respond,
        )

        wrap_mcp_servers_with_exception_handling(mcp_servers=mcp_servers)

        ctx = AgentResponseContext(
            event=stream_event,
            mention=event,
            bot=self.bot_info,
            user=await fetch_user_info(client=hctx.app.client, user_id=event.user)
            if not isinstance(event, SalesforceBaseEvent)
            else None,
            mcp_servers=mcp_servers,
            slack_bot_token=hctx.slack_bot_token,
        )

        extra_ctx = {}
        await self.augment_context(ctx=ctx, extra_ctx=extra_ctx)

        system_prompt = await self.make_system_prompt(ctx=ctx, extra_ctx=extra_ctx)
        user_prompt = await self.make_user_prompt(ctx=ctx, extra_ctx=extra_ctx)

        toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

        # need to create this closure here so we can pass it in
        # as a tool to Pydantic agent
        async def _download_slack_hosted_file(
            file: SlackFile,
        ) -> BinaryContent | str | None:
            return await download_slack_hosted_file(
                file=file, slack_bot_token=hctx.slack_bot_token
            )

        agent = Agent(
            model=self.model,
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
                )
            ],
            toolsets=toolsets,
            model_settings={
                "extra_headers": {"anthropic-beta": "context-1m-2025-08-07"}
            }
            if (isinstance(self.model, str) and self.model.startswith("anthropic:"))
            or isinstance(self.model, AnthropicModel)
            else None,
        )

        # when creating a thread for a new Salesforce case
        # we post a short description as a new top level message
        # then post the details response as a threaded message
        # within the OP, but only if the case is not deemed as spam
        if isinstance(event, SalesforceBaseEvent):
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
                text=f"*New Case <{create_case_url(event.case)}|{event.case.CaseNumber}> - {event.case.Subject}*\n{response.output.short_description_of_case}",
            )

            message_to_link_to = SlackMessage(
                channel_id=channel_to_respond,
                ts=original_message.data.get("ts"),
                text=response.output.message,
                thread_ts=None,
            )

            if not response.output.is_spam:
                # this is the detailed message
                await post_response(
                    client=hctx.app.client,
                    channel=channel_to_respond,
                    thread_ts=message_to_link_to.ts,
                    text=response.output.message,
                )

            return message_to_link_to

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
            )
            logfire.info("ended", extra={"res": rest})

        # clear the status widget
        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=False,
        )

        return response_message

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

            if not isinstance(event_to_handle, SalesforceBaseEvent):
                await add_reaction(
                    hctx.app.client,
                    event_to_handle.channel,
                    event_to_handle.ts,
                    "white_check_mark",
                )
            else:
                if message and SALESFORCE_SLACK_THREAD_FIELD:
                    result = await hctx.app.client.chat_getPermalink(
                        channel=message.channel_id, message_ts=message.ts
                    )
                    permalink = result.data.get("permalink")

                    hctx.salesforce_client.Case.update(
                        event_to_handle.case.Id,
                        {SALESFORCE_SLACK_THREAD_FIELD: permalink},
                        headers={"Sforce-Auto-Assign": "false"},
                    )

                    logfire.info(
                        "Updated Salesforce case to include the thread link",
                        extra={"permalink": permalink},
                    )
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
