import asyncio
from typing import Literal

import logfire
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from slack_sdk.errors import SlackApiError

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import usage_limit_reached, user_ignored
from tiger_agent.slack.status import (
    ResponseStatus,
    StreamEventSink,
    make_subagent_event_handler,
)
from tiger_agent.slack.stream import ResponseStream
from tiger_agent.slack.task_cards import TaskCards
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent
from tiger_agent.slack.utils import (
    add_reaction,
    post_response,
    slack_target_gone,
    stream_response_to_mention,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task

RunOutcome = Literal["completed", "cancelled", "target_gone"]


async def _cancel_when_requested(cancel_requested: asyncio.Event, run_events) -> None:
    """Side task: turn a cancellation request into a pydantic-ai run cancel.

    The consumer loop is usually blocked in ``__anext__`` (e.g. waiting on a
    delegated sub-agent), so it cannot poll the Event itself. ``cancel()`` is
    safe to call from another task; the loop then sees ``RunCancelled``.
    """
    await cancel_requested.wait()
    run_events.cancel()


class SlackTaskHandler(TaskHandler):
    EVENT_TYPES = [SlackAppMentionEvent, SlackMessageEvent]

    @logfire.instrument("SlackTaskHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SlackAppMentionEvent | SlackMessageEvent = task.event

        if await user_ignored(pool=hctx.pool, user_id=event.user):
            logfire.info("Ignore user", user_id=event.user)
            return

        if await usage_limit_reached(
            pool=hctx.pool,
            user_id=event.user,
            interval=self._agent.rate_limit_interval,
            allowed_requests=self._agent.rate_limit_allowed_requests,
        ):
            logfire.info(
                "User interaction limited due to usage",
                allowed_requests=self._agent.rate_limit_allowed_requests,
                interval=self._agent.rate_limit_interval,
                user_id=event.user,
            )
            await post_response(
                client=hctx.app.client,
                channel=event.channel,
                thread_ts=event.thread_ts or event.ts,
                text="I cannot process your request at this time due to usage limits. Please ask me again later.",
            )
            return

        thread_ts = event.thread_ts or event.ts

        # events with no originating user (e.g. Slack Workflow / bot-posted
        # mentions) have no recipient_user_id to stream to, so
        # chat.startStream fails with "missing_recipient_user_id". Fall back
        # to accumulating the response and posting it once via post_response.
        can_stream = event.user is not None
        response_stream = (
            ResponseStream(
                client=hctx.app.client,
                channel_id=event.channel,
                recipient_user_id=event.user,
                recipient_team_id=event.user_team or hctx.bot_info.team_id,
                thread_ts=thread_ts,
            )
            if can_stream
            else None
        )

        # Everything the agent tree does flows through these sinks:
        # - the thread's assistant status (coordinator and sub-agent tool
        #   calls, refreshed before Slack's two-minute expiry)
        # - one task card per delegated sub-agent inside the streamed reply,
        #   whose updates also keep that stream alive through a long delegation
        status = ResponseStatus(
            client=hctx.app.client, channel_id=event.channel, thread_ts=thread_ts
        )
        sinks: list[StreamEventSink] = [status]
        if response_stream is not None:
            sinks.append(TaskCards(response_stream))

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=event.channel,
            subagent_event_handler=make_subagent_event_handler(*sinks),
        )

        await status.set(None)
        response_text_parts: list[str] = []
        outcome: RunOutcome = "completed"

        # Registered for the whole run so a `message_deleted` event for this
        # message can stop it (see SlackListener._on_message_deleted).
        with hctx.cancellations.track(event.channel, event.ts) as cancel_requested:
            async with agent_and_ctx.agent.run_stream_events(
                user_prompt=agent_and_ctx.user_prompt,
                deps=agent_and_ctx.ctx,
                usage_limits=AGENT_USAGE_LIMITS,
            ) as stream_events:
                watcher = asyncio.create_task(
                    _cancel_when_requested(cancel_requested, stream_events)
                )
                try:
                    async for stream_event in stream_events:
                        for sink in sinks:
                            await sink.on_event(stream_event)
                        if response_stream is not None:
                            # sub-agent task cards write to the same stream from
                            # the event handler, so take the stream's lock
                            # around text too
                            async with response_stream.lock:
                                response_stream.stream = (
                                    await stream_response_to_mention(
                                        client=hctx.app.client,
                                        slack_stream=response_stream.stream,
                                        stream_event=stream_event,
                                        channel_id=event.channel,
                                        recipient_user_id=event.user,
                                        recipient_team_id=event.user_team
                                        or hctx.bot_info.team_id,
                                        ts=event.ts,
                                        thread_ts=event.thread_ts,
                                    )
                                )
                        elif isinstance(stream_event, PartStartEvent) and isinstance(
                            stream_event.part, TextPart
                        ):
                            response_text_parts.append(stream_event.part.content or "")
                        elif isinstance(stream_event, PartDeltaEvent) and isinstance(
                            stream_event.delta, TextPartDelta
                        ):
                            response_text_parts.append(
                                stream_event.delta.content_delta or ""
                            )
                except RunCancelled:
                    outcome = "cancelled"
                except SlackApiError as error:
                    if not slack_target_gone(error):
                        raise
                    # The message (or channel) we are replying to is gone. No
                    # retry can ever succeed, so stop the run and ack the task.
                    outcome = "target_gone"
                    logfire.warn(
                        "Stopping run: the Slack message being answered is gone",
                        channel=event.channel,
                        ts=event.ts,
                        slack_error=error.response.get("error")
                        if error.response is not None
                        else None,
                    )
                    stream_events.cancel()
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)

        if outcome != "completed":
            if outcome == "cancelled":
                logfire.info(
                    "Run cancelled: the triggering Slack message was deleted",
                    channel=event.channel,
                    ts=event.ts,
                )
            if response_stream is not None:
                await response_stream.discard()
            await status.clear()
            # a normal return acks the task; there is nothing left to answer
            return

        if response_stream is not None:
            await response_stream.stop()
        else:
            response_text = "".join(response_text_parts).strip()
            if response_text:
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=thread_ts,
                    text=response_text,
                )

        await status.clear()
        await add_reaction(hctx.app.client, event.channel, event.ts, "white_check_mark")
