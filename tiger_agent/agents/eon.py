import asyncio
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import logfire
from psycopg_pool import AsyncConnectionPool
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent import AGENT_NAME
from tiger_agent.agents.docs import query_docs
from tiger_agent.agents.progress import add_message
from tiger_agent.agents.sales import query_sales_support
from tiger_agent.agents.types import AgentContext, BotInfo, Mention
from tiger_agent.mcp_servers import slack_mcp_server
from tiger_agent.utils.db import (
    MAX_ATTEMPTS,
    delete_app_mention,
    delete_expired_mentions,
    get_any_app_mention,
)
from tiger_agent.utils.slack import (
    post_response,
    react_to_mention,
    remove_reaction_from_mention,
)

EON_MODEL = os.environ.get("EON_MODEL", "anthropic:claude-sonnet-4-0")
WORKER_SLEEP_SECONDS = 60  # how long the worker sleeps between iterations
WORKER_MIN_JITTER_SECONDS = -15
WORKER_MAX_JITTER_SECONDS = 15


assert WORKER_SLEEP_SECONDS >= 60
assert WORKER_MIN_JITTER_SECONDS < WORKER_MAX_JITTER_SECONDS
assert WORKER_SLEEP_SECONDS - WORKER_MIN_JITTER_SECONDS > 10
assert WORKER_SLEEP_SECONDS + WORKER_MAX_JITTER_SECONDS < WORKER_SLEEP_SECONDS * 2


SYSTEM_PROMPT = """\
You are {bot_name}, a member of TigerData.

TigerData is a company who provides the fastest PostgreSQL platform for real-time, analytical, and agentic applications.

You are an orchestrator agent that uses specialized sub-agents to answer questions asked of you in Slack messages. You have access to the following tools:

**Available Sub-Agents:**
* **progress_agent_tool**: Use for team progress updates, activity summaries, project status reports, individual contributor analysis, and "Snooper of the Week" reports. Searches Slack, GitHub, Linear, and memory systems.
* **docs_agent_tool**: Use for technical questions about PostgreSQL, TimescaleDB, and TigerCloud platform. Provides documentation quotes, feature explanations, configuration guidance, and best practices.
* **sales_agent_tool**: Use for customer support questions, troubleshooting based on historical cases, and sales insights. Searches Salesforce support case data and customer histories.

**Tool Selection Guidelines:**
* For questions about team member activities, project progress, or work summaries → use **progress_agent_tool**
* For technical questions, documentation lookup, or platform features → use **docs_agent_tool**  
* For customer issues, support case history, or sales insights → use **sales_agent_tool**
* For general Slack context or conversation history → use Slack MCP tools directly

**Response Protocol:**
1. If the question is unclear, first search recent Slack messages in the channel/thread for context
2. Select the most appropriate sub-agent tool based on the question type
3. If no sub-agent is appropriate, use your general knowledge or explain limitations
4. Always be concise but thorough in your responses

If asked to do something that falls outside your purpose or abilities, respond with an explanation why you refuse to carry out the ask.

Respond in valid Markdown format, following these rules:
- DO NOT specify a language for code blocks.
- DO NOT use tildes for code blocks, always use backticks.
- DO NOT include empty lines at beginning or end of code blocks.
- DO NOT include tables
- When using block quotes, there MUST be an empty line after the block quote.
- When mentioning a slack channel or user and you know the ID, you should ONLY reference them using the format <#CHANNEL_ID> (e.g. <#C099AQDL9CZ>) for channels and <@USER_ID> (e.g. <@U123456>) for users.
- Your response MUST be less than 40,000 characters.
- For bullet points, you MUST ONLY use asterisks (*), not dashes (-), pluses (+), or any other character.
"""

eon_agent = Agent(
    EON_MODEL,
    deps_type=AgentContext,
    system_prompt=SYSTEM_PROMPT.format(bot_name=AGENT_NAME),
    toolsets=[slack_mcp_server()],
)

@eon_agent.system_prompt
def add_the_date(ctx: RunContext[AgentContext]) -> str:
    try:
        timezone = ZoneInfo(ctx.deps.user_timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return (
        f"User's current time: {datetime.now(timezone).strftime('%Y-%m-%d %H:%M:%S%z')}"
    )


@eon_agent.system_prompt
def add_bot_user_id(ctx: RunContext[AgentContext]) -> str:
    return f"Your Slack user ID is {ctx.deps.bot_user_id}."


@eon_agent.tool
async def progress_agent_tool(
    ctx: RunContext[AgentContext],
    message: str
) -> str:
    """Create progress summaries for team members and projects using Slack, GitHub, Linear, and memory data.
    
    This tool provides comprehensive analysis of individual contributor activity and project status by:
    - Analyzing Slack conversations and GitHub activity 
    - Supporting exact matching with @username and #channel prefixes
    - Providing both individual contributor and project/channel summaries
    - Creating "Snooper of the Week" reports with highlights across teams
    - Integrating data from Slack, GitHub, Linear, and user memory systems
    
    Use this tool for progress updates, team activity summaries, project status reports, and cross-platform collaboration insights."""
    result = await add_message(
        message=message,
        context=ctx.deps,
    )
    return result.summary


@eon_agent.tool
async def docs_agent_tool(
    ctx: RunContext[AgentContext],
    message: str
) -> str:
    """Query comprehensive documentation for PostgreSQL, TimescaleDB, and TigerCloud platform.
    
    This tool provides expert assistance with technical documentation by:
    - Searching through PostgreSQL, TimescaleDB, and TigerCloud documentation
    - Providing direct quotes and references from official documentation
    - Offering expert guidance on database concepts, features, and best practices
    - Handling queries about SQL syntax, performance optimization, and platform-specific features
    - Providing confidence levels when documentation is incomplete or unavailable
    
    Use this tool for technical questions, feature explanations, configuration guidance, troubleshooting help, and best practices related to the PostgreSQL ecosystem and TigerCloud platform."""
    result = await query_docs(
        message=message,
        context=ctx.deps,
    )
    return result


@eon_agent.tool
async def sales_agent_tool(
    ctx: RunContext[AgentContext],
    message: str
) -> str:
    """Search historical Salesforce support cases and customer data to provide sales and support insights.
    
    This tool provides access to comprehensive customer support history and sales data by:
    - Performing semantic searches through historical Salesforce support cases
    - Finding solutions to customer problems based on past successful resolutions
    - Retrieving detailed case summaries for specific support tickets
    - Identifying patterns in customer issues and support trends
    - Providing context about customer interactions and case histories
    - Generating insights for sales teams based on support case data
    
    Use this tool for customer support questions, troubleshooting based on historical cases, sales insights from support data, and understanding common customer issues and their resolutions."""
    result = await query_sales_support(
        message=message,
        context=ctx.deps,
    )
    return result


def user_prompt(mention: Mention) -> str:
    lines = []
    lines.append("<slack-message>")
    lines.append(f"<requesting-user>{mention.user}</requesting-user>")
    lines.append(f"<channel>{mention.channel}</channel>")
    lines.append(f"<ts>{mention.ts}</ts>")
    if mention.thread_ts:
        lines.append(f"<thread_ts>{mention.thread_ts}</thread_ts>")
    lines.append("</slack-message>")
    lines.append(f"Q: {mention.text}")
    return "\n".join(lines)

async def respond(
    pool: AsyncConnectionPool, client: AsyncWebClient, bot_info: BotInfo
) -> bool:
    with logfire.span("respond") as span:
        try:
            async with pool.connection() as con:
                mention = await get_any_app_mention(con)
                if not mention:
                    logfire.info("no mention found")
                    return False
                assert mention is not None
                span.set_attributes({"channel": mention.channel, "user": mention.user})
                try:
                    await react_to_mention(client, mention, "spinthinking")
                    async with eon_agent as agent:
                        # Slack messages are limited to 40k chars and 1 token ~= 4 chars
                        # https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them
                        # https://api.slack.com/methods/chat.postMessage#truncating
                        response = await agent.run(
                            deps=AgentContext(
                                user_timezone=mention.tz or "UTC",
                                bot_user_id=bot_info["user_id"],
                                thread_ts=mention.thread_ts,
                                channel=mention.channel,
                                user_id=mention.user,
                            ),
                            user_prompt=user_prompt(mention),
                            usage_limits=UsageLimits(response_tokens_limit=9_000),
                        )
                        await post_response(
                            client,
                            mention.channel,
                            mention.thread_ts if mention.thread_ts else mention.ts,
                            response.output,
                        )
                    await delete_app_mention(con, mention)
                    await remove_reaction_from_mention(client, mention, "spinthinking")
                    await react_to_mention(client, mention, "white_check_mark")
                    return True
                except Exception as e:
                    logfire.exception("respond failed", error_type=type(e).__name__)
                    await remove_reaction_from_mention(client, mention, "spinthinking")
                    await react_to_mention(client, mention, "x")
                    await post_response(
                        client,
                        mention.channel,
                        mention.thread_ts if mention.thread_ts else mention.ts,
                        "I experienced an issue trying to respond."
                        + " I will try again."
                        if mention.attempts < MAX_ATTEMPTS
                        else " I give up. Sorry.",
                    )
        except Exception as e:
            logfire.exception("respond failed", error_type=type(e).__name__)
        return False


async def respond_worker(
    pool: AsyncConnectionPool, client: AsyncWebClient, bot_info: BotInfo
) -> None:
    while True:
        with logfire.span("respond_worker"):
            while await respond(
                pool, client, bot_info
            ):  # while we are being successful, continue
                pass
            await delete_expired_mentions(pool)
        jitter = random.randint(WORKER_MIN_JITTER_SECONDS, WORKER_MAX_JITTER_SECONDS)
        delay = WORKER_SLEEP_SECONDS + jitter
        await asyncio.sleep(delay)
