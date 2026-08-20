import asyncio

import logfire

from tiger_agent.slack.types import AgentFeedbackRequestReminderEvent
from tiger_agent.slack.utils import post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class AgentFeedbackRequestReminderHandler(TaskHandler):
    """
    When the agent supplies feedback, we have a mechanism that will remind the recipient
    to leave feedback. At this time, the only scenario that this will happen is when the agent
    gives a suggested response for a new Salesforce case. When the new case event occurs,
    we enqueue a AgentFeedbackRequestReminderEvent with a future vt -- this effectively
    schedules the reminder for the future (e.g. at the end of the support engineer's day)
    """

    @logfire.instrument(
        "AgentFeedbackRequestReminderHandler.handle", extract_args=False
    )
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: AgentFeedbackRequestReminderEvent = task.event

        permalink_results = await asyncio.gather(
            *[
                hctx.app.client.chat_getPermalink(
                    channel=t.channel, message_ts=t.message_ts
                )
                for t in event.threads
            ]
        )
        thread_links = "\n".join(
            f"• <{result.data.get('permalink')}|{t.label}>"
            for t, result in zip(event.threads, permalink_results, strict=True)
        )
        await post_response(
            client=hctx.app.client,
            channel=event.user,
            thread_ts=None,
            text=f"Hey! Thanks for all your support today. When you get a chance, we'd love to hear your thoughts on these conversations:\n{thread_links}",
        )
