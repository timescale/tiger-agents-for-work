from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, model_validator


class ChannelInfo(BaseModel):
    """Pydantic model for Slack channel information.

    Represents channel metadata and properties within a Slack workspace.
    Used for building context-aware responses based on channel type and settings.

    Attributes:
        id: Unique channel identifier
        name: Channel name
        is_channel: Whether this is a public channel
        is_group: Whether this is a private group
        is_im: Whether this is a direct message
        is_mpim: Whether this is a multi-party direct message
        is_private: Whether the channel is private
        is_archived: Whether the channel is archived
        is_shared: Whether the channel is shared with external orgs
        is_ext_shared: Whether externally shared
        is_member: Whether the bot is a member
    """

    model_config = {"extra": "allow"}

    id: str
    name: str | None = None
    is_channel: bool | None = None
    is_group: bool | None = None
    is_im: bool | None = None
    is_mpim: bool | None = None
    is_private: bool | None = None
    is_archived: bool | None = None
    is_shared: bool | None = None
    is_ext_shared: bool | None = None
    is_member: bool | None = None


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
    is_restricted: bool = False
    is_ultra_restricted: bool = False
    is_external: bool = False

    @model_validator(mode="after")
    def set_is_external(self) -> "UserInfo":
        self.is_external = self.is_restricted or self.is_ultra_restricted
        return self


@dataclass
class SlackUrlParts:
    channel_id: str
    ts: str
    thread_ts: str | None


@dataclass
class SlackMessage(SlackUrlParts):
    text: str
    to_user_id: str | None = None


class SlackCommand(BaseModel):
    """This represents a partial definition of the command object emitted to a handler for a slash command."""

    channel_id: str | None = None
    channel_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    command: str | None = None
    text: str | None = None


class SlackFile(BaseModel):
    """Pydantic model for Slack file objects.

    Represents files attached to Slack messages, including metadata
    about the file type, size, permissions, and various thumbnail URLs.
    """

    model_config = {"extra": "allow"}

    id: str
    name: str | None = None
    title: str | None = None
    mimetype: str | None = None
    filetype: str | None = None
    pretty_type: str | None = None
    url_private_download: str | None = None
    media_display_type: str | None = None
    size: int | None = None


class SlackBaseEvent(BaseModel):
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


class SlackAppMentionEvent(SlackBaseEvent):
    """Pydantic model for Slack app_mention events."""

    type: str = "app_mention"


class SlackMessageEvent(SlackBaseEvent):
    """Pydantic model for Slack message events."""

    type: str = "message"
    subtype: str | None = None
