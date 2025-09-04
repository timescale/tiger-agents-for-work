import logfire
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.agents.types import Mention


async def react_to_mention(
    client: AsyncWebClient, mention: Mention, emoji: str
) -> None:
    with logfire.span(
        "reacting to mention", channel=mention.channel, ts=mention.ts, emoji=emoji
    ):
        try:
            await client.reactions_add(
                channel=mention.channel, timestamp=mention.ts, name=emoji
            )
        except Exception as e:
            logfire.error(
                "error while reacting to mention",
                _exc_info=e,
                channel=mention.channel,
                ts=mention.ts,
                emoji=emoji,
            )


async def remove_reaction_from_mention(
    client: AsyncWebClient, mention: Mention, emoji: str
) -> None:
    with logfire.span(
        "removing reaction from mention",
        channel=mention.channel,
        ts=mention.ts,
        emoji=emoji,
    ):
        try:
            await client.reactions_remove(
                channel=mention.channel, timestamp=mention.ts, name=emoji
            )
        except Exception as e:
            logfire.error(
                "error while removing reaction from mention",
                _exc_info=e,
                channel=mention.channel,
                ts=mention.ts,
                emoji=emoji,
            )


async def post_response(
    client: AsyncWebClient, channel: str, thread_ts: str, text: str
) -> None:
    await client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, markdown_text=text
    )