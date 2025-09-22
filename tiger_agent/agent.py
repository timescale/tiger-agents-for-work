import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import jinja2
import logfire
from jinja2 import FileSystemLoader
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from tiger_agent import HarnessContext, Event
from tiger_agent.harness import AppMentionEvent
from tiger_agent.slack import add_reaction, post_response, remove_reaction, \
    fetch_user_info, \
    fetch_bot_info, BotInfo

logger = logging.getLogger(__name__)


def load_mcp_config(mcp_config: Path) -> dict[str, dict[str, Any]]:
    mcp_config: dict[str, dict[str, Any]] = json.loads(mcp_config.read_text()) if mcp_config else {}
    return mcp_config


@logfire.instrument("create_mcp_servers")
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


class TigerAgent:
    def __init__(
        self,
        model: str,
        mcp_config_path: Path | None,
        jinja_env: jinja2.Environment | None = None,
        max_attempts: int = 3
    ):
        self._model = model
        self._mcp_config_path = mcp_config_path
        self._mcp_config: dict[str, dict[str, Any]] | None = None
        self._max_attempts = max_attempts
        self._jinja_env = jinja_env if jinja_env else jinja2.Environment(
            enable_async=True,
            loader=FileSystemLoader(Path.cwd())
        )
        self._bot_info: BotInfo | None = None
    
    @logfire.instrument("make_mcp_servers", extract_args=False)
    async def make_mcp_servers(self) -> dict[str, MCPServerStreamableHTTP | MCPServerStdio]:
        if not self._mcp_config_path:
            return {}
        if not self._mcp_config:
            self._mcp_config: dict[str, dict[str, Any]] = load_mcp_config(self._mcp_config_path)
        return create_mcp_servers(self._mcp_config)
    
    @logfire.instrument("make_context", extract_args=False)
    async def make_context(self, **kwargs) -> dict[str, Any]:
        hctx: HarnessContext = kwargs["hctx"]
        event: Event = kwargs["event"]
        mention: AppMentionEvent = event.event
        mcp_servers = kwargs["mcp_servers"]
        kwargs["mention"] = mention
        if not self._bot_info:
            self._bot_info = await fetch_bot_info(hctx.app.client)
        kwargs["bot"] = self._bot_info
        user_info = await fetch_user_info(hctx.app.client, mention.user)
        if user_info:
            kwargs["user"] = user_info
            kwargs["local_time"] = event.event_ts.astimezone(ZoneInfo(user_info.tz))
        return kwargs
    
    @logfire.instrument("make_system_prompt", extract_args=False)
    async def make_system_prompt(self, ctx: dict[str, Any]) -> str:
        tmpl = self._jinja_env.get_template("system_prompt.md")
        return await tmpl.render_async(**ctx)
    
    @logfire.instrument("make_user_prompt", extract_args=False)
    async def make_user_prompt(self, ctx: dict[str, Any]) -> str:
        tmpl = self._jinja_env.get_template("user_prompt.md")
        return await tmpl.render_async(**ctx)
    
    @logfire.instrument("respond", extract_args=False)
    async def respond(self, hctx: HarnessContext, event: Event) -> str:
        mcp_servers = await self.make_mcp_servers()
        ctx = await self.make_context(hctx=hctx, event=event, mcp_servers=mcp_servers)
        system_prompt = await self.make_system_prompt(ctx)
        user_prompt = await self.make_user_prompt(ctx)
        toolsets = [mcp_server for mcp_server in mcp_servers.values()]
        with logfire.span("build_and_run_agent") as _:
            agent = Agent(
                model=self._model,
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

    @logfire.instrument("event_processor", extract_args=False)
    async def __call__(self, ctx: HarnessContext, event: Event) -> None:
        client = ctx.app.client
        mention = event.event
        try:
            await add_reaction(client, mention.channel, mention.ts, "spinthinking")
            response = await self.respond(ctx, event)
            await post_response(client, mention.channel, mention.thread_ts if mention.thread_ts else mention.ts, response)
            await remove_reaction(client, mention.channel, mention.ts, "spinthinking")
            await add_reaction(client, mention.channel, mention.ts, "white_check_mark")
        except Exception as e:
            logger.exception("respond failed", exc_info=e)
            await remove_reaction(client, mention.channel, mention.ts, "spinthinking")
            await add_reaction(client, mention.channel, mention.ts, "x")
            await post_response(
                client,
                mention.channel,
                mention.thread_ts if mention.thread_ts else mention.ts,
                "I experienced an issue trying to respond. I will try again."
                if event.attempts < self._max_attempts
                else " I give up. Sorry.",
            )
            raise e
