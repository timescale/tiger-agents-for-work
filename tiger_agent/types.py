

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from slack_bolt.app.async_app import AsyncApp


class BotInfo(BaseModel):
    """Pydantic model for Slack bot information.

    Represents the bot's identity and metadata within a Slack workspace.
    Used for building context-aware responses that can reference the bot's
    capabilities and identity.

    Attributes:
        url: Bot's workspace URL
        team: Team/workspace name
        team_id: Team/workspace identifier
        bot_id: Unique bot identifier
        name: Bot's display name
        app_id: Associated Slack app identifier
        user_id: Bot's user account identifier
    """
    model_config = {"extra": "allow"}

    url: str
    team: str
    team_id: str
    bot_id: str
    name: str
    app_id: str
    user_id: str
    
class UserProfile(BaseModel):
    """Pydantic model for Slack user profile information.

    Represents the profile section of a Slack user, containing display names,
    status information, and contact details. Allows extra fields to accommodate
    future Slack API changes.

    Attributes:
        status_text: User's current status message
        status_emoji: Emoji associated with user's status
        real_name: User's full real name
        display_name: User's chosen display name
        real_name_normalized: Normalized version of real name
        display_name_normalized: Normalized version of display name
        email: User's email address (if accessible)
        team: Team identifier
    """
    model_config = {"extra": "allow"}

    status_text: str | None = None
    status_emoji: str | None = None
    real_name: str | None = None
    display_name: str | None = None
    real_name_normalized: str | None = None
    display_name_normalized: str | None = None
    email: str | None = None
    team: str | None = None

class UserInfo(BaseModel):
    """Pydantic model for complete Slack user information.

    Represents a Slack user with all associated metadata including timezone,
    team membership, and profile details. Used for building context-aware
    responses that can reference user preferences and local time.

    Attributes:
        id: Unique user identifier
        team_id: Team/workspace identifier
        name: Username/handle
        deleted: Whether the user account is deleted
        color: User's display color in Slack UI
        real_name: User's real name (may differ from profile.real_name)
        tz: Timezone identifier (e.g., 'America/New_York')
        tz_label: Human-readable timezone label
        tz_offset: Timezone offset in seconds from UTC
        profile: Detailed profile information
    """
    model_config = {"extra": "allow"}

    id: str
    team_id: str
    name: str
    deleted: bool = False
    color: str | None = None
    real_name: str | None = None
    tz: str | None = None
    tz_label: str | None = None
    tz_offset: int | None = None
    profile: UserProfile
class SlackCommand(BaseModel):
    """This represents a partial definition of the command object emitted to a handler for a slash command."""
    channel_id: str | None = None
    channel_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    command: str | None = None
    text: str | None = None


@dataclass
class HarnessContext:
    """Shared context provided to event processors.

    This context gives event processors access to the Slack app for API calls,
    the database connection pool for data operations, and the task group for
    spawning concurrent tasks.

    Attributes:
        app: Slack Bolt AsyncApp for making Slack API calls
        pool: Database connection pool for PostgreSQL operations
    """
    app: AsyncApp
    pool: AsyncConnectionPool
    
@dataclass
class CommandContext:
    """Shared context provided to the command handlers."""
    hctx: HarnessContext
    command: SlackCommand


class SlackFile(BaseModel):
    """Pydantic model for Slack file objects.

    Represents files attached to Slack messages, including metadata
    about the file type, size, permissions, and various thumbnail URLs.
    """
    model_config = {"extra": "allow"}

    id: str
    name: str
    title: str
    mimetype: str
    filetype: str
    pretty_type: str
    url_private_download: str
    media_display_type: str
    size: int

class BaseEvent(BaseModel):
    """Base Pydantic model for Slack events.

    Represents the structure of a Slack app mention event as received from
    the Slack Events API. The model allows extra fields to accommodate
    future Slack API changes.

    Attributes:
        ts: Message timestamp
        thread_ts: Thread timestamp if this is a threaded message
        team: Slack team/workspace ID
        text: The text content of the message
        type: Event type (always 'app_mention')
        user: User ID who mentioned the app
        blocks: Slack Block Kit blocks if present
        channel: Channel ID where the mention occurred
        event_ts: Event timestamp from Slack
    """
    model_config = {"extra": "allow"}

    ts: str
    thread_ts: str | None = None
    team: str | None = None
    text: str
    type: str
    user: str
    blocks: list[dict[str, Any]] | None = None
    channel: str
    event_ts: str
    files: list[SlackFile] | None = None


class AppMentionEvent(BaseEvent):
    """Pydantic model for Slack app_mention events."""
    type: str = "app_mention"


class MessageEvent(BaseEvent):
    """Pydantic model for Slack message events."""
    type: str = "message"
    subtype: str | None = None


class Event(BaseModel):
    """Database representation of an event from the agent.event table.

    This model represents events stored in the PostgreSQL work queue table,
    including metadata for retry logic and worker coordination.

    Attributes:
        id: Primary key from agent.event table
        event_ts: Timestamp when the event occurred
        attempts: Number of processing attempts made
        vt: Visibility threshold - when event becomes available for processing
        claimed: Array of timestamps when event was claimed by workers
        event: The original Slack app mention event data
    """
    id: int
    event_ts: datetime
    attempts: int
    vt: datetime
    claimed: list[datetime]
    event: AppMentionEvent | MessageEvent
    
    
class AgentResponseContext(BaseModel):
    """Context object for AI agent responses containing event data and user information.

    This model serves as the context passed to Jinja2 templates for generating
    system and user prompts. It contains all necessary information about the
    Slack event, user details, and computed values like localized timestamps.

    Attributes:
        event: The database event record containing metadata and Slack event data
        mention: The specific app mention or message event that triggered processing
        bot: Information about the bot user (display name, user ID, etc.)
        user: Slack user information including timezone, or None if unavailable
        local_time: Event timestamp converted to user's local timezone, set automatically
    """
    event: Event
    mention: AppMentionEvent | MessageEvent
    bot: BotInfo
    user: UserInfo | None = None
    local_time: datetime | None = None

    def model_post_init(self, __context):
        """Automatically compute derived fields after model initialization.

        Sets the local_time field by converting the event timestamp to the
        user's timezone if user information is available. This ensures templates
        always have access to properly localized time information.
        """
        if self.user is not None:
            self.local_time = self.event.event_ts.astimezone(ZoneInfo(self.user.tz))