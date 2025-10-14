import asyncio
from dotenv import load_dotenv, find_dotenv
import jinja2
from jinja2 import FileSystemLoader
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Any

from tiger_agent import TigerAgent, HarnessContext, Command, SlackHarness
from tiger_agent.log_config import setup_logging
from tiger_agent.slack import post_ephermal


class Memory(BaseModel):
    id: str = Field(description="The unique identifier of this memory.")
    content: str = Field(description="The content of this memory.")
    source: str | None = Field(
        default=None,
        description="The source or origin of this memory. A deep URI to the origin of the fact is preferred (e.g., a specific URL, file path, or reference).",
    )
    created_at: str = Field(
        description="The date and time when this memory was created."
    )
    updated_at: str = Field(
        description="The date and time when this memory was last updated."
    )


class MemoriesResponse(BaseModel):
    memories: list[Memory] = Field(
        default=[], description="The list of memories found."
    )
    scope: str = Field(
        description="A unique identifier for the target set of memories. Can be any combination of user and application ids, as needed for scoping and personalization."
    )


class MemoryAgent(TigerAgent):
    async def command_processor(self, context: HarnessContext, command: Command) -> None:
        print("Command received:", command)
        if command.text.lower() == "list":
            mcp_servers = self.mcp_loader()
            mcp_memory = mcp_servers.get("memory")
            if mcp_memory:
                response = await mcp_memory.direct_call_tool("recall", {"scope": f"eon:{command.user_id}"}, None)
                memories = MemoriesResponse.model_validate(response).memories
                response_text = "Memories:\n" + "\n".join(f" - {mem.content}" for mem in memories)
                await post_ephermal(
                    context.app.client,
                    channel=command.channel_id or '',
                    user=command.user_id,
                    text=response_text,
                    replace_original=True,
                    delete_original=True
                )


def main():
    load_dotenv(dotenv_path=find_dotenv(usecwd=True))
    setup_logging()

    # build our agent
    agent = MemoryAgent(
        model="anthropic:claude-sonnet-4-5-20250929",
        mcp_config_path=Path(__file__).resolve().parent / "memory_mcp_config.json",
        jinja_env=jinja2.Environment(
            enable_async=True,
            loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "prompts")
        )
    )

    # create a harness for the processor
    harness = SlackHarness(
        agent,
    )

    # run the harness
    asyncio.run(harness.run())

if __name__ == "__main__":
    main()
