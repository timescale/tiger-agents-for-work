import asyncio
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import find_dotenv, load_dotenv
from jinja2 import Environment, FileSystemLoader

from tiger_agent import AgentHarness, Event, EventContext
from tiger_agent.agent import TigerAgent, load_mcp_config
from tiger_agent.logging_config import setup_logging
from tiger_agent.slack import user_info
from tiger_agent.user_memory import list_user_memories

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
setup_logging(service_name="eon")


async def main() -> None:
    # load jinja templates from the filesystem
    template_env = Environment(
        enable_async=True,
        loader=FileSystemLoader(Path.cwd())
    )
    
    # load the mcp config from json file
    mcp_config = load_mcp_config(Path.cwd().joinpath("mcp_config.json"))

    # implement how we want to generate system prompts
    async def generate_system_prompt(ctx: EventContext, event: Event) -> str:
        tmpl = template_env.get_template("system_prompt.md")
        return await tmpl.render_async(bot_user_id=ctx.bot_user_id, bot_name=ctx.bot_name)
    
    # implement how we want to generate user prompts
    async def generate_user_prompt(ctx: EventContext, event: Event) -> str:
        mention = event.event
        user = await user_info(ctx, mention.user)
        memories = await list_user_memories(ctx.pool, mention.user)
        
        # Convert event timestamp to user's timezone
        local_time = event.event_ts.astimezone(ZoneInfo(user.tz)) if user else None

        tmpl = template_env.get_template("user_prompt.md")
        return await tmpl.render_async(
            event=event,
            mention=mention,
            user=user,
            memories=memories,
            local_time=local_time
        )

    # build our agent
    agent = TigerAgent(
        model="anthropic:claude-sonnet-4-20250514",
        system_prompt_generator=generate_system_prompt,
        user_prompt_generator=generate_user_prompt,
        mcp_config=mcp_config
    )

    # create the agent harness for the event processor
    harness = AgentHarness(agent.get_event_processor())

    # run the harness
    await harness.run(num_workers=5)


if __name__ == "__main__":
    asyncio.run(main())
