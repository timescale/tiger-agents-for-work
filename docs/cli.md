# Tiger Agent CLI

Tiger Agent provides a command-line interface for running custom AI-powered Slack bots. The CLI allows you to quickly deploy a TigerAgent instance with custom prompts and MCP server integrations without writing Python code.

## Overview

The CLI tool (`tiger-agent`) provides two main commands:
- **`run`**: Start the Tiger Agent bot with custom configuration
- **`migrate`**: Run database migrations to set up or update the database schema

## Installation & Setup

```bash
# Install tiger-agent
pip install tiger-agent
# or
uv add tiger-agent

# Verify installation
tiger-agent --help
```

## Basic Usage

### Prerequisites

Before running Tiger Agent, you need:

1. **Environment variables** for Slack and database connectivity
2. **Prompt templates** (system_prompt.md and user_prompt.md)
3. **MCP configuration** (optional, for extended capabilities)
4. **PostgreSQL database** with TimescaleDB extension

### Minimal Setup

```bash
# 1. Set up environment variables
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_APP_TOKEN="xapp-your-app-token"
export PGHOST="localhost"
export PGDATABASE="tiger_agent"
export PGUSER="your_user"
export PGPASSWORD="your_password"

# 2. Run database migrations
tiger-agent migrate

# 3. Start the bot (requires prompts directory)
tiger-agent run --prompts ./prompts
```

## Commands

### `run` - Start the Agent

Starts the Tiger Agent bot with EventHarness for processing Slack app_mention events.

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

### `migrate` - Database Migrations

Runs database migrations to create or update the Tiger Agent database schema.

```bash
tiger-agent migrate [OPTIONS]
```

#### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--env` | Auto-detected | Path to custom environment file |

#### Examples

```bash
# Run migrations with auto-detected .env
tiger-agent migrate

# Run migrations with custom environment
tiger-agent migrate --env .env.production
```

## Configuration Files

### Prompt Templates

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

### MCP Server Configuration

Configure external tools and capabilities via `mcp_config.json`:

#### HTTP-based MCP Servers

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

#### Command-line MCP Servers

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

## Environment Configuration

### Required Environment Variables

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here

# Database Configuration
PGHOST=localhost
PGDATABASE=tiger_agent
PGUSER=your_username
PGPASSWORD=your_password
PGPORT=5432  # optional, defaults to 5432

# Optional: AI Model Configuration
ANTHROPIC_API_KEY=your_anthropic_key
OPENAI_API_KEY=your_openai_key

# Optional: Observability
LOGFIRE_TOKEN=your_logfire_token
SERVICE_NAME=my-tiger-agent
```

### Environment File Support

Tiger Agent supports `.env` files for configuration:

```bash
# .env file
SLACK_BOT_TOKEN=xoxb-123...
SLACK_APP_TOKEN=xapp-456...
PGHOST=db.example.com
PGDATABASE=production_tiger
PGUSER=tiger_user
PGPASSWORD=secure_password
ANTHROPIC_API_KEY=sk-ant-123...
LOGFIRE_TOKEN=logfire-789...
```

## Complete Setup Example

Here's a complete example of setting up Tiger Agent from scratch:

### 1. Project Structure

```
my-tiger-agent/
├── .env
├── mcp_config.json
└── prompts/
    ├── system_prompt.md
    └── user_prompt.md
```

### 2. Environment Setup (`.env`)

```bash
# Slack
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# Database
PGHOST=localhost
PGDATABASE=tiger_agent_dev
PGUSER=postgres
PGPASSWORD=postgres

# AI
ANTHROPIC_API_KEY=sk-ant-your-key

# Observability (optional)
LOGFIRE_TOKEN=your-logfire-token
SERVICE_NAME=my-custom-agent
```

### 3. MCP Configuration (`mcp_config.json`)

```json
{
  "logfire": {
    "command": "uvx",
    "args": ["logfire-mcp"],
    "env": {
      "LOGFIRE_READ_TOKEN": "your_read_token"
    }
  },
  "docs": {
    "tool_prefix": "docs",
    "url": "http://localhost:8000/mcp",
    "allow_sampling": false,
    "disabled": true
  }
}
```

### 4. System Prompt (`prompts/system_prompt.md`)

```markdown
# Customer Support Agent

You are a helpful customer support agent for {{bot.team}}.

## Your Role
- Help team members with questions and requests
- Provide accurate information using available tools
- Be professional and friendly

## Context
- Current user: {{user.real_name}}
- User timezone: {{user.tz_label}}
- Current time: {{local_time.strftime('%I:%M %p')}}

## Guidelines
- Always greet users by name
- Use tools to find accurate information
- Ask clarifying questions when needed
- Keep responses concise but helpful
```

### 5. User Prompt (`prompts/user_prompt.md`)

```markdown
**User Request from {{user.real_name}}:**

{{mention.text}}

{% if mention.thread_ts %}
*This is part of an ongoing thread conversation.*
{% endif %}

**Additional Context:**
- Time: {{local_time.strftime('%A, %B %d at %I:%M %p %Z')}}
- Channel: {{mention.channel}}

Please help with this request using available tools and information.
```

### 6. Run the Agent

```bash
# First, run migrations
tiger-agent migrate

# Then start the agent
tiger-agent run \
  --prompts ./prompts \
  --mcp-config ./mcp_config.json \
  --num-workers 3
```

## Advanced Usage

### Custom Models

Tiger Agent supports various AI model providers:

```bash
# Anthropic Claude
tiger-agent run --model "anthropic:claude-3-5-sonnet-20241022"
tiger-agent run --model "anthropic:claude-3-5-haiku-20241022"

# OpenAI
tiger-agent run --model "openai:gpt-4"
tiger-agent run --model "openai:gpt-3.5-turbo"

# Custom model endpoints
tiger-agent run --model "custom:my-model"
```

### Performance Tuning

For high-traffic Slack workspaces:

```bash
tiger-agent run \
  --prompts ./prompts \
  --mcp-config ./mcp_config.json \
  --num-workers 15 \
  --worker-sleep-seconds 30 \
  --max-attempts 5 \
  --invisibility-minutes 5
```

### Development vs Production

Development setup:
```bash
tiger-agent run \
  --prompts ./dev-prompts \
  --num-workers 2 \
  --env .env.dev
```

Production setup:
```bash
tiger-agent run \
  --prompts ./prod-prompts \
  --mcp-config ./prod-mcp-config.json \
  --num-workers 10 \
  --max-attempts 5 \
  --env .env.production
```

## Troubleshooting

### Common Issues

1. **"No prompts directory"**
   - Ensure `--prompts` points to a directory containing `system_prompt.md` and `user_prompt.md`

2. **"Database connection failed"**
   - Verify PostgreSQL is running and environment variables are correct
   - Run `tiger-agent migrate` first

3. **"Slack authentication failed"**
   - Check `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are valid
   - Ensure bot is installed in workspace

4. **"MCP server connection failed"**
   - Verify MCP server URLs are accessible
   - Check command-line MCP servers can be executed
   - Review environment variables for MCP servers

### Debugging

Enable verbose logging:
```bash
export LOGFIRE_TOKEN=your-token  # For comprehensive tracing
tiger-agent run --prompts ./prompts
```

Check database connection:
```bash
tiger-agent migrate  # Should complete without errors
```

Test MCP configuration:
```bash
# Disable all MCP servers in config to isolate issues
# Set "disabled": true for all servers in mcp_config.json
```

## Integration with CI/CD

### Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy configuration
COPY prompts/ ./prompts/
COPY mcp_config.json .
COPY .env .

# Run migrations and start agent
CMD ["sh", "-c", "tiger-agent migrate && tiger-agent run --prompts ./prompts --mcp-config ./mcp_config.json"]
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tiger-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: tiger-agent
  template:
    metadata:
      labels:
        app: tiger-agent
    spec:
      containers:
      - name: tiger-agent
        image: your-registry/tiger-agent:latest
        command:
          - tiger-agent
          - run
          - --prompts
          - /app/prompts
          - --mcp-config
          - /app/mcp_config.json
          - --num-workers
          - "5"
        env:
        - name: SLACK_BOT_TOKEN
          valueFrom:
            secretKeyRef:
              name: slack-secrets
              key: bot-token
        - name: PGHOST
          value: "postgres-service"
```

The Tiger Agent CLI provides a powerful, flexible way to deploy custom AI-powered Slack bots with minimal configuration while maintaining full access to the sophisticated EventHarness architecture and MCP server ecosystem.