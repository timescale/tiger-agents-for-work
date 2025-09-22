import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable, Sequence

import logfire
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.tools import Tool

from tiger_agent import EventContext, Event, EventProcessor

from tiger_agent.slack import add_reaction, post_response, remove_reaction
import tiger_agent.user_memory as mem

logger = logging.getLogger(__name__)


# Type alias for event processing callback
PromptGenerator = Callable[[EventContext, Event], Awaitable[str]]


def make_memory_tools(ctx: EventContext, user_id: str) -> list[Tool]:
    async def save_user_memory(memory: str) -> mem.UserMemory:
        """Save a new memory for the user. Use this to remember important information about the user for future conversations.

        Args:
            memory: The information to remember about the user (preferences, context, facts, etc.)
        """
        return await mem.insert_user_memory(ctx.pool, user_id, memory)

    async def update_user_memory(id: int, memory: str) -> None:
        """Update an existing user memory. Use this to modify or correct previously saved information.

        Args:
            id: The ID of the memory to update
            memory: The new memory content to replace the existing one
        """
        return await mem.update_user_memory(ctx.pool, id, user_id, memory)

    async def delete_user_memory(id: int) -> None:
        """Delete a user memory. Use this to remove information that is no longer relevant or accurate.

        Args:
            id: The ID of the memory to delete
        """
        return await mem.delete_user_memory(ctx.pool, id, user_id)

    async def list_user_memories() -> list[mem.UserMemory]:
        """Get all memories saved for the user. Use this to review what you know about the user."""
        return await mem.list_user_memories(ctx.pool, user_id)

    async def get_user_memory(id: int) -> mem.UserMemory | None:
        """Get a specific user memory by ID. Use this to retrieve details about a particular memory.

        Args:
            id: The ID of the memory to retrieve
        """
        return await mem.get_user_memory(ctx.pool, id, user_id)

    return [
        Tool(save_user_memory),
        Tool(update_user_memory),
        Tool(delete_user_memory),
        Tool(list_user_memories),
        Tool(get_user_memory),
    ]


def load_mcp_config(mcp_config: Path) -> dict[str, dict[str, Any]]:
    mcp_config: dict[str, dict[str, Any]] = json.loads(mcp_config.read_text()) if mcp_config else {}
    return mcp_config


@logfire.instrument("load_mcp_servers")
def load_mcp_servers(mcp_config: dict[str, dict[str, Any]]) -> dict[str, MCPServerStreamableHTTP]:
    mcp_servers: dict[str, MCPServerStreamableHTTP] = {}
    for name, cfg in mcp_config.items():
        if cfg.pop("disabled", False):
            continue
        mcp_servers[name] = MCPServerStreamableHTTP(**cfg)
    return mcp_servers


class TigerAgent:
    def __init__(
        self,
        system_prompt_generator: PromptGenerator,
        user_prompt_generator: PromptGenerator,
        model: str,
        model_settings: ModelSettings | None = None,
        mcp_config: dict[str, dict[str, Any]] = (),
        tools: Sequence[Tool] = (),
        builtin_tools: Sequence[AbstractBuiltinTool] = (),
    ):
        self._system_prompt_generator = system_prompt_generator
        self._user_prompt_generator = user_prompt_generator
        self._model = model
        self._model_settings = model_settings
        self._mcp_config = mcp_config
        self._tools = tools
        self._builtin_tools = builtin_tools

    def get_event_processor(self) -> EventProcessor:
        async def generate_system_prompt(ctx: EventContext, event: Event) -> str:
            with logfire.span("generate_system_prompt", event_id=event.id) as _:
                return await self._system_prompt_generator(ctx, event)

        async def generate_user_prompt(ctx: EventContext, event: Event) -> str:
            with logfire.span("generate_user_prompt", event_id=event.id) as _:
                return await self._user_prompt_generator(ctx, event)
    
        async def event_processor(ctx: EventContext, event: Event) -> None:
            mention = event.event
            try:
                await add_reaction(ctx, mention.channel, mention.ts, "spinthinking")
                with logfire.span("build_agent", event_id=event.id) as _:
                    system_prompt = await generate_system_prompt(ctx, event)
                    user_prompt = await generate_user_prompt(ctx, event)
                    tools = make_memory_tools(ctx, mention.user)
                    tools.extend(self._tools)
                    mcp_servers = load_mcp_servers(self._mcp_config)
                    agent = Agent(
                        self._model,
                        model_settings=self._model_settings,
                        toolsets=[mcp_server for mcp_server in mcp_servers.values()],
                        tools=tools,
                        builtin_tools=self._builtin_tools,
                        system_prompt=system_prompt,
                    )
                with logfire.span("run_agent", event_id=event.id) as _:
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
                    if event.attempts < 3
                    else " I give up. Sorry.",
                )
                raise e
        
        return event_processor
