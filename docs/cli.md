# Tiger Agent CLI

Tiger Agent provides a command-line interface for running custom AI-powered Slack bots. The CLI allows you to quickly deploy a TigerAgent instance with custom prompts and MCP server integrations without writing Python code.

## Overview

The CLI tool (`tiger-agent`) provides two main commands:
- **`run`**: Start the Tiger Agent bot with custom configuration
- **`migrate`**: Run database migrations to set up or update the database schema (Not usually necessary)

## Running and Installing the CLI

Run the Tiger Agent CLI

```bash
uvx --from git+https://github.com/timescale/tiger-agent.git tiger-agent --help
```

Run a specific version using a git tag (v0.0.1 in the example)

```bash
uvx --from git+https://github.com/timescale/tiger-agent.git@v0.0.1 tiger-agent --help
```

Install Tiger Agent as a tool

```bash
# install the tool
uv tool install --from git+https://github.com/timescale/tiger-agent.git@v0.0.1

# test the installation
tiger-agent --help
```

## Prerequisites

Before running Tiger Agent, you need:

1. **PostgreSQL database** with TimescaleDB extension
2. **Environment variables** for Slack and database connectivity
3. **Prompt templates** (system_prompt.md and user_prompt.md)
4. **MCP configuration** (optional, for extended capabilities)

## Setup

### A Database

You will need a PostgreSQL database with the TimescaleDB extension.

You can use docker:

```bash
# pull the latest image
docker pull timescale/timescaledb-ha:pg17

# run the database container
docker run -d --name tiger-agent -e POSTGRES_PASSWORD=password -p 127.0.0.1:5432:5432 timescale/timescaledb-ha:pg17
```

### Project Structure

Your project structure will look like this:

```
my-tiger-agent/
├── .env
├── mcp_config.json
└── prompts/
    ├── system_prompt.md
    └── user_prompt.md
```

```bash
mkdir my-tiger-agent

cd my-tiger-agent
```

### Environment Variables

Create a `.env` file to put your environment variables in. Copy [.env.sample](/.env.sample) to get started.
The two Slack tokens, the Anthropic API key, and the database connection details are required.

Alternately, you can set the environment variables in your shell session:

```bash
# 1. Set up environment variables
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_APP_TOKEN="xapp-your-app-token"
ANTHROPIC_API_KEY="sk-ant-api03-your-api-key"
export PGHOST="localhost"
export PGDATABASE="postgres"
export PGUSER="postgres"
export PGPASSWORD="password"
```

### Prompts

Create a directory to put your prompt templates in.

```bash
mkdir prompts
```

Copy the [system_prompt.md](/prompts/system_prompt.md) and [user_prompt.md](/prompts/user_prompt.md) into the `prompts` directory you just created.
Use these Jinja2 templates as a starting point for customizing the instructions for your agent.
See [Customizing the Prompt Templates](#customizing-the-prompt-templates) for detailed instructions.

### MCP Server Config File (Optional)

You can give your agent capabilities by configuring MCP Servers for it to use.
Copy [examples/mcp_config.json](/examples/mcp_config.json) to your project as an example to get started.
Read [MCP Server Configuration](#mcp-server-configuration) for detailed instructions.

## Run the Agent

```bash
tiger-agent run [OPTIONS]
```

#### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | `anthropic:claude-sonnet-4-20250514` | AI model to use |
| `--prompts` | `./prompts` | Directory containing prompt templates |
| `--mcp-config` | None | Path to MCP configuration JSON file |
| `--env` | Auto-detected | Path to custom environment file |
| `--worker-sleep-seconds` | `60` | Base worker sleep duration |
| `--worker-min-jitter-seconds` | `-15` | Minimum jitter for worker sleep |
| `--worker-max-jitter-seconds` | `15` | Maximum jitter for worker sleep |
| `--max-attempts` | `3` | Maximum retry attempts per event |
| `--max-age-minutes` | `60` | Event expiration time |
| `--invisibility-minutes` | `10` | Task claim duration |
| `--num-workers` | `5` | Number of concurrent workers |

#### Examples

```bash
# Basic usage with default settings
tiger-agent run --prompts ./my-prompts

# Custom model and MCP integration
tiger-agent run \
  --model "openai:gpt-4" \
  --prompts ./prompts \
  --mcp-config ./config/mcp_servers.json

# Production configuration with custom worker settings
tiger-agent run \
  --prompts ./prompts \
  --mcp-config ./mcp_config.json \
  --num-workers 10 \
  --max-attempts 5 \
  --worker-sleep-seconds 30

# Using custom environment file
tiger-agent run \
  --prompts ./prompts \
  --env .env.production
```


## Customizing the Prompt Templates

Tiger Agent requires two Jinja2 template files in the prompts directory:

#### `system_prompt.md`

Defines the AI's role, capabilities, and behavior:

```markdown
# Tiger Agent System Prompt

You are Tiger Agent, an AI assistant integrated into Slack via {{bot.name}}.

## Your Capabilities
- Access to real-time information through connected tools
- Ability to help with {{user.real_name}}'s requests in {{bot.team}}
- Context-aware responses based on user timezone ({{user.tz_label}})

## Available Tools
{% if mention.text contains "help" %}
You can help with documentation, code analysis, project management, and more.
{% endif %}

## Guidelines
- Be helpful and concise
- Use threaded replies when appropriate
- Reference user by name: {{user.real_name or user.name}}
- Consider local time: {{local_time.strftime('%I:%M %p %Z')}}
```

#### `user_prompt.md`

Formats the user's request with context:

```markdown
# Request from {{user.real_name or user.name}}

**Message:** {{mention.text}}

**Context:**
- Channel: {{mention.channel}}
- Time: {{local_time.strftime('%Y-%m-%d %I:%M %p %Z')}}
{% if mention.thread_ts %}
- Thread: This is part of an ongoing conversation
{% endif %}

**User Profile:**
- Timezone: {{user.tz_label}}
- Team: {{bot.team}}

Please respond appropriately to this request.
```

#### Available Template Variables

| Variable | Description |
|----------|-------------|
| `event` | Complete Event object with processing metadata |
| `mention` | AppMentionEvent with message details |
| `bot` | Bot information (name, team, capabilities) |
| `user` | User profile (real_name, timezone, etc.) |
| `local_time` | Event timestamp in user's timezone |

## MCP Server Configuration

Configure external tools and capabilities via `mcp_config.json`:

### HTTP-based MCP Servers

```json
{
  "docs_server": {
    "tool_prefix": "docs",
    "url": "https://docs-mcp-server.example.com/mcp",
    "allow_sampling": false,
    "disabled": false
  },
  "github_server": {
    "tool_prefix": "github",
    "url": "http://localhost:3001/mcp",
    "allow_sampling": true,
    "disabled": false
  }
}
```

### Command-line MCP Servers

```json
{
  "logfire_tools": {
    "command": "uvx",
    "args": ["logfire-mcp"],
    "env": {
      "LOGFIRE_READ_TOKEN": "your_token"
    },
    "disabled": false
  },
  "custom_tools": {
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
