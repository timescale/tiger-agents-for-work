from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from tiger_agent.events.types import Event
from tiger_agent.mcp.types import MCPDict
from tiger_agent.salesforce.types import SalesforceNewCaseEvent
from tiger_agent.slack.types import (
    BotInfo,
    SlackAppMentionEvent,
    SlackMessageEvent,
    UserInfo,
)


class AgentResponseContext(BaseModel):
    """Context object for AI agent responses containing event data and user information.

    This model serves as the context passed to Jinja2 templates for generating
    system and user prompts. It contains all necessary information about the
    Slack event, user details, and computed values like localized timestamps.

    Attributes:
        event: The database event record containing metadata and Slack event data
        mention: The specific app mention or message event that triggered processing
        bot: Information about the bot user (display name, user ID, etc.)
        user: Slack user information including timezone, or None if unavailable
        local_time: Event timestamp converted to user's local timezone, set automatically
        mcp_servers: Dictionary of mcp servers that the Agent has as its disposal
    """

    event: Event
    mention: SlackAppMentionEvent | SlackMessageEvent | SalesforceNewCaseEvent
    bot: BotInfo
    user: UserInfo | None = None
    local_time: datetime | None = None
    mcp_servers: MCPDict | None = None
    slack_bot_token: str

    def model_post_init(self, __context):
        """Automatically compute derived fields after model initialization.

        Sets the local_time field by converting the event timestamp to the
        user's timezone if user information is available. This ensures templates
        always have access to properly localized time information.
        """
        if self.user is not None and self.user.tz is not None:
            self.local_time = self.event.event_ts.astimezone(ZoneInfo(self.user.tz))


type ExtraContextDict = dict[str, BaseModel]
