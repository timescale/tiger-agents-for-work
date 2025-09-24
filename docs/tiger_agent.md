# TigerAgent - AI-Powered Slack Bot

The TigerAgent is an intelligent Slack bot that processes app_mention events using advanced AI capabilities. It serves as the primary EventProcessor for the EventHarness system, combining Pydantic-AI, MCP server integration, dynamic prompt templating, and rich Slack interactions to create a sophisticated conversational AI experience.

## Overview

TigerAgent transforms simple Slack mentions into powerful AI interactions by:
- Processing natural language requests through Large Language Models
- Extending AI capabilities with external tools via MCP (Model Context Protocol) servers
- Providing context-aware responses using dynamic Jinja2 templating
- Delivering rich user experiences with visual feedback through Slack reactions

## Key Features

### 🤖 **AI-Powered Responses**
Uses Pydantic-AI to generate intelligent responses to Slack mentions, with support for multiple LLM providers and structured output handling.

### 🔧 **MCP Server Integration**
Extends AI capabilities by connecting to multiple MCP servers, providing access to external APIs, databases, documentation systems, and specialized tools.

### 📝 **Dynamic Prompt Templating**
Utilizes Jinja2 templates for context-aware prompt generation, allowing for sophisticated system and user prompts that adapt to conversation context.

### 💬 **Rich Slack Interaction**
Provides visual feedback through reactions and supports threaded conversations, creating an intuitive user experience that indicates processing status.

### 🎛️ **Extensible Architecture**
Designed for customization through subclassing, allowing developers to override response generation for specialized use cases.

## Architecture

### Core Components

#### **Prompt System**
TigerAgent uses a two template for generating prompts:

- **system_prompt.md**: Defines the AI's role, capabilities, and behavior patterns
- **user_prompt.md**: Contains the user's request with relevant context

Templates have access to a rich context object containing:
- `event`: Complete Event object with processing metadata
- `mention`: AppMentionEvent with Slack message details
- `bot`: Bot profile information and capabilities
- `user`: User profile including timezone preferences
- `local_time`: Event timestamp in user's local timezone

#### **MCP Server Ecosystem**
MCP servers provide specialized capabilities through a standardized protocol:

- **Streamable HTTP Servers**: For cloud-based services and APIs
- **stdio Servers**: For command-line tools and local utilities
- **Tool Prefixes**: Organize tools by domain (e.g., `slack_`, `docs_`, `github_`)
- **Dynamic Loading**: Servers can be enabled/disabled via configuration

#### **Context Building**
Each event processing cycle builds comprehensive context:

1. **Event Processing**: Extracts Slack event details and metadata
2. **User Enrichment**: Fetches user profile and timezone information
3. **Bot Information**: Includes bot capabilities and identity
4. **Temporal Context**: Provides event timing in user's local timezone

## Configuration

### MCP Server Configuration

TigerAgent loads MCP servers from a JSON configuration file. There are two types of MCP servers:

#### **Streaming HTTP Servers**
For remote MCP services accessible over HTTP:

```json
{
  "slack_server": {
    "tool_prefix": "slack",
    "url": "http://slack-mcp-server/mcp",
    "allow_sampling": false,
    "disabled": false
  },
  "docs_server": {
    "tool_prefix": "docs",
    "url": "https://docs-api.example.com/mcp",
    "allow_sampling": true,
    "disabled": false
  }
}
```

#### **stdio Servers**
For command-line MCP servers that run as local processes:

```json
{
  "logfire_server": {
    "command": "uvx",
    "args": ["logfire-mcp"],
    "env": {
      "LOGFIRE_READ_TOKEN": "your_token_here"
    },
    "disabled": false
  },
  "local_tool": {
    "command": "python",
    "args": ["/path/to/mcp_server.py", "--config", "prod"],
    "env": {
      "DATABASE_URL": "postgresql://...",
      "API_KEY": "secret"
    },
    "disabled": false
  }
}
```

**Configuration Features**:
- **Tool Prefixing**: Prevents naming conflicts between servers
- **Selective Enabling**: Disable servers without removing configuration
- **Environment Variables**: Pass secrets and configuration to servers
- **Sampling Control**: Fine-tune model behavior per server

### Template Configuration

Templates are loaded from the filesystem using Jinja2:

```python
agent = TigerAgent(
    jinja_env=Path("/path/to/templates"),  # Template directory
    # or
    jinja_env=Environment(enable_async=True, ...)  # Custom environment
)
```

**Required Templates**:
- `system_prompt.md`: AI system instructions and capabilities
- `user_prompt.md`: User request formatting and context

**Template Context Variables**:
- `{{event}}`: Complete event object with processing metadata
- `{{mention}}`: Slack message content and threading information
- `{{bot}}`: Bot identity and capabilities
- `{{user}}`: User profile and preferences
- `{{local_time}}`: Event timestamp in user's timezone

## Getting Started

### Project Setup

To create a custom Tiger Agent implementation, first create a Python project:

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

## Usage Patterns

### Basic Usage

```python
from tiger_agent import TigerAgent, EventHarness

# Create agent with default configuration
agent = TigerAgent(
    model="claude-3-5-sonnet-latest",
    jinja_env=Path("./templates"),
    mcp_config_path=Path("./mcp_config.json")
)

# Use with EventHarness
harness = EventHarness(event_processor=agent)
await harness.run()
```

### Advanced Configuration

```python
# Custom Jinja2 environment with additional filters
from jinja2 import Environment, FileSystemLoader

jinja_env = Environment(
    enable_async=True,
    loader=FileSystemLoader("templates"),
    trim_blocks=True,
    lstrip_blocks=True
)

agent = TigerAgent(
    model=models.Model("gpt-4"),
    jinja_env=jinja_env,
    mcp_config_path=Path("config/mcp_servers.json"),
    max_attempts=5
)
```

### Customization Through Subclassing

TigerAgent is designed for extension through inheritance. You can subclass TigerAgent and override the `generate_response(...)` method to customize exactly how responses are generated:

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

For simpler customizations, you can also override specific aspects:

```python
class CustomTigerAgent(TigerAgent):
    async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
        # Add custom pre-processing
        if self._should_use_custom_logic(event):
            return await self._custom_response_logic(hctx, event)

        # Use default logic with modifications
        response = await super().generate_response(hctx, event)
        return self._post_process_response(response, event)

    def _should_use_custom_logic(self, event: Event) -> bool:
        # Custom routing logic
        mention = event.event
        return "urgent" in mention.text.lower()

    async def _custom_response_logic(self, hctx: HarnessContext, event: Event) -> str:
        # Specialized handling for urgent requests
        return "Urgent request detected. Escalating to human support."
```

**Common Customization Patterns**:

#### **Request Routing**
```python
async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
    mention = event.event

    if "@channel" in mention.text:
        return await self._handle_broadcast_request(hctx, event)
    elif mention.thread_ts:
        return await self._handle_threaded_conversation(hctx, event)
    else:
        return await super().generate_response(hctx, event)
```

#### **Response Filtering**
```python
async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
    response = await super().generate_response(hctx, event)

    # Apply content filtering
    if self._contains_sensitive_info(response):
        return "I cannot provide that information in this channel."

    return response
```

#### **Context Enhancement**
```python
async def generate_response(self, hctx: HarnessContext, event: Event) -> str:
    # Add custom context before generating response
    async with hctx.pool.connection() as conn:
        custom_data = await self._fetch_custom_context(conn, event)

    # Temporarily store custom data for template access
    original_method = self.make_system_prompt
    async def enhanced_system_prompt(ctx):
        ctx["custom_data"] = custom_data
        return await original_method(ctx)

    self.make_system_prompt = enhanced_system_prompt
    try:
        return await super().generate_response(hctx, event)
    finally:
        self.make_system_prompt = original_method
```

## Alternative: Implementing EventProcessor

For even more customizability, you can implement an EventProcessor directly to control every aspect of the interaction. This can be a simple function which is passed to the EventHarness:

```python
import asyncio
from tiger_agent import EventHarness

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

Alternatively, you can create a class that implements EventProcessor. This is handy if you need state:

```python
import asyncio
from tiger_agent import EventHarness

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

## Interaction Flow

### Success Path

1. **Event Reception**: EventHarness delivers Slack app_mention event
2. **Visual Feedback**: Adds `:spinthinking:` reaction to indicate processing
3. **Context Building**: Fetches user info, bot info, and builds template context
4. **Prompt Generation**: Renders system and user prompts from Jinja2 templates
5. **AI Processing**: Creates Pydantic-AI agent with MCP toolsets and generates response
6. **Response Delivery**: Posts response to Slack thread or channel
7. **Success Indication**: Removes `:spinthinking:` and adds `:white_check_mark:`

### Error Handling

1. **Exception Capture**: Any processing failure is caught and logged
2. **Visual Feedback**: Removes `:spinthinking:` and adds `:x:` reaction
3. **User Communication**: Posts explanatory message to user
4. **Retry Logic**: Re-raises exception for EventHarness retry handling
5. **Adaptive Messaging**: Error message adapts based on retry count

**Error Message Patterns**:
- During retries: "I experienced an issue trying to respond. I will try again."
- Final failure: "I experienced an issue trying to respond. I give up. Sorry."

## Integration Patterns

### With EventHarness
TigerAgent implements the EventProcessor interface, making it compatible with the EventHarness architecture for scalable event processing.

### With MCP Ecosystem
Supports the full MCP protocol specification, enabling integration with:
- Documentation systems (search, retrieval)
- Development tools (GitHub, Linear, Jira)
- Data sources (databases, APIs)
- Observability systems (Logfire, monitoring)

### With Slack Platform
Leverages Slack's rich interaction model:
- Threaded conversations for context continuity
- Reaction-based status indication
- Channel and direct message support
- User profile integration

TigerAgent represents the convergence of modern AI capabilities with practical chat interface design, providing a foundation for building sophisticated conversational AI systems that can scale with organizational needs while remaining customizable for specific use cases.