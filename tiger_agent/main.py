import asyncio
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import find_dotenv, load_dotenv
from jinja2 import Environment, FileSystemLoader

from tiger_agent import AgentHarness, Event, EventContext
from tiger_agent.processor import build_event_processor
from tiger_agent.logging_config import setup_logging
from tiger_agent.slack import user_info

load_dotenv(dotenv_path=find_dotenv(usecwd=True))
setup_logging(service_name="eon")


async def main() -> None:
    template_env = Environment(
        enable_async=True,
        loader=FileSystemLoader(Path.cwd())
    )
    mcp_config = Path.cwd().joinpath("mcp_config.json")

    async def generate_system_prompt(ctx: EventContext, event: Event) -> str:
        tmpl = template_env.get_template("system_prompt.md")
        return await tmpl.render_async(bot_user_id=ctx.bot_user_id, bot_name=ctx.bot_name)
    
    
    async def generate_user_prompt(ctx: EventContext, event: Event) -> str:
        user = await user_info(ctx, event.event.user)
        
        # Convert event timestamp to user's timezone
        local_time = event.event_ts.astimezone(ZoneInfo(user.tz)) if user else None

        tmpl = template_env.get_template("user_prompt.md")
        return await tmpl.render_async(event=event, mention=event.event, user=user, local_time=local_time)


    # build an event processor
    event_processor = build_event_processor(
        "anthropic:claude-sonnet-4-20250514",
        generate_system_prompt,
        generate_user_prompt,
        mcp_config,
    )

    # create the agent harness for the event processor
    harness = AgentHarness(event_processor)

    # run the harness
    await harness.run(num_workers=5)


if __name__ == "__main__":
    asyncio.run(main())
