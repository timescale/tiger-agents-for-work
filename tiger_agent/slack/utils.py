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
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import logfire
from pydantic_ai.messages import (
    AgentStreamEvent,
    BaseToolCallPart,
    BinaryContent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_sdk.errors import SlackApiError, SlackRequestError
from slack_sdk.web.async_client import (
    AsyncChatStream,
    AsyncSlackResponse,
    AsyncWebClient,
)

from tiger_agent.slack.constants import (
    CONFIRM_PROACTIVE_PROMPT,
    REJECT_PROACTIVE_PROMPT,
)
from tiger_agent.slack.types import (
    BotInfo,
    ChannelInfo,
    SlackFile,
    SlackMessageEvent,
    SlackUrlParts,
    UserInfo,
)
from tiger_agent.utils import file_type_supported


def parse_slack_user_name(mention_string: str) -> tuple[str, str] | None:
    """Parse Slack user mention format <@USER_ID|username> and return (username, user_id).

    Args:
        mention_string: String in format '<@U06S8H0V94P|nathan>'

    Returns:
        Tuple of (username, user_id) or None if pattern doesn't match.

    Example:
        parse_slack_user_name('<@U06S8H0V94P|nathan>') -> ('nathan', 'U06S8H0V94P')
    """
    match = re.match(r"<@([A-Z0-9]+)\|([^>]+)>", mention_string)
    if match:
        user_id, username = match.groups()
        return (username, user_id)
    logfire.warning(
        "Argument was not of expected format for a <@USER_ID|username> formatted Slack username + user id"
    )
    return (None, None)


def parse_slack_url(url: str) -> SlackUrlParts:
    """Parse a Slack message URL into its component parts."""

    parsed = urlparse(url)
    path_match = re.search(r"/archives/([^/]+)/p(\d+)", parsed.path)
    if not path_match:
        raise ValueError(f"Could not parse Slack URL path: {url}")

    channel_id = path_match.group(1)
    raw_ts = path_match.group(2)

    # p-prefix ts has no decimal -- insert before last 6 digits
    # Handles both with and without fractional seconds (though Slack always uses 6 digits)
    ts = f"{raw_ts[:-6]}.{raw_ts[-6:]}" if len(raw_ts) > 6 else f"0.{raw_ts.zfill(6)}"

    # thread_ts from query param may or may not have a decimal
    raw_thread_ts = parse_qs(parsed.query).get("thread_ts", [None])[0]
    if raw_thread_ts is not None and "." not in raw_thread_ts:
        # No decimal -- insert before last 6 digits
        if len(raw_thread_ts) > 6:
            thread_ts = f"{raw_thread_ts[:-6]}.{raw_thread_ts[-6:]}"
        else:
            thread_ts = f"0.{raw_thread_ts.zfill(6)}"
    else:
        thread_ts = raw_thread_ts

    return SlackUrlParts(channel_id=channel_id, ts=ts, thread_ts=thread_ts)


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


@logfire.instrument(
    "fetch_thread_replies", extract_args=["channel", "thread_ts", "limit"]
)
async def fetch_thread_messages(
    client: AsyncWebClient,
    channel: str,
    thread_ts: str,
    limit: int = 20,
) -> list[SlackMessageEvent]:
    """Fetch recent messages from a Slack thread for conversation history.

    Retrieves the most recent messages from a thread to provide context for
    the AI agent. Messages are returned in chronological order (oldest first)
    to preserve conversation flow.

    Args:
        client: Slack AsyncWebClient for API calls
        channel: Slack channel ID where the thread is located
        thread_ts: Thread timestamp identifier (the parent message ts)
        limit: Maximum number of messages to fetch (default 10)

    Returns:
        List of message objects in chronological order
    """
    try:
        # Fetch thread replies - Slack returns messages oldest-first by default
        # We request limit+1 because we'll exclude the current message
        resp = await client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=limit + 1,
            inclusive=True,
        )
        assert isinstance(resp.data, dict)
        assert resp.data["ok"]

        messages = resp.data.get("messages", [])
        thread_messages: list[SlackMessageEvent] = []

        for msg in messages:
            thread_messages.append(
                SlackMessageEvent(
                    **msg,
                    channel=channel,
                    event_ts=msg.get("ts", ""),
                )
            )

        return thread_messages

    except Exception:
        logfire.exception(
            "Failed to fetch thread replies", channel=channel, thread_ts=thread_ts
        )
        return []


@logfire.instrument("post_response", extract_args=["channel", "thread_ts"])
async def post_response(
    client: AsyncWebClient, channel: str, thread_ts: str | None, text: str
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


async def download_slack_hosted_file(
    file: SlackFile,
    slack_bot_token: str,
) -> BinaryContent | str | None:
    """This will download a file associated with a Slack message and return its contents. Note: only images, text, or PDFs are supported."""
    if not file_type_supported(file.mimetype):
        return "File type not supported"

    return await download_private_file(
        url_private_download=file.url_private_download,
        slack_bot_token=slack_bot_token,
    )


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
    is_busy: bool,
    message: str | None = None,
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


async def stream_response_to_mention(
    client: AsyncWebClient,
    slack_stream: AsyncChatStream | None,
    stream_event: AgentStreamEvent,
    channel_id: str,
    recipient_user_id: str,
    recipient_team_id: str,
    ts: str,
    thread_ts: str | None = None,
) -> AsyncChatStream | None:
    async def append(
        markdown_text: str, stream: AsyncChatStream | None = None
    ) -> AsyncChatStream:
        return await append_message_to_stream(
            client=client,
            channel_id=channel_id,
            recipient_user_id=recipient_user_id,
            recipient_team_id=recipient_team_id,
            thread_ts=thread_ts or ts,
            markdown_text=markdown_text,
            stream=stream,
        )

    # the beginning of a 'part'
    if isinstance(stream_event, PartStartEvent):
        # a text response start
        if isinstance(stream_event.part, TextPart) and stream_event.part.content:
            slack_stream = await append(
                markdown_text=stream_event.part.content, stream=slack_stream
            )

        # show tool call info in Slack Assistant status
        if isinstance(stream_event.part, BaseToolCallPart):
            await set_status(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts or ts,
                is_busy=True,
                message=f"Calling Tool: {stream_event.part.tool_name}",
            )

    # when a part changes there can be more text to append
    elif isinstance(stream_event, PartDeltaEvent):
        if (
            isinstance(stream_event.delta, TextPartDelta)
            and stream_event.delta.content_delta
        ):
            slack_stream = await append(
                markdown_text=stream_event.delta.content_delta,
                stream=slack_stream,
            )

    # at the end of text part, add some new lines
    # at the end of a tool call part, let's show the arguments in a codeblock
    elif isinstance(stream_event, PartEndEvent):
        if isinstance(stream_event.part, TextPart):
            slack_stream = await append(
                markdown_text="\n\n",
                stream=slack_stream,
            )
        if isinstance(stream_event.part, BaseToolCallPart):
            await set_status(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts or ts,
                is_busy=True,
            )

        # let's flush the buffer at the end of a part so that conversation is a flowin'
        if slack_stream is not None and slack_stream._buffer:
            try:
                await slack_stream._flush_buffer()
            except (SlackRequestError, SlackApiError) as e:
                # Stream might already be stopped (e.g., from early stop() call), log but continue
                logfire.exception("Failed to flush stream buffer", error=str(e))

                # if there is more in the buffer, let's create a new
                # stream and send what is in the buffer
                if slack_stream._buffer:
                    logfire.info("Could not flush buffer, appending to a new stream")
                    await append(markdown_text=slack_stream._buffer)

    return slack_stream


async def send_proactive_prompt(
    client: AsyncWebClient, channel: str, user: str, event_hist_id: int
):
    await client.chat_postEphemeral(
        channel=channel,
        user=user,
        text=f"Hey <@{user}>, would you like me to assist you?",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Hey <@{user}>, would you like me to assist you?",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": CONFIRM_PROACTIVE_PROMPT,
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Yes"},
                        "value": f"{event_hist_id}",
                    },
                    {
                        "type": "button",
                        "action_id": REJECT_PROACTIVE_PROMPT,
                        "text": {"type": "plain_text", "text": "No"},
                        "value": f"{event_hist_id}",
                    },
                ],
            },
        ],
    )


async def handle_proactive_prompt(
    ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond, bot_info: BotInfo
) -> int | None:
    await ack()

    actions = body.get("actions")
    if actions is None or not isinstance(actions, Sequence) or len(actions) != 1:
        logfire.error(
            "Actions was not an expected payload",
            event=body,
        )
        return
    action = actions[0]
    relevant_event_hist = action.get("value")

    action_id = action.get("action_id", REJECT_PROACTIVE_PROMPT)

    if action_id == REJECT_PROACTIVE_PROMPT:
        await respond(
            response_type="ephemeral",
            text="",
            replace_original=True,
            delete_original=True,
        )
        return

    if relevant_event_hist is None:
        logfire.error(
            "Could not find relevent event_hist for proactive agent response",
            event=body,
        )
        return

    await respond(
        response_type="ephemeral",
        text=f"I will respond to your message now! For future reference, you can include <@{bot_info.user_id}> in a message and I will respond.",
        replace_original=True,
        delete_original=True,
    )

    try:
        event_hist_id = int(relevant_event_hist)
        return event_hist_id
    except (ValueError, TypeError):
        logfire.error(
            "Invalid event_hist ID format",
            relevant_event_hist=relevant_event_hist,
            event=body,
        )
        return
