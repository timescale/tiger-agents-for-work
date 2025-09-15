from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from pydantic import BaseModel, Field


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
    key: str = Field(
        description="A unique identifier for the target set of memories. Can be any combination of user and application ids, as needed for scoping and personalization."
    )


class SlackUserResult(BaseModel):
    id: str = Field(description="The unique Slack user ID.")
    user_name: str = Field(description="The unique slack username.")
    real_name: str | None = Field(
        default=None,
        description="The full name of the user. This may contain diacritics or other special characters.",
    )
    display_name: str | None = Field(
        default=None,
        description="The user-specified display name. This may contain diacritics or other special characters.",
    )
    email: str | None = Field(default=None, description="The user's email address.")
    tz: str | None = Field(
        default=None,
        description="The user's timezone city/location (e.g. America/Chicago).",
    )
    is_bot: bool | None = Field(default=None, description="Whether this user is a bot.")


class SlackUsersResponse(BaseModel):
    results: list[SlackUserResult] = Field(
        default=[], description="The list of users found."
    )


@dataclass
class Mention:
    id: int
    ts: str
    channel: str
    user: str
    text: str
    thread_ts: str | None
    attempts: int
    vt: datetime


@dataclass
class SlackUser:
    id: str
    user_name: str
    real_name: str
    display_name: str
    email: str
    tz: str | None


class AgentContext(BaseModel):
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
    memories: list[Memory] | None = Field(
        None, description="An id:content map of the user's memories for this bot"
    )
    slack_user_metadata: SlackUserResult | None = Field(
        default=None, description="The metadata for the user's slack profile."
    )
    user_id: str | None = Field(
        None,
        description="The current user's Slack user id. This should be used for all operations related to memory.",
    )
