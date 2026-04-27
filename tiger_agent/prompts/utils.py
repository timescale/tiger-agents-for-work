from datetime import UTC, datetime

from tiger_agent.slack.types import BotInfo, SlackMessageEvent


def format_thread_history(
    thread_messages: list[SlackMessageEvent],
    bot_info: BotInfo,
    filter_message_ts: list[str] | None = None,
) -> str:
    """Format Slack thread messages as a readable conversation transcript.

    Args:
        thread_messages: List of messages from Slack thread
        bot_info: The agent's slack bot info, used to label bot messages
        filter_message_ts: Optional list of message timestamps to exclude

    Returns:
        Formatted string transcript, or empty string if no messages.
    """
    lines: list[str] = []

    for msg in thread_messages:
        sender_id = msg.user or msg.bot_id
        if filter_message_ts and msg.ts in filter_message_ts:
            continue
        actor = (
            f"<@{bot_info.user_id}> (you)"
            if sender_id == bot_info.user_id
            else f"<@{sender_id}>"
        )
        ts = datetime.fromtimestamp(float(msg.ts), tz=UTC).isoformat()
        lines.append(f"[{ts}] {actor}: {msg.text}")

    return "\n".join(lines)
