import re
from asyncio import TaskGroup
from collections.abc import Sequence
from typing import Any

import logfire
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond

from tiger_agent.db.utils import get_event_hist, insert_event, insert_handled_event
from tiger_agent.events.types import EventProcessor, HarnessContext
from tiger_agent.events.utils import process_event
from tiger_agent.salesforce.types import AgentFeedbackRatingEvent
from tiger_agent.slack.commands import handle_command
from tiger_agent.slack.constants import (
    AGENT_FEEDBACK_RATING,
    CONFIRM_PROACTIVE_PROMPT,
    REJECT_PROACTIVE_PROMPT,
)
from tiger_agent.slack.types import BotInfo, SlackCommand
from tiger_agent.slack.utils import (
    fetch_bot_info,
    handle_proactive_prompt,
    send_proactive_prompt,
    set_status,
)


class SlackEventHandler:
    """Wrapper around Slack utility functions scoped to a HarnessContext.

    Provides convenience methods that delegate to the module-level functions,
    automatically sourcing client, slack_bot_token, and bot_info from the
    provided HarnessContext so callers don't need to pass them explicitly.
    Bot info is fetched lazily and cached after the first call.

    Args:
        hctx: HarnessContext providing the Slack app, token, and database pool
    """

    def __init__(
        self,
        hctx: HarnessContext,
        event_processor: EventProcessor,
        proactive_prompt_channels: set[str] | None = None,
    ):
        self._hctx = hctx
        self._pool = hctx.pool
        self._app = hctx.app
        self._trigger = hctx.trigger
        self._event_processor = event_processor
        self._proactive_prompt_channels = proactive_prompt_channels
        self._bot_info: BotInfo | None = None

    async def start(self, tasks: TaskGroup):
        self._bot_info = await fetch_bot_info(self._app.client)
        self._app.action(CONFIRM_PROACTIVE_PROMPT)(self._handle_proactive_prompt)
        self._app.action(REJECT_PROACTIVE_PROMPT)(self._handle_proactive_prompt)
        self._app.action(AGENT_FEEDBACK_RATING)(self._handle_agent_feedback_rating)
        self._app.event("message")(self._on_message)

        self._app.command(re.compile(r"\/.*"))(self._on_slack_command)
        self._app.event("app_mention")(self._on_slack_event)

        handler = AsyncSocketModeHandler(
            self._app, app_token=self._hctx.slack_app_token
        )
        tasks.create_task(handler.start_async())

    async def _on_slack_event(self, ack: AsyncAck, event: dict[str, Any]):
        await set_status(
            self._app.client,
            channel_id=event.get("channel"),
            thread_ts=event.get("thread_ts") or event.get("ts"),
            is_busy=True,
        )
        await insert_event(self._pool, event)
        await ack()
        await self._trigger.put(True)

    async def _on_slack_command(
        self, ack: AsyncAck, respond: AsyncRespond, command: dict[str, Any]
    ):
        slack_command = SlackCommand(**command)
        await ack()
        response = await handle_command(command=slack_command, hctx=self._hctx)
        await respond(text=response, response_type="ephemeral", delete_original=True)

    async def _on_message(self, ack: AsyncAck, event: dict[str, Any]):
        await ack()

        # agent should ignore its own messages
        user = event.get("user")
        if user == self._bot_info.user_id or user is None:
            return

        event["subtype"] = event["channel_type"]
        channel = event.get("channel")

        # if the message was in an im to the agent, respond (even though agent was not mentioned)
        if event["subtype"] in ("im"):
            await self._on_slack_event(ack, event)
            return

        elif (
            not self._proactive_prompt_channels
            or channel not in self._proactive_prompt_channels
            or re.search(
                rf"<@{re.escape(self._bot_info.user_id)}>", event.get("text", "")
            )
        ):
            return

        # if the channel is one that the agent should proactively respond to and the agent was not @mentioned
        user = event.get("user")
        thread_ts = event.get("thread_ts")

        # only offer proactive prompts on top level messages
        if thread_ts is not None:
            return

        event_hist_id = await insert_handled_event(pool=self._pool, event=event)

        await send_proactive_prompt(
            client=self._hctx.app.client,
            channel=channel,
            user=user,
            event_hist_id=event_hist_id,
        )

    async def _handle_proactive_prompt(
        self, ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond
    ):
        # todo: would be nice to not have this wrapper closure, but limiting refactor effort for now
        event_hist_id = await handle_proactive_prompt(
            ack=ack, body=body, respond=respond, bot_info=self._bot_info
        )

        if not event_hist_id:
            return

        event = await get_event_hist(self._pool, event_hist_id)

        if event is None:
            logfire.error(
                "Could not find event_hist record", event_hist_id=event_hist_id
            )
            return

        await process_event(self._event_processor, self._hctx, event)

    async def _handle_agent_feedback_rating(
        self, ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond
    ):
        await ack()

        actions = body.get("actions")
        if actions is None or not isinstance(actions, Sequence) or len(actions) != 1:
            logfire.error("Actions was not an expected payload", event=body)
            return

        selected_option = actions[0].get("selected_option")
        if selected_option is None:
            logfire.error("No selected option in feedback rating action", event=body)
            return

        value = selected_option.get("value")
        if value is None:
            logfire.error("No value in selected option", event=body)
            return

        parts = value.split("|")
        if len(parts) != 4:
            logfire.error("Unexpected value format in feedback rating", value=value)
            return

        agent_message_ts, channel, user, rating = parts

        await respond(
            response_type="ephemeral",
            text="",
            replace_original=True,
            delete_original=True,
        )

        logfire.info(
            "Agent feedback rating received",
            agent_message_ts=agent_message_ts,
            channel=channel,
            user=user,
            rating=rating,
        )

        await insert_handled_event(
            pool=self._hctx.pool,
            event=AgentFeedbackRatingEvent(
                message_ts=agent_message_ts,
                channel=channel,
                rating=int(rating),
            ).model_dump(),
        )
