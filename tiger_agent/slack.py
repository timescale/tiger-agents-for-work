"""Slack API integration utilities for Tiger Agent.

This module provides helper functions and data models for interacting with the Slack API.
It includes utilities for:

- Reaction management (adding/removing emoji reactions)
- User information retrieval and modeling
- Bot information retrieval and modeling
- Message posting with rich formatting

All functions are designed to be resilient, gracefully handling API errors and providing
structured data models for Slack entities.
"""

from typing import Any, Optional

import logfire
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


@logfire.instrument("add_reaction", extract_args=["channel", "ts", "emoji"])
async def add_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    """Add an emoji reaction to a Slack message.

    Gracefully handles API errors by silently ignoring them, making this safe
    to use for non-critical visual feedback without disrupting the main flow.

    Args:
        client: Slack AsyncWebClient for API calls
        channel: Slack channel ID where the message is located
        ts: Message timestamp identifier
        emoji: Emoji name (without colons, e.g., 'thumbsup', 'spinthinking')
    """
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


@logfire.instrument("remove_reaction", extract_args=["channel", "ts", "emoji"])
async def remove_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    """Remove an emoji reaction from a Slack message.

    Gracefully handles API errors by silently ignoring them, making this safe
    to use for cleanup operations without disrupting the main flow.

    Args:
        client: Slack AsyncWebClient for API calls
        channel: Slack channel ID where the message is located
        ts: Message timestamp identifier
        emoji: Emoji name (without colons, e.g., 'thumbsup', 'spinthinking')
    """
    try:
        await client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


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

    status_text: Optional[str] = None
    status_emoji: Optional[str] = None
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    real_name_normalized: Optional[str] = None
    display_name_normalized: Optional[str] = None
    email: Optional[str] = None
    team: Optional[str] = None


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
    deleted: bool
    color: Optional[str] = None
    real_name: Optional[str] = None
    tz: Optional[str] = None
    tz_label: Optional[str] = None
    tz_offset: Optional[int] = None
    profile: UserProfile


@logfire.instrument("fetch_user_info", extract_args=["user_id"])
async def fetch_user_info(client: AsyncWebClient, user_id: str) -> UserInfo | None:
    """Fetch comprehensive user information from Slack API.

    Retrieves user profile data including timezone information, which is essential
    for creating context-aware responses. Returns None on any API error to allow
    graceful degradation when user info is unavailable.

    Args:
        client: Slack AsyncWebClient for API calls
        user_id: Slack user ID to fetch information for

    Returns:
        UserInfo object with complete user data, or None if fetch failed
    """
    try:
        resp = await client.users_info(user=user_id, include_locale=True)
        assert isinstance(resp.data, dict)
        assert resp.data["ok"]
        return UserInfo(**(resp.data["user"]))
    except SlackApiError:
        return None


@logfire.instrument("post_response", extract_args=["channel", "thread_ts"])
async def post_response(
        client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> None:
    """Post a response message to Slack with rich formatting.

    Posts a message to a specific thread (or creates a new thread if thread_ts
    is a message timestamp). Uses markdown blocks for rich text formatting
    and disables link/media unfurling to keep responses clean.

    Args:
        client: Slack AsyncWebClient for API calls
        channel: Slack channel ID to post in
        thread_ts: Thread timestamp to reply to (or message ts to start thread)
        text: Message content with markdown formatting support

    Raises:
        SlackApiError: If message posting fails (not caught, allows caller to handle)
    """
    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[{"type": "markdown", "text": text}],
        unfurl_links=False,
        unfurl_media=False,
    )


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


@logfire.instrument("fetch_bot_info", extract_args=False)
async def fetch_bot_info(client: AsyncWebClient) -> BotInfo:
    """Fetch bot information using authenticated client.

    Combines data from auth.test and bots.info API calls to build complete
    bot identity information. This is typically called once per TigerAgent
    instance and cached for subsequent use in prompt templates.

    Args:
        client: Authenticated Slack AsyncWebClient

    Returns:
        BotInfo object with complete bot identity data

    Raises:
        AssertionError: If API calls fail or return unexpected data
        SlackApiError: If Slack API calls fail
    """
    auth_test_response = await client.auth_test()
    assert auth_test_response.get("ok"), "slack auth_test failed"

    bot_id = auth_test_response.get("bot_id")

    bots_info_response = await client.bots_info(bot=bot_id)
    assert bots_info_response.get("ok"), "slack bots_info failed"

    bot = bots_info_response.get("bot")
    assert isinstance(bot, dict), "bots_info_response has unexpected payload"

    bot_info = BotInfo(
        url=auth_test_response.get("url"),
        team=auth_test_response.get("team"),
        team_id=auth_test_response.get("team_id"),
        bot_id=bot_id,
        name=bot.get("name"),
        app_id=bot.get("app_id"),
        user_id=bot.get("user_id")
    )

    return bot_info

