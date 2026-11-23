import asyncio
from typing import Literal

import logfire
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from slack_sdk.errors import SlackApiError

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import (
    delete_session_cancel,
    is_session_cancelled,
    usage_limit_reached,
    user_ignored,
)
from tiger_agent.slack.events import (
    StreamEventSink,
    make_subagent_event_handler,
)
from tiger_agent.slack.stream import ResponseStream
from tiger_agent.slack.task_cards import TaskCards
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent
from tiger_agent.slack.utils import (
    add_reaction,
    post_response,
    set_status,
    slack_target_gone,
    stream_response_to_mention,
)
from tiger_agent.tasks.cancellation import TrackedRun
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task

RunOutcome = Literal["completed", "cancelled", "target_gone"]


async def _reset_session_status(hctx, channel_id: str, thread_ts: str) -> None:
    """Take the agent session back to "active" now the run is over.

    The session is left showing "processing" otherwise, whether the run
    finished, was cancelled, or gave up because the message is gone.
    """
    await set_status(
        client=hctx.app.client,
        channel_id=channel_id,
        thread_ts=thread_ts,
        is_busy=False,
    )


# How often a running worker checks the database for a cross-replica stop.
_POLL_INTERVAL_SECONDS: float = 5.0


async def _cancel_when_requested(cancel_requested: asyncio.Event, run_events) -> None:
    """Side task: turn a cancellation request into a pydantic-ai run cancel.

    The consumer loop is usually blocked in ``__anext__`` (e.g. waiting on a
    delegated sub-agent), so it cannot poll the Event itself. ``cancel()`` is
    safe to call from another task; the loop then sees ``RunCancelled``.
    """
    await cancel_requested.wait()
    run_events.cancel()


async def _poll_session_cancel(
    pool,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    event_ts,
    tracked_run: TrackedRun,
) -> None:
    """Side task: poll the database for a cross-replica stop click.

    Checks periodically whether another replica recorded a
    ``agent_session_stopped`` for this session, and if so tells the tracked
    run to cancel itself.
    """
    while not tracked_run.cancel_requested.is_set():
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            if await is_session_cancelled(
                pool=pool,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                event_ts=event_ts,
            ):
                tracked_run.cancel("user_request")
                await delete_session_cancel(
                    pool=pool,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    user_id=user_id,
                )
                return
        except Exception:
            logfire.exception("Failed to poll session cancellation")


class SlackTaskHandler(TaskHandler):
    EVENT_TYPES = [SlackAppMentionEvent, SlackMessageEvent]

    @logfire.instrument("SlackTaskHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        event: SlackAppMentionEvent | SlackMessageEvent = task.event
        thread_ts = event.thread_ts or event.ts

        # Track how many requests are active in this session so we only
        # transition back to "active" when the last one leaves. This also
        # sets the busy indicator for retried or proactively accepted tasks
        # that do not pass through the listener's enqueue-time call.
        session_count = self._hctx.cancellations.enter_session(event.channel, thread_ts)
        if session_count == 1:
            try:
                await set_status(
                    client=self._hctx.app.client,
                    channel_id=event.channel,
                    thread_ts=thread_ts,
                    is_busy=True,
                )
            except Exception:
                logfire.exception("Failed to set session busy at worker start")

        # Register before any setup work so a cancellation that lands while we
        # are still building the agent (MCP servers, thread history, ...) is
        # not missed: the run can be stopped from outside by a `message_deleted`
        # event for this message (SlackListener) or by the same user saying
        # "nevermind" in the thread (the cancel_my_requests tool in a later run).
        with self._hctx.cancellations.track(
            event.channel, event.ts, user=event.user, thread_ts=event.thread_ts
        ) as tracked_run:
            try:
                await self._answer(task, tracked_run)
            finally:
                remaining = self._hctx.cancellations.exit_session(
                    event.channel, thread_ts
                )
                if remaining == 0:
                    await _reset_session_status(self._hctx, event.channel, thread_ts)

    async def _answer(self, task: Task, tracked_run: TrackedRun) -> None:
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

        # Everything the agent tree does flows through these sinks: one task
        # card per agent inside the streamed reply (the coordinator's own, plus
        # one per delegated sub-agent) listing the tools it calls. Those
        # updates also keep the stream alive through a long delegation.
        sinks: list[StreamEventSink] = []
        task_cards: TaskCards | None = None
        if response_stream is not None:
            task_cards = TaskCards(response_stream)
            sinks.append(task_cards)

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            subagent_event_handler=make_subagent_event_handler(*sinks),
        )

        if tracked_run.cancel_requested.is_set():
            # deleted (or withdrawn) while we were setting up: nothing has been
            # posted yet, so there is nothing to clean up either
            logfire.info(
                "Run cancelled before it started",
                channel=event.channel,
                ts=event.ts,
                reason=tracked_run.reason,
            )
            return

        response_text_parts: list[str] = []
        outcome: RunOutcome = "completed"

        async with agent_and_ctx.agent.run_stream_events(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=AGENT_USAGE_LIMITS,
        ) as stream_events:
            watcher = asyncio.create_task(
                _cancel_when_requested(tracked_run.cancel_requested, stream_events)
            )
            # Poll the database so a stop click delivered to another replica
            # can still cancel this run.
            poller = None
            if event.user is not None:
                poller = asyncio.create_task(
                    _poll_session_cancel(
                        pool=hctx.pool,
                        channel_id=event.channel,
                        thread_ts=thread_ts,
                        user_id=event.user,
                        event_ts=task.event_ts,
                        tracked_run=tracked_run,
                    )
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
                            response_stream.stream = await stream_response_to_mention(
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
                if poller is not None:
                    poller.cancel()
                await asyncio.gather(watcher, poller, return_exceptions=True)

        if outcome != "completed":
            if outcome == "cancelled":
                logfire.info(
                    "Run cancelled: the triggering Slack message was deleted"
                    if tracked_run.reason == "message_deleted"
                    else "Run cancelled at the user's request",
                    channel=event.channel,
                    ts=event.ts,
                    reason=tracked_run.reason,
                )
            # When the stream is about to be discarded, skip card appends:
            # append_message_to_stream retries by opening a fresh stream,
            # which orphans the original message.
            if task_cards is not None and outcome == "completed":
                await task_cards.close(outcome)
            if response_stream is not None:
                await response_stream.discard()
            # a normal return acks the task; there is nothing left to answer
            return

        if task_cards is not None:
            await task_cards.close()
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

        await add_reaction(hctx.app.client, event.channel, event.ts, "white_check_mark")
