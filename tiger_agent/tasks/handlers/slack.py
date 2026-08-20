import logfire

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import usage_limit_reached, user_ignored
from tiger_agent.slack.types import SlackAppMentionEvent, SlackMessageEvent
from tiger_agent.slack.utils import (
    add_reaction,
    post_response,
    set_status,
    stream_response_to_mention,
)
from tiger_agent.tasks.handlers.base import AGENT_USAGE_LIMITS, TaskHandler
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


class SlackTaskHandler(TaskHandler):
    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        super().__init__(hctx)
        self._agent = agent

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

        async with agent_and_ctx.agent.run_stream_events(
            user_prompt=agent_and_ctx.user_prompt,
            deps=agent_and_ctx.ctx,
            usage_limits=AGENT_USAGE_LIMITS,
        ) as stream_events:
            async for stream_event in stream_events:
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

        if slack_stream is not None and slack_stream._state != "completed":
            rest = await slack_stream.stop()
            logfire.info("ended", extra={"res": rest})

        await set_status(
            client=hctx.app.client,
            channel_id=event.channel,
            thread_ts=event.thread_ts or event.ts,
            is_busy=False,
        )
        await add_reaction(hctx.app.client, event.channel, event.ts, "white_check_mark")
