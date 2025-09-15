import os

import logfire
from pydantic_ai import RunContext
from pydantic_ai.usage import UsageLimits
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent import Event, EventContext
from tiger_agent.agents import AGENT_NAME
from tiger_agent.agents.data_types import AgentContext, Mention
from tiger_agent.agents.docs import query_docs
from tiger_agent.agents.filtering_agent import FilteringAgent
from tiger_agent.agents.mcp import get_memories, get_user_metadata
from tiger_agent.agents.mcp_servers import slack_mcp_server, memory_mcp_server
from tiger_agent.agents.progress import add_message
from tiger_agent.agents.prompt import create_memory_prompt, create_user_metadata_prompt
from tiger_agent.agents.sales import query_sales_support

EON_MODEL = os.environ.get("EON_MODEL", "anthropic:claude-sonnet-4-20250514")

MAX_ATTEMPTS = 3

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
2. Select the most appropriate sub-agent tool(s) based on the question type - you may use multiple sub-agents when their combined insights would provide a more comprehensive answer
3. When using multiple sub-agents, synthesize their outputs into a cohesive response that highlights how the different perspectives complement each other
4. **IMPORTANT: When presenting output from sub-agents, preserve their original markdown formatting, including links, bold text, headers, and bullet points. Do not reformat or paraphrase their responses.**
5. If no sub-agent is appropriate, use your general knowledge or explain limitations
6. Always be concise but thorough in your responses

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

eon_agent = FilteringAgent(
    EON_MODEL,
    deps_type=AgentContext,
    system_prompt=SYSTEM_PROMPT.format(bot_name=AGENT_NAME),
    toolsets=[slack_mcp_server(), memory_mcp_server()],
)


@eon_agent.system_prompt
def add_bot_user_id(ctx: RunContext[AgentContext]) -> str:
    return f"Your Slack user ID is {ctx.deps.bot_user_id}."


eon_agent.system_prompt(create_memory_prompt)
eon_agent.system_prompt(create_user_metadata_prompt)


@eon_agent.tool
async def progress_agent_tool(ctx: RunContext[AgentContext], message: str) -> str:
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
async def docs_agent_tool(ctx: RunContext[AgentContext], message: str) -> str:
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
async def sales_agent_tool(ctx: RunContext[AgentContext], message: str) -> str:
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


async def add_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


async def remove_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    try:
        await client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


async def post_response(
        client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> None:
    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[{"type": "markdown", "text": text}],
        unfurl_links=False,
        unfurl_media=False,
    )

async def respond(ctx: EventContext, event: Event) -> None:
    if event.event.get("type") != "app_mention":
        logfire.warning("only app_mention events are handled", event=event)
    mention = Mention(
        id=event.id,
        ts=event.event["ts"],
        channel=event.event["channel"],
        user=event.event["user"],
        text=event.event["text"],
        thread_ts=event.event.get("thread_ts"),
        attempts=event.attempts,
        vt=event.vt
    )
    client = ctx.app.client
    with logfire.span("respond") as span:
        span.set_attributes({"channel": mention.channel, "user": mention.user})
        try:
            await add_reaction(client, channel=mention.channel, ts=mention.ts, emoji="spinthinking")
            async with eon_agent as agent:
                # Slack messages are limited to 40k chars and 1 token ~= 4 chars
                # https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them
                # https://api.slack.com/methods/chat.postMessage#truncating
                response = await agent.run(
                    deps=AgentContext(
                        bot_user_id=ctx.bot_id,
                        thread_ts=mention.thread_ts,
                        channel=mention.channel,
                        memories=await get_memories(mention.user),
                        slack_user_metadata=await get_user_metadata(mention.user),
                        user_id=mention.user,
                    ),
                    usage_limits=UsageLimits(output_tokens_limit=9_000),
                    user_prompt=user_prompt(mention),
                )

            await post_response(
                client,
                mention.channel,
                mention.thread_ts if mention.thread_ts else mention.ts,
                response.output,
            )
            await remove_reaction(client, channel=mention.channel, ts=mention.ts, emoji="spinthinking")
            await add_reaction(client, channel=mention.channel, ts=mention.ts, emoji="white_check_mark")
            return
        except Exception as e:
            logfire.exception("respond failed", error=e)
            await remove_reaction(client, channel=mention.channel, ts=mention.ts, emoji="spinthinking")
            await add_reaction(client, channel=mention.channel, ts=mention.ts, emoji="x")
            await post_response(
                client,
                mention.channel,
                mention.thread_ts if mention.thread_ts else mention.ts,
                "I experienced an issue trying to respond." + " I will try again."
                if mention.attempts < MAX_ATTEMPTS
                else " I give up. Sorry.",
            )
            raise e
