from unittest.mock import AsyncMock, MagicMock

from slack_sdk.models.messages.chunk import TaskUpdateChunk
from slack_sdk.web.async_client import AsyncChatStream

from tiger_agent.slack.stream import ResponseStream


def _live_stream() -> MagicMock:
    stream = MagicMock(spec=AsyncChatStream)
    stream._buffer = ""
    stream._state = "in_progress"
    stream.append = AsyncMock(return_value=None)
    stream.stop = AsyncMock(return_value={"ok": True})
    return stream


def _response_stream(client) -> ResponseStream:
    return ResponseStream(
        client=client,
        channel_id="C_CHAN",
        recipient_user_id="U_USER",
        recipient_team_id="T_HOME",
        thread_ts="1.0",
    )


class TestResponseStream:
    async def test_first_append_opens_the_stream_and_keeps_it(
        self, make_async_web_client_mock
    ):
        slack_stream = _live_stream()
        client = make_async_web_client_mock(
            chat_stream=AsyncMock(return_value=slack_stream)
        )
        rs = _response_stream(client)
        chunk = TaskUpdateChunk(id="c1", title="t", status="in_progress")

        await rs.append(chunks=[chunk])
        await rs.append(markdown_text="hello")

        assert rs.stream is slack_stream
        assert rs.is_open
        client.chat_stream.assert_awaited_once()
        assert slack_stream.append.await_args_list[0].kwargs["chunks"] == [chunk]
        assert slack_stream.append.await_args_list[1].kwargs["markdown_text"] == "hello"

    async def test_stop_finalizes_only_an_open_stream(self, make_async_web_client_mock):
        client = make_async_web_client_mock()
        rs = _response_stream(client)

        await rs.stop()  # nothing opened yet: no-op

        slack_stream = _live_stream()
        rs.stream = slack_stream
        await rs.stop()
        slack_stream.stop.assert_awaited_once()

        slack_stream._state = "completed"
        await rs.stop()
        slack_stream.stop.assert_awaited_once()
