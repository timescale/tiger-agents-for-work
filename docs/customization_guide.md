# Customization Guide

Using the [CLI](/docs/cli.md) is a great way to get started quickly and allows for light customization.
Sometimes you want to do more customization than is possible with the CLI.
To further customize, you'll need to write some Python.

## Setup

Create a Python project:

```bash
# create a directory for the project
mkdir my-agent
cd my-agent

# initialize the project
uv init
```

Add the Tiger Agent library as a dependency:

```bash
# add the Tiger Agent as a dependency
uv add git+https://github.com/timescale/tiger-agent.git
```

To get a specific version of the library using git tags:

```bash
uv add git+https://github.com/timescale/tiger-agent.git@v0.0.1
```

## Subclassing TigerAgent

The [TigerAgent class](/docs/tiger_agent.md) interacts with Slack `app_mention` events.
You can subclass TigerAgent and override the `generate_response(...)` method to customize exactly how responses are generated.

```python
class MyAgent(TigerAgent):
    def __init__(
            self,
            model: models.Model | models.KnownModelName | str | None = None,
            jinja_env: Environment | Path = Path.cwd(),
            mcp_config_path: Path | None = None,
            max_attempts: int = 3,
    ):
        super().__init__(
            model,
            jinja_env,
            mcp_config_path,
            max_attempts
        )

    async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
        client = hctx.app.client
        mention = event.event
        # get the bot info if we haven't already
        if not self.bot_info:
            self.bot_info = await fetch_bot_info(client)
        # get the user info
        user_info = await fetch_user_info(client, mention.user)
        # init context
        ctx: dict[str, Any] = dict(event=event, mention=mention, bot=self.bot_info, user=user_info)
        
        # ADD CODE TO CUSTOMIZE THE CONTEXT
        
        # render system prompt
        system_prompts: str = await self.make_system_prompt(ctx)
        # render the user prompt
        user_prompt = await self.make_user_prompt(ctx)

        # load the mcp servers if you wish
        mcp_servers = self.mcp_loader()
        toolsets = [mcp for mcp in mcp_servers.values()]

        # CUSTOMIZE AGENT CREATION IF YOU WISH (e.g. add more tools)
        agent = Agent(
            model=self.model,
            deps_type=dict[str, Any],
            system_prompt=system_prompts,
            toolsets=toolsets
        )
        
        # CUSTOMIZE RUNNING THE AGENT IF YOU WISH
        async with agent as a:
            response = await a.run(
                user_prompt=user_prompt,
                deps=ctx,
                usage_limits=UsageLimits(
                    output_tokens_limit=9_000
                )
            )
            return response.output
```

## Implementing EventProcessor

For even more customizability, you can implement an EventProcessor to control every aspect of the interaction.
This can be a simple function which is passed to the [EventHarness](/docs/event_harness.md).
See examples in [/examples/echo.py](/examples/echo.py) and [/examples/random_fail.py](/examples/random_fail.py).

```python
# our slackbot will just echo messages back
async def echo(ctx: HarnessContext, event: Event):
    channel = event.event["channel"]
    ts = event.event["ts"]
    text = event.event["text"]
    await ctx.app.client.chat_postMessage(
        channel=channel, thread_ts=ts, text=f"echo: {text}"
    )


async def main() -> None:
    # create the agent harness
    harness = EventHarness(echo)
    # run the harness
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())
```

Alternately, you can create a class that implements EventProcessor. This is handy if you need state.

```python
class MyEventProcessor:
    def __init__(self):
        pass

    # the __call__ method implements EventProcessor
    async def __call__(self, ctx: HarnessContext, event: Event):
        # echo back the message
        channel = event.event["channel"]
        ts = event.event["ts"]
        text = event.event["text"]
        await ctx.app.client.chat_postMessage(
            channel=channel, thread_ts=ts, text=f"echo: {text}"
        )

async def main() -> None:
    # create an instance of our custom event processor
    event_processor = MyEventProcessor()
    # create the agent harness
    harness = EventHarness(event_processor)
    # run the harness
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())

```