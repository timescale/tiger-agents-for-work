"""A single Slack streaming reply shared by everything that writes to it.

The coordinator's text deltas and the delegated sub-agents' task cards land
in the same ``chat.startStream`` message. Sub-agent events arrive while the
coordinator loop is awaiting its next event, so the two writers can interleave;
``ResponseStream`` serialises them and keeps the one live ``AsyncChatStream``
(which may be replaced when Slack expires an idle stream and
``append_message_to_stream`` opens a new one).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence

import logfire
from slack_sdk.models.messages.chunk import Chunk
from slack_sdk.web.async_client import AsyncChatStream, AsyncWebClient

from tiger_agent.slack.utils import append_message_to_stream


class ResponseStream:
    def __init__(
        self,
        client: AsyncWebClient,
        channel_id: str,
        recipient_user_id: str,
        recipient_team_id: str,
        thread_ts: str,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._recipient_user_id = recipient_user_id
        self._recipient_team_id = recipient_team_id
        self._thread_ts = thread_ts
        self.stream: AsyncChatStream | None = None
        self.lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self.stream is not None and self.stream._state != "completed"

    async def append(
        self,
        markdown_text: str | None = None,
        chunks: Sequence[Chunk] | None = None,
    ) -> None:
        """Append text and/or chunks, opening the stream on first use."""
        async with self.lock:
            self.stream = await append_message_to_stream(
                client=self._client,
                channel_id=self._channel_id,
                recipient_user_id=self._recipient_user_id,
                recipient_team_id=self._recipient_team_id,
                thread_ts=self._thread_ts,
                markdown_text=markdown_text,
                chunks=chunks,
                stream=self.stream,
            )

    async def stop(self) -> None:
        """Finalize the message if a stream is still open."""
        async with self.lock:
            if self.is_open:
                assert self.stream is not None
                rest = await self.stream.stop()
                logfire.info("ended", extra={"res": rest})

    async def discard(self) -> None:
        """Remove whatever was streamed so far; used when the question was deleted.

        Best effort: the stream may already be dead (Slack finalized it, or the
        thread is gone), so every step swallows Slack errors.
        """
        async with self.lock:
            stream = self.stream
            self.stream = None
        if stream is None:
            return
        message_ts = getattr(stream, "_stream_ts", None)
        if stream._state != "completed":
            with contextlib.suppress(Exception):
                await stream.stop()
        if message_ts:
            with contextlib.suppress(Exception):
                await self._client.chat_delete(channel=self._channel_id, ts=message_ts)
                logfire.info("Discarded partial reply", ts=message_ts)
