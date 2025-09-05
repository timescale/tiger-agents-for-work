from mcp_servers import docs_mcp_server
from pydantic_ai import Agent, RunContext

from app.types import AgentContext

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
    context: AgentContext,
) -> str:
    """Query documentation using the docs agent"""
    async with docs_agent as agent:
        result = await agent.run(message, deps=context)
        return result