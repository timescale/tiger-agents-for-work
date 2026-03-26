from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from tiger_agent.slack.types import BotInfo, SlackMessageEvent


def build_message_history_from_slack_messages(
    thread_messages: list[SlackMessageEvent],
    bot_info: BotInfo,
    filter_message_ts: list[str] | None = None,
) -> list[ModelMessage]:
    """Convert thread messages to Pydantic-AI message history format.

    Transforms Slack thread messages into the ModelMessage format expected
    by Pydantic-AI for conversation history. User messages become ModelRequest
    with UserPromptPart, and bot messages become ModelResponse with TextPart.

    Args:
        thread_messages: List of messages from Slack thread
        bot_info: The agent's slack bot info, used to determine which messages are agentic responses
        filter_message_ts: Optional list of messages to filter out

    Returns:
        List of ModelMessage objects suitable for agent.run(message_history=...)
    """
    message_history: list[ModelMessage] = []

    for msg in thread_messages:
        if filter_message_ts and msg.ts in filter_message_ts:
            # dont process ignored messages
            continue
        if msg.user == bot_info.user_id:
            # Bot messages become assistant responses
            message_history.append(ModelResponse(parts=[TextPart(content=msg.text)]))
        else:
            # all other messages are requests
            message_history.append(
                ModelRequest(parts=[UserPromptPart(content=msg.text)])
            )

    return message_history
