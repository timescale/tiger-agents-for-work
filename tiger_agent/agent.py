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

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import logfire
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, UsageLimits, models
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP
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
from tiger_agent.types import AgentResponseContext, Event, HarnessContext
from tiger_agent.utils import get_all_fields, usage_limit_reached, user_ignored

logger = logging.getLogger(__name__)

@dataclass
class McpConfigExtraFields:
    """
    This represents the custom-properties on the config items in the mcp_config.json file.
    Each item can use properties from MCPServerStreamableHTTP or MCPServerStdio, plus these fields
    Attributes:
        internal_only: Specifies if this can be used in externally shared channels
        
    """
    internal_only: bool
    disabled: bool

@dataclass
class McpConfig:
    """
    Attributes:
        internal_only: Specifies if this can be used in externally shared channels
        mcp_server: The MCP server instance
    """
    internal_only: bool
    mcp_server: MCPServerStreamableHTTP | MCPServerStdio

type MCPDict = dict[str, McpConfig]

@logfire.instrument("load_mcp_config")
def load_mcp_config(mcp_config: Path) -> dict[str, dict[str, Any]]:
    """Load MCP server configuration from a JSON file.

    Args:
        mcp_config: Path to JSON configuration file

    Returns:
        Dictionary mapping server names to their configuration dictionaries
    """
    loaded_mcp_config: dict[str, dict[str, Any]] = json.loads(mcp_config.read_text()) if mcp_config else {}
    return loaded_mcp_config


@logfire.instrument("create_mcp_servers", extract_args=False)
def create_mcp_servers(mcp_config: dict[str, dict[str, Any]]) -> MCPDict:
    """Create MCP server instances from configuration.

    Supports two types of MCP servers:
    - MCPServerStdio: For command-line MCP servers (uses 'command' and 'args')
    - MCPServerStreamableHTTP: For HTTP-based MCP servers (uses 'url')

    Servers marked with 'disabled': true are skipped.

    Args:
        mcp_config: Dictionary of server configurations

    Returns:
        Dictionary mapping server names to configured MCP server instances
    """
    mcp_servers: MCPDict = {}
    for name, cfg in mcp_config.items():
        if cfg.get("disabled", False):
            continue

        internal_only = cfg.get("internal_only", False)
        
        # our mcp_config.json items have fields that do not exist on pydantic's MCPServer object
        # if we pass them in, an error will be thrown. Previously, we were pop()'ing the parameters
        # off, but was destructive -- in other words, an mcp config would only be disabled the first time
        # this method was called & and it is called each time an agent handles an event
        valid_mcp_server_fields = get_all_fields(MCPServerStdio) | get_all_fields(MCPServerStreamableHTTP)
        valid_extra_fields = get_all_fields(McpConfigExtraFields)

        # Get keys that are not in the intersection of valid fields
        all_valid_fields = valid_mcp_server_fields | valid_extra_fields
        
        invalid_keys = [k for k in cfg if k not in all_valid_fields]
        
        if len(invalid_keys) > 0:
            logfire.error("Received an invalid key in mcp_config", invalid_keys=invalid_keys)
            raise ValueError("Received an invalid key in mcp_config", invalid_keys)

        server_cfg = {k: v for k, v in cfg.items() if k in valid_mcp_server_fields}

        if not server_cfg.get("tool_prefix"):
            server_cfg["tool_prefix"] = name

        mcp_server: MCPServerStdio | MCPServerStreamableHTTP

        if server_cfg.get("command"):
            mcp_server = MCPServerStdio(**server_cfg)
        elif server_cfg.get("url"):
            mcp_server = MCPServerStreamableHTTP(**server_cfg)
        mcp_servers[name] = McpConfig(internal_only=internal_only, mcp_server=mcp_server)
    return mcp_servers


class MCPLoader:
    """Lazy loader for MCP server configurations.

    This class loads MCP server configuration once during initialization
    and creates fresh server instances each time it's called. This pattern
    allows TigerAgent to reconnect to MCP servers for each request while
    reusing the same configuration.

    Args:
        config: Path to MCP configuration JSON file, or None for no servers
    """
    def __init__(self, config: Path | None):
        self._config = load_mcp_config(config) if config else {}

    def __call__(self) -> MCPDict:
        """Create fresh MCP server instances from the loaded configuration.

        Returns:
            Dictionary of configured MCP server instances ready for use
        """
        return create_mcp_servers(self._config)


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
    async def make_system_prompt(self, ctx: AgentResponseContext) -> str:
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

        Args:
            ctx: Template context containing event, user, bot info, etc.

        Returns:
            Rendered user prompt string
        """
        tmpl = self.jinja_env.get_template("user_prompt.md")
        text_prompt =  await tmpl.render_async(**ctx.model_dump())
        
        if ctx.mention.files is None or not len(ctx.mention.files):
            return text_prompt
        
        user_contents = [await download_private_file(file) for file in ctx.mention.files]
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
        ctx = AgentResponseContext(event=event, mention=mention, bot=self.bot_info, user=user_info)

        system_prompt = await self.make_system_prompt(ctx)
        user_prompt = await self.make_user_prompt(ctx)
        mcp_servers = self.mcp_loader()
        self.augment_mcp_servers(mcp_servers)
        
        channel_info = await fetch_channel_info(client=hctx.app.client, channel_id=event.event.channel)
        if channel_info is None:
            # default to shared if we can't fetch channel info to be conservative
            channel_is_shared = True
        else:
            channel_is_shared = channel_info.is_ext_shared or channel_info.is_shared
        
        toolsets = [mcp_config.mcp_server for mcp_config in mcp_servers.values() if not channel_is_shared or not mcp_config.internal_only]

        if channel_is_shared:
            total_tools = len(mcp_servers)
            available_tools = len(toolsets)
            removed_count = total_tools - available_tools
            if removed_count > 0:
                logfire.info("Tools were removed as channel is shared with external users", removed_count=removed_count, channel_id=event.event.channel)
        agent = Agent(
            model=self.model,
            deps_type=dict[str, Any],
            system_prompt=system_prompt,
            toolsets=toolsets
        )
    
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
