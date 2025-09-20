from typing import Any, Optional

import logfire
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError

from tiger_agent import EventContext


@logfire.instrument("add_reaction", extract_args=["channel", "ts", "emoji"])
async def add_reaction(ctx: EventContext, channel: str, ts: str, emoji: str):
    try:
        await ctx.app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


@logfire.instrument("remove_reaction", extract_args=["channel", "ts", "emoji"])
async def remove_reaction(ctx: EventContext, channel: str, ts: str, emoji: str):
    try:
        await ctx.app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


class UserProfile(BaseModel):
    model_config = {"extra": "allow"}

    status_text: Optional[str] = None
    status_emoji: Optional[str] = None
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    real_name_normalized: Optional[str] = None
    display_name_normalized: Optional[str] = None
    email: Optional[str] = None
    team: Optional[str] = None


class UserInfo(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    team_id: str
    name: str
    deleted: bool
    color: Optional[str] = None
    real_name: Optional[str] = None
    tz: Optional[str] = None
    tz_label: Optional[str] = None
    tz_offset: Optional[int] = None
    profile: UserProfile

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserInfo":
        return cls(**data)


@logfire.instrument("user_info", extract_args=["user_id"])
async def user_info(ctx: EventContext, user_id: str) -> UserInfo | None:
    try:
        resp = await ctx.app.client.users_info(user=user_id, include_locale=True)
        assert isinstance(resp.data, dict)
        assert resp.data["ok"]
        return UserInfo.from_dict(resp.data["user"])
    except SlackApiError:
        return None


@logfire.instrument("post_response", extract_args=["channel", "thread_ts"])
async def post_response(
        ctx: EventContext, channel: str, thread_ts: str, text: str
) -> None:
    await ctx.app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[{"type": "markdown", "text": text}],
        unfurl_links=False,
        unfurl_media=False,
    )