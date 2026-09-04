import json

import logfire
from pydantic_ai import Agent, Tool

from tiger_agent.agent.limits import AGENT_USAGE_LIMITS
from tiger_agent.salesforce.types import UserDefinedRuleMatch
from tiger_agent.salesforce.utils import create_case_url
from tiger_agent.slack.utils import post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class UserDefinedRuleMatchHandler(TaskHandler):
    EVENT_TYPES = [UserDefinedRuleMatch]

    @logfire.instrument("UserDefinedRuleMatchHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: UserDefinedRuleMatch = task.event

        async def _send_dm(user_id: str, message: str) -> None:
            await post_response(
                client=hctx.app.client,
                channel=user_id,
                thread_ts=None,
                text=message,
            )

        async def _send_channel_message(channel_id: str, message: str) -> None:
            await post_response(
                client=hctx.app.client,
                channel=channel_id,
                thread_ts=None,
                text=message,
            )

        def _get_case_url(case_id: str) -> str:
            """Return the Salesforce URL for a case. The case_id can often be obtained
            from a Salesforce object's ParentId field (e.g. on FeedItem, Task, etc.)."""
            return create_case_url(case_id)

        agent = Agent(
            model="anthropic:claude-opus-4-7",
            system_prompt=(
                "You are an automated action agent. A custom monitoring rule has matched an incoming "
                "event and you must carry out the action described in the user prompt. "
                "Use the send_dm tool to notify users or send_channel_message to post to a channel. "
                "Act immediately — do not ask clarifying questions "
                "and do not add conversational framing."
            ),
            tools=[
                Tool(_send_dm, takes_ctx=False, name="send_dm"),
                Tool(
                    _send_channel_message, takes_ctx=False, name="send_channel_message"
                ),
                Tool(_get_case_url, takes_ctx=False, name="get_case_url"),
            ],
        )

        user_prompt = (
            f"{event.action_prompt}\n\n"
            f"## Event Payload\n\n"
            f"```\n{json.dumps(event.matched_event, indent=2, default=str)}\n```"
        )

        await agent.run(
            user_prompt=user_prompt,
            usage_limits=AGENT_USAGE_LIMITS,
        )
