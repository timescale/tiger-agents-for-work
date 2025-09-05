from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    user_timezone: str = Field(
        default="UTC",
        description="User's timezone for date/time formatting",
    )
    thread_ts: str | None = Field(
        None,
        description="Slack thread timestamp for fetching thread messages when in conversational context",
    )
    bot_user_id: str | None = Field(
        None,
        description="Bot's Slack user ID for filtering conversation history - messages from this ID are 'assistant messages', messages mentioning this ID are 'user messages'",
    )
    channel: str | None = Field(
        None, description="Slack channel ID where the conversation is taking place"
    )
    user_id: str | None = Field(
        None,
        description="The current user's Slack user id. This should be used for all operations related to memory.",
    )


@dataclass
class Mention:
    id: int
    ts: str
    channel: str
    user: str
    tz: str | None
    text: str
    thread_ts: str | None
    attempts: int
    vt: datetime


# See https://api.slack.com/methods/auth.test
class BotInfo(TypedDict):
    bot_id: str
    team_id: str
    user_id: str
    user: str
