from datetime import datetime
from textwrap import dedent
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_ai import RunContext

from app import AGENT_NAME
from app.data_types import AgentContext


async def create_memory_prompt(ctx: RunContext[AgentContext]) -> str:
    memories = ctx.deps.memories
    return dedent(f"""\
    You have memory tools which may be used to store and retrieve important notes about the user.
    User-specific memories use a key of the format `{AGENT_NAME}:<USER_ID>`.
    The key for this user is `{AGENT_NAME}:{ctx.deps.user_id}`. You MUST use this key if you choose to store/retrieve memories about this user.
    Assume the newest memory is most accurate and supersedes older conflicting memories.
    When a newer memory conflicts with an older memory, either delete or update the older memory.
    Prefer to update an existing memory over creating a new one if the existing memory is very relevant.
    If there are redundant memories, update one with the semantic sum of the two and remove the other.

    The current memories for this user are:
    {"I was unable to retrieve your memories" if memories is None else "\n".join(f"ID {m.id} - {m.content}" for m in memories)}
    """)


async def create_user_metadata_prompt(ctx: RunContext[AgentContext]) -> str:
    try:
        user = ctx.deps.slack_user_metadata

        if user is not None and user.tz is not None:
            timezone = ZoneInfo(user.tz)
        else:
            timezone = ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return f"""User's Slack metadata: {user.model_dump_json(exclude={"tz"})}\n
        User's current time: {datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S%z")}"""
