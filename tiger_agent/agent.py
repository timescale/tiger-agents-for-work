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
from pathlib import Path
from typing import Any, TypeAlias
from zoneinfo import ZoneInfo

import logfire
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, UsageLimits, models
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from tiger_agent import HarnessContext, Interaction
from tiger_agent.harness import AppMentionEvent, BaseEvent, Command, SlackProcessor
from tiger_agent.slack import fetch_user_info, fetch_bot_info, BotInfo, add_reaction, \
    post_response, remove_reaction

logger = logging.getLogger(__name__)

MCPDict: TypeAlias = dict[str, MCPServerStreamableHTTP | MCPServerStdio]

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
        if cfg.pop("disabled", False):
            continue
        if not cfg.get("tool_prefix"):
            cfg["tool_prefix"] = name
        if cfg.get("command"):
            mcp_servers[name] = MCPServerStdio(**cfg)
        elif cfg.get("url"):
            mcp_servers[name] = MCPServerStreamableHTTP(**cfg)
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


class TigerAgent(SlackProcessor):
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

    @logfire.instrument("make_system_prompt", extract_args=False)
    async def make_system_prompt(self, ctx: dict[str, Any]) -> str:
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
        return await tmpl.render_async(**ctx)

    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(self, ctx: dict[str, Any]) -> str:
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
        return await tmpl.render_async(**ctx)

    def augment_mcp_servers(self, mcp_servers: MCPDict):
        """Hook to augment loaded MCP servers before use.

        This method can be overridden in subclasses to modify or add to the
        MCP servers created from configuration in-place. This can be useful
        for adding a `process_tool_call` callback on servers for example.

        Args:
            mcp_servers: Dictionary of loaded MCP servers
        """
        pass

    @logfire.instrument("generate_response", extract_args=False)
    async def generate_response(self, hctx: HarnessContext, event: BaseEvent) -> str:
        """Generate AI response to a Slack app_mention event.

        This is the core logic that:
        1. Builds context from interaction data, user info, and bot info
        2. Renders system and user prompts from Jinja2 templates
        3. Creates a Pydantic-AI agent with MCP server toolsets
        4. Runs the AI model to generate a response
        5. Returns the generated response text

        The context dictionary provides templates with access to:
        - interaction: The full Interaction object with processing metadata
        - mention: The AppMentionEvent with Slack message details
        - bot: Bot information and capabilities
        - user: User profile information including timezone
        - local_time: Event timestamp in user's local timezone

        Args:
            hctx: Harness context providing Slack app and database access
            interaction: The Slack interaction to process

        Returns:
            Generated AI response text ready for posting to Slack
        """
        ctx: dict[str, Any] = {}
        ctx["event"] = event
        if not self.bot_info:
            self.bot_info = await fetch_bot_info(hctx.app.client)
        ctx["bot"] = self.bot_info
        user_info = await fetch_user_info(hctx.app.client, event.user)
        if user_info:
            ctx["user"] = user_info
            ctx["local_time"] = event.event_ts.astimezone(ZoneInfo(user_info.tz))
        system_prompt = await self.make_system_prompt(ctx)
        user_prompt = await self.make_user_prompt(ctx)
        mcp_servers = self.mcp_loader()
        self.augment_mcp_servers(mcp_servers)
        toolsets = [mcp_server for mcp_server in mcp_servers.values()]
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

    async def event_processor(self, hctx: HarnessContext, event: BaseEvent) -> None:
        """Process a Slack app_mention event with full interaction flow.

        This method implements the complete EventProcessor interface for the
        SlackHarness system. It provides rich Slack interaction patterns:

        Success Flow:
        1. Add :spinthinking: reaction to show processing started
        2. Generate AI response using MCP tools
        3. Post response in thread (or as reply if not threaded)
        4. Remove :spinthinking: and add :white_check_mark: for success

        Failure Flow:
        1. Remove :spinthinking: reaction
        2. Add :x: reaction to indicate failure
        3. Post user-friendly error message
        4. Re-raise exception for SlackHarness retry logic

        The error message adapts based on retry attempts:
        - During retries: "I will try again."
        - Final failure: "I give up. Sorry."

        Args:
            hctx: Harness context providing Slack app and database access
            event: The Slack event to process (not the full Interaction object)

        Raises:
            Exception: Re-raises any processing exceptions for SlackHarness retry handling
        """
        client = hctx.app.client
        try:
            await add_reaction(client, event.channel, event.ts, "spinthinking")
            response = await self.generate_response(hctx, event)
            await post_response(client, event.channel, event.thread_ts if event.thread_ts else event.ts, response)
            await remove_reaction(client, event.channel, event.ts, "spinthinking")
            await add_reaction(client, event.channel, event.ts, "white_check_mark")
        except Exception as e:
            logger.exception("response failed", exc_info=e)
            await remove_reaction(client, event.channel, event.ts, "spinthinking")
            await add_reaction(client, event.channel, event.ts, "x")
            await post_response(
                client,
                event.channel,
                event.thread_ts if event.thread_ts else event.ts,
                "I experienced an issue trying to respond. I will try again.",
            )
            raise e
