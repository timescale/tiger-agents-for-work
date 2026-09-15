from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import PartEndEvent, TextPart
from slack_sdk.errors import SlackApiError, SlackRequestError
from slack_sdk.models.messages.chunk import TaskUpdateChunk
from slack_sdk.web.async_client import AsyncChatStream

from tiger_agent.slack.utils import (
    append_message_to_stream,
    get_channel_link,
    get_handle_link,
    post_response,
    stream_response_to_mention,
    user_is_external,
)


class TestGetHandleLink:
    def test_wraps_user_id_in_slack_mention_syntax(self):
        assert get_handle_link("U12345") == "<@U12345>"


class TestGetChannelLink:
    def test_wraps_channel_id_in_slack_channel_syntax(self):
        assert get_channel_link("C98765") == "<#C98765>"


class TestUserIsExternal:
    def test_same_team_regular_user_is_internal(self, make_bot_info, make_user_info):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME")
        assert user_is_external(bot_info=bot, user_info=user) is False

    def test_different_team_is_external(self, make_bot_info, make_user_info):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_OTHER")
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_restricted_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_restricted=True)
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_ultra_restricted_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_ultra_restricted=True)
        assert user_is_external(bot_info=bot, user_info=user) is True

    def test_stranger_user_is_external_even_on_same_team(
        self, make_bot_info, make_user_info
    ):
        bot = make_bot_info(team_id="T_HOME")
        user = make_user_info(team_id="T_HOME", is_stranger=True)
        assert user_is_external(bot_info=bot, user_info=user) is True


class TestPostMessage:
    async def test_should_pass_all_fields_to_post_message(
        self, make_async_web_client_mock
    ):
        message = "this is a message"
        client = make_async_web_client_mock()
        await post_response(
            client=client,
            channel="channel",
            text=message,
            thread_ts="thread_ts",
        )

        assert client.chat_postMessage.call_count == 1
        client.chat_postMessage.assert_called_with(
            channel="channel",
            text=message,
            thread_ts="thread_ts",
            unfurl_links=False,
            unfurl_media=False,
        )

    async def test_should_split_large_markdown_text_into_multiple_calls(
        self, make_async_web_client_mock
    ):
        # 15 characters per line * 1000 = 15k, exceeds the max length for markdown
        # in a single message
        text = "## Heading" + "\nthis is a line\n\n" * 1000

        client = make_async_web_client_mock()
        await post_response(
            client=client,
            channel="channel",
            text=text,
            thread_ts="thread_ts",
        )

        assert client.chat_postMessage.call_count == 2


def _expired_stream_error() -> SlackApiError:
    """The error Slack returns once it has finalized an idle streaming message."""
    return SlackApiError(
        "The request to the Slack API failed.",
        {"ok": False, "error": "message_not_in_streaming_state"},
    )


def _make_dead_stream(buffered: str) -> MagicMock:
    """A stream whose buffer holds `buffered` and whose next flush fails.

    Mirrors `AsyncChatStream.append`: the delta is added to `_buffer` before the
    API call, and the buffer is left intact when that call fails.
    """
    stream = MagicMock(spec=AsyncChatStream)
    stream._buffer = ""
    stream._state = "in_progress"

    async def append(markdown_text: str, **_):
        stream._buffer = buffered + markdown_text
        raise _expired_stream_error()

    stream.append = AsyncMock(side_effect=append)
    stream._flush_buffer = AsyncMock(side_effect=_expired_stream_error())
    return stream


def _make_live_stream() -> MagicMock:
    stream = MagicMock(spec=AsyncChatStream)
    stream._buffer = ""
    stream._state = "in_progress"
    stream.append = AsyncMock(return_value=None)
    stream._flush_buffer = AsyncMock(return_value=None)
    return stream


class TestAppendMessageToStream:
    async def test_retry_replays_the_unsent_buffer_onto_a_new_stream(
        self, make_async_web_client_mock
    ):
        dead_stream = _make_dead_stream(
            buffered="### Finding 1 — the system went read-"
        )
        new_stream = _make_live_stream()
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=new_stream)
        )

        result = await append_message_to_stream(
            client=client,
            channel_id="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            thread_ts="thread_ts",
            markdown_text=" only mode because of",
            stream=dead_stream,
        )

        assert result is new_stream
        client.chat_stream.assert_awaited_once_with(
            channel="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            thread_ts="thread_ts",
        )
        new_stream.append.assert_awaited_once_with(
            markdown_text="### Finding 1 — the system went read- only mode because of",
            chunks=None,
        )

    async def test_retry_sends_only_the_delta_when_the_stream_was_stopped(
        self, make_async_web_client_mock
    ):
        # a stopped stream rejects the append before buffering it, and stop()
        # already drained the buffer, so the delta is all that is undelivered
        dead_stream = _make_live_stream()
        dead_stream._state = "completed"
        dead_stream.append = AsyncMock(
            side_effect=SlackRequestError(
                "Cannot append to stream: stream state is completed"
            )
        )
        new_stream = _make_live_stream()
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=new_stream)
        )

        await append_message_to_stream(
            client=client,
            channel_id="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            thread_ts="thread_ts",
            markdown_text="delta",
            stream=dead_stream,
        )

        new_stream.append.assert_awaited_once_with(markdown_text="delta", chunks=None)

    async def test_does_not_retry_twice(self, make_async_web_client_mock):
        dead_stream = _make_dead_stream(buffered="head ")
        second_dead_stream = _make_dead_stream(buffered="")
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=second_dead_stream)
        )

        with pytest.raises(SlackApiError):
            await append_message_to_stream(
                client=client,
                channel_id="channel",
                recipient_user_id="U_USER",
                recipient_team_id="T_HOME",
                thread_ts="thread_ts",
                markdown_text="tail",
                stream=dead_stream,
            )

        assert client.chat_stream.await_count == 1


class TestStreamResponseToMention:
    async def test_part_end_flush_failure_switches_to_the_new_stream(
        self, make_async_web_client_mock
    ):
        dead_stream = _make_live_stream()
        dead_stream._buffer = "buffered tail\n\n"
        dead_stream._flush_buffer = AsyncMock(side_effect=_expired_stream_error())
        new_stream = _make_live_stream()
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=new_stream)
        )

        result = await stream_response_to_mention(
            client=client,
            slack_stream=dead_stream,
            stream_event=PartEndEvent(index=0, part=TextPart(content="ignored")),
            channel_id="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            ts="ts",
            thread_ts="thread_ts",
        )

        assert result is new_stream
        # "\n\n" was appended to the dead stream first, then the buffer replayed
        dead_stream.append.assert_awaited_once_with(markdown_text="\n\n", chunks=None)
        new_stream.append.assert_awaited_once_with(
            markdown_text="buffered tail\n\n", chunks=None
        )


class TestAppendChunksToStream:
    async def test_chunks_are_passed_through_to_the_stream(
        self, make_async_web_client_mock
    ):
        stream = _make_live_stream()
        client = make_async_web_client_mock()
        chunk = TaskUpdateChunk(id="c1", title="t", status="in_progress")

        result = await append_message_to_stream(
            client=client,
            channel_id="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            thread_ts="thread_ts",
            chunks=[chunk],
            stream=stream,
        )

        assert result is stream
        stream.append.assert_awaited_once_with(markdown_text=None, chunks=[chunk])

    async def test_retry_replays_buffer_and_chunks_onto_a_new_stream(
        self, make_async_web_client_mock
    ):
        dead_stream = _make_live_stream()
        dead_stream._buffer = "buffered intro"
        dead_stream.append = AsyncMock(side_effect=_expired_stream_error())
        new_stream = _make_live_stream()
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=new_stream)
        )
        chunk = TaskUpdateChunk(id="c1", title="t", status="in_progress")

        result = await append_message_to_stream(
            client=client,
            channel_id="channel",
            recipient_user_id="U_USER",
            recipient_team_id="T_HOME",
            thread_ts="thread_ts",
            chunks=[chunk],
            stream=dead_stream,
        )

        assert result is new_stream
        new_stream.append.assert_awaited_once_with(
            markdown_text="buffered intro", chunks=[chunk]
        )
