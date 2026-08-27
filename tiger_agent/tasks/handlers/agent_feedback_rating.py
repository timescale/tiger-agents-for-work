import logfire

from tiger_agent.slack.constants import AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL
from tiger_agent.slack.types import (
    AgentFeedbackRatingEvent,
    AgentFeedbackRatingSubtype,
)
from tiger_agent.slack.utils import get_channel_link, get_handle_link, post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class AgentFeedbackRatingHandler(TaskHandler):
    """
    Called when a Salesforce case status changes.
    """

    EVENT_TYPES = [AgentFeedbackRatingEvent]

    @logfire.instrument("AgentFeedbackRatingHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: AgentFeedbackRatingEvent = task.event

        if not AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL:
            logfire.info(
                "AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL not specified, not posting results."
            )
            return

        if event.subtype == AgentFeedbackRatingSubtype.external:
            await post_response(
                client=hctx.app.client,
                channel=AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL,
                thread_ts=None,
                use_mrkdwn=True,
                text="\n".join(
                    [
                        "*Feedback Received*",
                        *([f"_Source:_ `{event.subtype}`"] if event.subtype else []),
                        *(
                            [f"_Rating:_ `{event.rating}/5`"]
                            if event.rating is not None
                            else []
                        ),
                        *(
                            [f"_User:_ {get_handle_link(event.user)}"]
                            if event.user
                            else []
                        ),
                        *(
                            [f"_Channel:_ {get_channel_link(event.channel)}"]
                            if event.channel
                            else []
                        ),
                        *(
                            [
                                f"_Description:_ \n{'\n'.join(f'> {line}' for line in event.description.splitlines())}"
                            ]
                            if event.description
                            else []
                        ),
                    ]
                ),
            )
