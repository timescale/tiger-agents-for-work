"""Tiger Agent - AI-powered Slack bot using Pydantic-AI with MCP server integration.

TigerAgent handles prompt generation and context augmentation for LLM-based task
handlers. It is designed to be subclassed for custom prompt and context behaviour.
Actual task dispatching is handled by TaskProcessor and the TaskHandler subclasses
in tiger_agent.tasks.handlers.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import logfire
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader
from pydantic import BaseModel
from pydantic_ai import models
from pydantic_ai.messages import UserContent

from tiger_agent.agent.types import (
    AgentResponseContext,
    ExtraContextDict,
)
from tiger_agent.mcp.types import MCPDict
from tiger_agent.mcp.utils import MCPLoader
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.salesforce.types import SalesforceBaseEvent
from tiger_agent.slack.types import BotInfo
from tiger_agent.slack.utils import download_private_file
from tiger_agent.utils import file_type_supported

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_REGEX = r"^system_prompt.*\.md$"
USER_PROMPT_REGEX = r"^user_prompt.*\.md$"


class TigerAgent:
    """Prompt generation and context augmentation for LLM-based task handlers.

    TigerAgent provides the building blocks for AI-powered responses:
    - Dynamic prompting via Jinja2 templates
    - MCP server loading and augmentation
    - Context augmentation hooks for subclasses

    It is injected into HarnessContext and accessed by handler classes that
    need LLM capabilities (SlackTaskHandler, SalesforceAssignmentChangedHandler).

    Args:
        model: Pydantic-AI model specification
        prompt_config: Sequence of PromptPackage instances or Path objects for extra prompt templates
        jinja_env: Pre-configured Jinja2 Environment (mutually exclusive with prompt_config)
        mcp_config_path: Path to MCP server configuration JSON file
        max_attempts: Maximum retry attempts for failed tasks (defaults to 3)
        rate_limit_allowed_requests: Maximum requests allowed per interval for rate limiting
        rate_limit_interval: Time interval for rate limiting (defaults to 1 minute)
        anthropic_cache_ttl: Prompt cache TTL applied to tool definitions, system
            prompt, and message history on Anthropic models ("5m" or "1h", defaults
            to "5m"). Pass None to disable prompt caching.
        compress_tool_results: Compact oversized JSON tool results before they
            reach the model by rendering arrays of similar objects as tables with
            constant fields factored out (defaults to True). No items are dropped;
            see tiger_agent.compression for thresholds.

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
        anthropic_cache_ttl: Literal["5m", "1h"] | None = "5m",
        compress_tool_results: bool = True,
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
        self.anthropic_cache_ttl = anthropic_cache_ttl
        self.compress_tool_results = compress_tool_results

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
        """Generate system prompt from Jinja2 templates matching *system_prompt.md."""
        return await self.render_prompts(SYSTEM_PROMPT_REGEX, ctx, extra_ctx)

    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(
        self, ctx: AgentResponseContext, extra_ctx: ExtraContextDict | None = None
    ) -> str | Sequence[UserContent]:
        """Generate user prompt from Jinja2 templates matching *user_prompt.md.

        If the mention contains attached files, downloads them and returns
        a sequence of UserContent objects (text + binary files) for multimodal
        processing by the AI agent.
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
            await download_private_file(file)
            for file in ctx.mention.files
            if file_type_supported(file.mimetype)
        ]
        return [*user_contents, *rendered_user_prompts]

    def augment_mcp_servers(self, mcp_servers: MCPDict):
        """Hook to augment loaded MCP servers before use.

        Override in subclasses to modify or add to the MCP servers created from
        configuration in-place (e.g. to add a process_tool_call callback).
        """

    async def augment_context(
        self, ctx: AgentResponseContext, extra_ctx: ExtraContextDict
    ) -> None:
        """Hook to augment context with additional BaseModel objects.

        Override in subclasses to add custom BaseModel instances to extra_ctx
        that will be available in Jinja2 templates.
        """
