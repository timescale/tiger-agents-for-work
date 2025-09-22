import json
from pathlib import Path
from typing import Any, Callable

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerConfig

from tiger_agent import AgentHarness, Event, EventContext
from tiger_agent.logging_config import setup_logging


class TigerAgent:
    def __init__(
            self,
            model: str,
            system_prompt: str | Callable[[EventContext], str] | list[str] | list[Callable[[EventContext], str]],
            user_prompt: str | Callable[[EventContext, Event], str],
            mcp_config: str | dict = {},
            tools: list[Any] = [],
    ):
        self.agent = Agent(
            model=model,
        )
        self.user_prompt = user_prompt


        if isinstance(mcp_config, str):
            mcp_config_path = Path(mcp_config)
            if not mcp_config_path.exists():
                raise FileNotFoundError(f"MCP config file not found: {mcp_config}")
            self.mcp_config = json.loads(mcp_config_path.read_text())
        else:
            self.mcp_config = mcp_config

        self.tools = tools

        if isinstance(system_prompt, str):
            self.agent.system_prompt(lambda ctx: EventContext: system_prompt.format(ctx=ctx))
        elif isinstance(system_prompt, list):
            for sp in system_prompt:
                if isinstance(sp, str):
                    self.agent.system_prompt(lambda ctx: sp.format(ctx=ctx))
                else:
                    self.agent.system_prompt(sp)
        else:
            self.agent.system_prompt(system_prompt)
        self.agent.system_prompt()

    def get_tools(self):
        return [*self.tools, *MCPServerConfig.model_validate({"mcp_config": self.mcp_config})]

    async def respond(self, ctx: EventContext, event: Event):
        channel = event.event["channel"]
        ts = event.event["ts"]
        text = event.event["text"]
        async with self.agent as a:
            resp = await a.run(
                self.user_prompt.format(ctx=ctx, event=event) if isinstance(self.user_prompt, str) else self.user_prompt(ctx, event),
                toolsets=self.get_tools()
            )
        await ctx.app.client.chat_postMessage(
            channel=channel, thread_ts=ts, text=resp.output
        )

    async def run(self):
        # create the agent harness
        harness = AgentHarness(respond)

        # run the harness
        await harness.run(num_workers=5)
