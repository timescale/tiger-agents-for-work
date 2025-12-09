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

import os

import httpx
import logfire
from pydantic import BaseModel
from pydantic_ai.messages import BinaryContent
from slack_sdk.errors import SlackApiError, SlackRequestError
from slack_sdk.web.async_client import (
    AsyncChatStream,
    AsyncSlackResponse,
    AsyncWebClient,
)

from tiger_agent.types import BotInfo, UserInfo


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
    except Exception:
        logfire.exception("Failed to fetch user info", user_id=user_id)
        return None


@logfire.instrument("post_response", extract_args=["channel", "thread_ts"])
async def post_response(
    client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> AsyncSlackResponse:
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
    return await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[{"type": "markdown", "text": text}],
        unfurl_links=False,
        unfurl_media=False,
    )


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
        user_id=bot.get("user_id"),
    )

    return bot_info


@logfire.instrument("fetch_channel_info", extract_args=["channel_id"])
async def fetch_channel_info(
    client: AsyncWebClient, channel_id: str
) -> ChannelInfo | None:
    """Fetch comprehensive channel information from Slack API.

    Retrieves channel metadata including privacy settings and sharing status.
    Returns None on any API error to allow graceful degradation when channel
    info is unavailable.

    Args:
        client: Slack AsyncWebClient for API calls
        channel_id: Slack channel ID to fetch information for

    Returns:
        ChannelInfo object with complete channel data, or None if fetch failed
    """
    try:
        resp = await client.conversations_info(channel=channel_id)
        assert isinstance(resp.data, dict)
        assert resp.data["ok"]
        return ChannelInfo(**(resp.data["channel"]))
    except Exception:
        logfire.exception("Failed to fetch channel info", channel_id=channel_id)
        return None


async def download_private_file(
    url_private_download: str, slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN")
) -> BinaryContent | str | None:
    """Download a private Slack file using the bot token for authentication.

    Downloads the content of a private Slack file by making an authenticated
    HTTP request to the file's private download URL. This is necessary because
    private files require authorization headers to access.

    Args:
        file: SlackFile object containing the private download URL and metadata

    Returns:
        BinaryContent object for binary files, string for text files, or error message string if download fails

    Raises:
        Returns error message string instead of raising exceptions for validation errors,
        HTTP errors, or authentication failures
    """
    try:
        if url_private_download is None:
            raise ValueError("No private url provided")

        if not slack_bot_token:
            raise ValueError("Cannot fetch file without a token")

        async with httpx.AsyncClient() as client:
            # Download file using bot token for authentication
            resp = await client.get(
                url=url_private_download,
                headers={"Authorization": f"Bearer {slack_bot_token}"},
            )
            resp.raise_for_status()

            media_type = resp.headers["content-type"]

            if not media_type:
                raise ValueError("Cannot determine file content type")

            # For text files, return string content
            if media_type.startswith("text/"):
                return resp.content.decode("utf-8")

            # For binary files, return BinaryContent
            return BinaryContent(data=resp.content, media_type=media_type)
    except Exception as e:
        return f"Could not fetch file: {str(e)}"


async def set_status(
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str,
    message: str | None = None,
    is_busy: bool = True,
) -> AsyncSlackResponse:
    """Set the status indicator for an assistant thread.

    Args:
        client: Slack web client for API calls
        channel_id: ID of the Slack channel containing the thread
        thread_ts: Timestamp of the thread to update status for
        message: Custom status message to display (truncated to 47 chars if longer)
        is_busy: Whether to show busy status with loading messages

    Returns:
        Response from Slack API

    Note:
        If message is None and is_busy=True, displays random loading messages.
        Exceptions are logged but not re-raised.
    """
    truncated_message = (
        message[:47] + "..." if message and len(message) > 50 else message
    )
    try:
        return await client.assistant_threads_setStatus(
            channel_id=channel_id,
            thread_ts=thread_ts,
            status="is responding..." if is_busy else "",
            loading_messages=[truncated_message]
            if truncated_message
            else [
                "Prowling for info...",
                "Hunting for the truth...",
                "Stalking data...",
                "Getting ready to pounce on the answer...",
                "Fishing up the right stream...",
                "Devouring data...",
                "Chuffling...",
                "Pacing...",
            ],
        )
    except Exception:
        logfire.exception("Failed to set status of assistant", message=message)


async def append_message_to_stream(
    client: AsyncWebClient,
    channel_id: str,
    recipient_user_id: str,
    recipient_team_id: str,
    thread_ts: str,
    markdown_text: str,
    should_retry: bool = True,
    stream: AsyncChatStream | None = None,
) -> AsyncChatStream:
    """Append markdown text to a Slack chat stream.

    Args:
        client: Slack web client for API calls
        channel_id: ID of the Slack channel
        recipient_user_id: User ID of the message recipient
        recipient_team_id: Team ID of the recipient
        thread_ts: Timestamp of the thread to append to
        markdown_text: Markdown-formatted text to append
        should_retry: Whether to retry once on failure
        stream: Existing stream to use, or None to create new one

    Returns:
        The chat stream that was used/created

    Raises:
        Exception: If append fails and should_retry=False, or retry also fails

    Note:
        Automatically retries once on failure by creating a new stream.
    """
    stream_to_use = (
        stream
        if stream
        else await client.chat_stream(
            channel=channel_id,
            recipient_user_id=recipient_user_id,
            recipient_team_id=recipient_team_id,
            thread_ts=thread_ts,
        )
    )

    try:
        await stream_to_use.append(markdown_text=markdown_text)
        return stream_to_use
    except (SlackRequestError, SlackApiError) as slack_error:
        logfire.exception(
            "Slack Error occurred while calling append_message_to_stream",
            markdown_text=markdown_text,
        )
        if not should_retry:
            raise slack_error

        # if we get this error, let's retry one time
        # retrying is going to create a new stream with the same
        # params
        return await append_message_to_stream(
            channel_id=channel_id,
            client=client,
            recipient_user_id=recipient_user_id,
            recipient_team_id=recipient_team_id,
            thread_ts=thread_ts,
            markdown_text=markdown_text,
            should_retry=False,
        )
    except Exception as error:
        logfire.exception(
            "Unknown exception occurred while calling append_message_to_stream",
            markdown_text=markdown_text,
        )
        raise error
