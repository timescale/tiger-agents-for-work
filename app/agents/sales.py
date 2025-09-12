from mcp_servers import salesforce_mcp_server
from pydantic_ai import Agent, RunContext

from app.data_types import AgentContext
from app.utils.prompt import create_memory_prompt

sales_agent = Agent(
    "anthropic:claude-sonnet-4-20250514",
    toolsets=[salesforce_mcp_server()],
    deps_type=AgentContext,
)

sales_agent.system_prompt(create_memory_prompt)


@sales_agent.system_prompt
def get_system_prompt(ctx: RunContext[AgentContext]) -> str:
    return """You are a helpful sales and customer support assistant for TigerData with access to historical Salesforce support cases.\
        
        You can search through past customer support cases to find solutions to problems and provide insights based on historical data.\
        When referencing specific cases, always include the case ID and any relevant URLs provided.\
        
        Use the available tools to:
        - Search for similar issues experienced by customers in the past
        - Retrieve detailed information about specific support cases
        - Provide solutions based on successful case resolutions
        - Identify patterns in customer issues
        
        Always be helpful, professional, and reference specific cases when providing solutions.\
        Be concise but thorough in your responses."""


async def query_sales_support(
    message: str,
    context: AgentContext,
) -> str:
    """Query Salesforce support case data and customer history using the sales agent"""
    async with sales_agent as agent:
        result = await agent.run(message, deps=context)
        return result
