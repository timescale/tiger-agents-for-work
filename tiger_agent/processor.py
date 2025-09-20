import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

import logfire
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from tiger_agent import EventContext, Event, EventProcessor

from tiger_agent.slack import add_reaction, post_response, remove_reaction

logger = logging.getLogger(__name__)


# Type alias for event processing callback
PromptGenerator = Callable[[EventContext, Event], Awaitable[str]]


def build_event_processor(
    model: str,
    system_prompt_generator: PromptGenerator,
    user_prompt_generator: PromptGenerator,
    mcp_config: Path | None,
) -> EventProcessor:

    mcp_config: list[dict[str, Any]] = json.loads(mcp_config.read_text()) if mcp_config else []
    @logfire.instrument("load_mcp_servers")
    def load_mcp_servers() -> list[MCPServerStreamableHTTP]:
        mcp_servers: list[MCPServerStreamableHTTP] = []
        for cfg in mcp_config:
            mcp_servers.append(MCPServerStreamableHTTP(**cfg))
        return mcp_servers

    async def generate_system_prompt(ctx: EventContext, event: Event) -> str:
        with logfire.span("generate_system_prompt", event_id=event.id) as _:
            return await system_prompt_generator(ctx, event)

    async def generate_user_prompt(ctx: EventContext, event: Event) -> str:
        with logfire.span("generate_user_prompt", event_id=event.id) as _:
            return await user_prompt_generator(ctx, event)

    @logfire.instrument("event_processor")
    async def event_processor(ctx: EventContext, event: Event) -> None:
        mention = event.event
        try:
            await add_reaction(ctx, mention.channel, mention.ts, "spinthinking")
            system_prompt = await generate_system_prompt(ctx, event)
            user_prompt = await generate_user_prompt(ctx, event)
            mcp_servers = load_mcp_servers()
            with logfire.span("run_agent", event_id=event.id) as _:
                agent = Agent(
                    model,
                    toolsets=mcp_servers,
                    system_prompt=system_prompt
                )
                async with agent as a:
                    resp = await a.run(user_prompt)
            await post_response(ctx, mention.channel, mention.thread_ts if mention.thread_ts else mention.ts, resp.output)
            await remove_reaction(ctx, mention.channel, mention.ts, "spinthinking")
            await add_reaction(ctx, mention.channel, mention.ts, "white_check_mark")
        except Exception as e:
            logger.exception("respond failed", exc_info=e)
            await remove_reaction(ctx, mention.channel, mention.ts, "spinthinking")
            await add_reaction(ctx, mention.channel, mention.ts, "x")
            await post_response(
                ctx,
                mention.channel,
                mention.thread_ts if mention.thread_ts else mention.ts,
                "I experienced an issue trying to respond. I will try again."
                if mention.attempts < 3
                else " I give up. Sorry.",
            )
            raise e

    return event_processor
