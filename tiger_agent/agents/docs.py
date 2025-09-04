from pydantic_ai import Agent, RunContext

from tiger_agent.agents.types import AgentContext
from tiger_agent.mcp_servers import docs_mcp_server

docs_agent = Agent(
    "anthropic:claude-sonnet-4-20250514",
    toolsets=[
        docs_mcp_server()
    ],
    deps_type=AgentContext,
)

@docs_agent.system_prompt
def get_system_prompt(ctx: RunContext[AgentContext]) -> str:
    return """You are a helpful assistant with expertise in PostgreSQL, Timescaledb, and TigerCloud.\
        Always consult the documentation and provide quotes in your answer.

        If you are unable to find relevant documentation, state explicitly that you could not find a direct answer in documentation, then provide your best guess. State your confidence level.\
        Be concise, but thoroughly answer the question."""


async def query_docs(
    message: str,
    user_timezone: str = "UTC",
    bot_user_id: str | None = None,
    thread_ts: str | None = None,
    channel: str | None = None,
    user_id: str | None = None,
) -> str:
    """Query documentation using the docs agent"""
    context = AgentContext(
        user_timezone=user_timezone,
        bot_user_id=bot_user_id,
        thread_ts=thread_ts,
        channel=channel,
        user_id=user_id,
    )

    async with docs_agent as agent:
        result = await agent.run(message, deps=context)
        return result