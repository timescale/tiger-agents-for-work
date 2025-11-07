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

import logging
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import logfire
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, BinaryContent, UsageLimits, models

from pydantic_ai.messages import UserContent

from tiger_agent.slack import (
    BotInfo,
    add_reaction,
    download_private_file,
    fetch_bot_info,
    fetch_channel_info,
    fetch_user_info,
    post_response,
    remove_reaction,
)
from tiger_agent.types import AgentResponseContext, Event, HarnessContext, MCPDict, McpConfig, McpConfigExtraFields, SlackFile
from tiger_agent.utils import MCPLoader, file_type_supported, get_filtered_mcp_servers, get_all_fields, usage_limit_reached, user_ignored

logger = logging.getLogger(__name__)


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
        jinja_env: Jinja2 Environment or path to template directory
        mcp_config_path: Path to MCP server configuration JSON file
        max_attempts: Maximum retry attempts for failed events

    Raises:
        ValueError: If jinja_env is provided as Environment but not async-enabled
    """
    def __init__(
        self,
        model: models.Model | models.KnownModelName | str | None = None,
        jinja_env: Environment | Path = Path.cwd(),
        mcp_config_path: Path | None = None,
        max_attempts: int = 3,
        rate_limit_allowed_requests: int | None = None,
        rate_limit_interval: timedelta = timedelta(minutes=1),
    ):
        self.bot_info: BotInfo | None = None
        self.model = model
        if isinstance(jinja_env, Environment):
            if not jinja_env.is_async:
                raise ValueError("jinja_env must have `enable_async=True`")
            self.jinja_env = jinja_env
        else:
            self.jinja_env = Environment(
                enable_async=True,
                loader=FileSystemLoader(jinja_env)
            )
        self.mcp_loader = MCPLoader(mcp_config_path)
        self.max_attempts = max_attempts
        self.rate_limit_allowed_requests = rate_limit_allowed_requests
        self.rate_limit_interval = rate_limit_interval

    @logfire.instrument("make_system_prompt", extract_args=False)
    async def make_system_prompt(self, ctx: AgentResponseContext) -> str | Sequence[str]:
        """Generate system prompt from Jinja2 template.

        Renders the 'system_prompt.md' template with the provided context,
        creating a dynamic system prompt that can include event details,
        user information, bot capabilities, and other contextual data.

        Args:
            ctx: Template context containing event, user, bot info, etc.

        Returns:
            Rendered system prompt string
        """
        tmpl = self.jinja_env.get_template("system_prompt.md")
        return await tmpl.render_async(**ctx.model_dump())

    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(self, ctx: AgentResponseContext) -> str | Sequence[UserContent]:
        """Generate user prompt from Jinja2 template.

        Renders the 'user_prompt.md' template with the provided context,
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
        tmpl = self.jinja_env.get_template("user_prompt.md")
        text_prompt =  await tmpl.render_async(**ctx.model_dump())
        
        if ctx.mention.files is None or not len(ctx.mention.files):
            return text_prompt
        
        user_contents: list[UserContent] = [await download_private_file(url_private_download=file.url_private_download) for file in ctx.mention.files if file_type_supported(file.mimetype)]
        user_contents.insert(0, text_prompt)
        
        return user_contents

    def augment_mcp_servers(self, mcp_servers: MCPDict):
        """Hook to augment loaded MCP servers before use.

        This method can be overridden in subclasses to modify or add to the
        MCP servers created from configuration in-place. This can be useful
        for adding a `process_tool_call` callback on servers for example.

        Args:
            mcp_servers: Dictionary of loaded MCP servers
        """

    @logfire.instrument("generate_response", extract_args=False)
    async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
        """Generate AI response to a Slack app_mention event.

        This is the core logic that:
        1. Builds context from event data, user info, and bot info
        2. Renders system and user prompts from Jinja2 templates
        3. Creates a Pydantic-AI agent with MCP server toolsets
        4. Runs the AI model to generate a response
        5. Returns the generated response text

        The context dictionary provides templates with access to:
        - event: The full Event object with processing metadata
        - mention: The AppMentionEvent with Slack message details
        - bot: Bot information and capabilities
        - user: User profile information including timezone
        - local_time: Event timestamp in user's local timezone

        Args:
            hctx: Harness context providing Slack app and database access
            event: The Slack event to process

        Returns:
            Generated AI response text ready for posting to Slack
        """
        mention = event.event
        
        if await usage_limit_reached(pool=hctx.pool, user_id=mention.user, interval=self.rate_limit_interval, allowed_requests=self.rate_limit_allowed_requests):
            logfire.info("User interaction limited due to usage", allowed_requests=self.rate_limit_allowed_requests, interval=self.rate_limit_interval, user_id=mention.user)
            return "I cannot process your request at this time due to usage limits. Please ask me again later."
        
        if not self.bot_info:
            self.bot_info = await fetch_bot_info(hctx.app.client)
        
        user_info = await fetch_user_info(hctx.app.client, mention.user)
        
        mcp_servers = await get_filtered_mcp_servers(mcp_loader=self.mcp_loader, client=hctx.app.client, channel_id=mention.channel)
        
        self.augment_mcp_servers(mcp_servers)
        
        ctx = AgentResponseContext(event=event, mention=mention, bot=self.bot_info, user=user_info, mcp_servers=mcp_servers)

        system_prompt = await self.make_system_prompt(ctx)
        user_prompt = await self.make_user_prompt(ctx)

        toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values()]

        agent = Agent(
            model=self.model,
            deps_type=dict[str, Any],
            system_prompt=system_prompt,
            toolsets=toolsets
        )
        @agent.tool_plain
        async def download_slack_hosted_file(file: SlackFile) -> BinaryContent | str | None:
            """This will download a file associated with a Slack message and return its contents. Note: only images, text, or PDFs are supported."""
            if not file_type_supported(file.mimetype):
                return "File type not supported"

            return await download_private_file(url_private_download=file.url_private_download)

        async with agent as a:
            response = await a.run(
                user_prompt=user_prompt,
                deps=ctx,
                usage_limits=UsageLimits(
                    output_tokens_limit=9_000
                )
            )
            return response.output

    async def __call__(self, hctx: HarnessContext, event: Event) -> None:
        """Process a Slack app_mention event with full interaction flow.

        This method implements the complete EventProcessor interface for the
        EventHarness system. It provides rich Slack interaction patterns:

        Success Flow:
        1. Add :spinthinking: reaction to show processing started
        2. Generate AI response using MCP tools
        3. Post response in thread (or as reply if not threaded)
        4. Remove :spinthinking: and add :white_check_mark: for success

        Failure Flow:
        1. Remove :spinthinking: reaction
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
        client = hctx.app.client
        mention = event.event
        try:
            if await user_ignored(pool=hctx.pool,user_id=mention.user):
                logfire.info("Ignore user", user_id=mention.user)
                return
            await add_reaction(client, mention.channel, mention.ts, "spinthinking")
            response = await self.generate_response(hctx, event)
            await post_response(client, mention.channel, mention.thread_ts if mention.thread_ts else mention.ts, response)
            await remove_reaction(client, mention.channel, mention.ts, "spinthinking")
            await add_reaction(client, mention.channel, mention.ts, "white_check_mark")
        except Exception as e:
            logger.exception("response failed", exc_info=e)
            await remove_reaction(client, mention.channel, mention.ts, "spinthinking")
            await add_reaction(client, mention.channel, mention.ts, "x")
            await post_response(
                client,
                mention.channel,
                mention.thread_ts if mention.thread_ts else mention.ts,
                "I experienced an issue trying to respond. I will try again."
                if event.attempts < self.max_attempts
                else " I give up. Sorry.",
            )
            raise e
