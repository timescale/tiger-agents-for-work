from .agent import TigerAgent
import asyncio

async def main() -> None:
    agent = TigerAgent(
        "anthropic:claude-sonnet-4-20250514",
        "You are a helpful Slack-native agent who answers questions posed to you in Slack messages.",
        "Please answer the following question: {event.event['text']}",
    )
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
