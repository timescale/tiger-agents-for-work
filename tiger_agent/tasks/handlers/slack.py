import logfire
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import usage_limit_reached, user_ignored
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent
from tiger_agent.slack.utils import (
    add_reaction,
    post_response,
    set_status,
    stream_response_to_mention,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


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

        agent_and_ctx = await create_agent_and_context(
            hctx=hctx,
            task=task,
            agent=self._agent,
            channel_to_respond=event.channel,
        )

        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=True,
        )
        slack_stream = None

        # events with no originating user (e.g. Slack Workflow / bot-posted
        # mentions) have no recipient_user_id to stream to, so
        # chat.startStream fails with "missing_recipient_user_id". Fall back
        # to accumulating the response and posting it once via post_response.
        can_stream = event.user is not None
        response_text_parts: list[str] = []

        async with agent_and_ctx.agent.run_stream_events(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=AGENT_USAGE_LIMITS,
        ) as stream_events:
            async for stream_event in stream_events:
                if can_stream:
                    slack_stream = await stream_response_to_mention(
                        client=hctx.app.client,
                        slack_stream=slack_stream,
                        stream_event=stream_event,
                        channel_id=event.channel,
                        recipient_user_id=event.user,
                        recipient_team_id=event.user_team or hctx.bot_info.team_id,
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
                    response_text_parts.append(stream_event.delta.content_delta or "")

        if can_stream:
            if slack_stream is not None and slack_stream._state != "completed":
                rest = await slack_stream.stop()
                logfire.info("ended", extra={"res": rest})
        else:
            response_text = "".join(response_text_parts).strip()
            if response_text:
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=event.thread_ts or event.ts,
                    text=response_text,
                )

        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=False,
        )
        await add_reaction(hctx.app.client, event.channel, event.ts, "white_check_mark")
