import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import logfire
from jinja2 import Environment, FileSystemLoader
from pydantic_ai import Agent, UsageLimits, models
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from tiger_agent import HarnessContext, Event
from tiger_agent.harness import AppMentionEvent
from tiger_agent.slack import fetch_user_info, fetch_bot_info, BotInfo, add_reaction, \
    post_response, remove_reaction

logger = logging.getLogger(__name__)


@logfire.instrument("load_mcp_config")
def load_mcp_config(mcp_config: Path) -> dict[str, dict[str, Any]]:
    mcp_config: dict[str, dict[str, Any]] = json.loads(mcp_config.read_text()) if mcp_config else {}
    return mcp_config


@logfire.instrument("create_mcp_servers", extract_args=False)
def create_mcp_servers(mcp_config: dict[str, dict[str, Any]]) -> dict[str, MCPServerStreamableHTTP | MCPServerStdio]:
    mcp_servers: dict[str, MCPServerStreamableHTTP | MCPServerStdio] = {}
    for name, cfg in mcp_config.items():
        if cfg.pop("disabled", False):
            continue
        if cfg.get("command"):
            mcp_servers[name] = MCPServerStdio(**cfg)
        elif cfg.get("url"):
            mcp_servers[name] = MCPServerStreamableHTTP(**cfg)
    return mcp_servers


class MCPLoader:
    def __init__(self, config: Path | None):
        self._config = load_mcp_config(config) if config else {}
    
    def __call__(self) -> dict[str, MCPServerStreamableHTTP | MCPServerStdio]:
        return create_mcp_servers(self._config)


class TigerAgent:
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
        tmpl = self.jinja_env.get_template("system_prompt.md")
        return await tmpl.render_async(**ctx)
    
    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(self, ctx: dict[str, Any]) -> str:
        tmpl = self.jinja_env.get_template("user_prompt.md")
        return await tmpl.render_async(**ctx)
    
    @logfire.instrument("generate_response", extract_args=False)
    async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
        ctx: dict[str, Any] = {}
        ctx["event"] = event
        mention: AppMentionEvent = event.event
        ctx["mention"] = mention
        if not self.bot_info:
            self.bot_info = await fetch_bot_info(hctx.app.client)
        user_info = await fetch_user_info(hctx.app.client, mention.user)
        if user_info:
            ctx["user"] = user_info
            ctx["local_time"] = event.event_ts.astimezone(ZoneInfo(user_info.tz))
        system_prompt = await self.make_system_prompt(ctx)
        user_prompt = await self.make_user_prompt(ctx)
        mcp_servers = self.mcp_loader()
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

    async def __call__(self, hctx: HarnessContext, event: Event) -> None:
        client = hctx.app.client
        mention = event.event
        try:
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
