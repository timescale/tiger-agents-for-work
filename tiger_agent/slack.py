from typing import Any, Optional

import logfire
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


@logfire.instrument("add_reaction", extract_args=["channel", "ts", "emoji"])
async def add_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name=emoji)
    except SlackApiError:
        pass


@logfire.instrument("remove_reaction", extract_args=["channel", "ts", "emoji"])
async def remove_reaction(client: AsyncWebClient, channel: str, ts: str, emoji: str):
    try:
        await client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
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


@logfire.instrument("fetch_user_info", extract_args=["user_id"])
async def fetch_user_info(client: AsyncWebClient, user_id: str) -> UserInfo | None:
    try:
        resp = await client.users_info(user=user_id, include_locale=True)
        assert isinstance(resp.data, dict)
        assert resp.data["ok"]
        return UserInfo(**(resp.data["user"]))
    except SlackApiError:
        return None


@logfire.instrument("post_response", extract_args=["channel", "thread_ts"])
async def post_response(
        client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> None:
    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=[{"type": "markdown", "text": text}],
        unfurl_links=False,
        unfurl_media=False,
    )


class BotInfo(BaseModel):
    model_config = {"extra": "allow"}

    url: str
    team: str
    team_id: str
    bot_id: str
    name: str
    app_id: str
    user_id: str


@logfire.instrument("fetch_bot_info", extract_args=False)
async def fetch_bot_info(client: AsyncWebClient) -> BotInfo:
    auth_test_response = await client.auth_test()
    assert auth_test_response.get("ok"), "slack auth_test failed"

    bot_id = auth_test_response.get("bot_id")

    bots_info_response = await client.bots_info(bot=bot_id)
    assert bots_info_response.get("ok"), "slack bots_info failed"

    bot = bots_info_response.get("bot")
    assert isinstance(bot, dict), "bots_info_response has unexpected payload"
    
    bot_info = BotInfo(
        url=auth_test_response.get("url"),
        team=auth_test_response.get("team"),
        team_id=auth_test_response.get("team_id"),
        bot_id=bot_id,
        name=bot.get("name"),
        app_id=bot.get("app_id"),
        user_id=bot.get("user_id")
    )
    
    return bot_info

