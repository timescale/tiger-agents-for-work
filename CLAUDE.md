# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Tiger Agent is a production-ready Python library for building AI-powered Slack bots with enterprise-grade reliability. It processes thousands of concurrent conversations using a PostgreSQL + TimescaleDB-backed event system with atomic operations and horizontal scalability.

### Core Architecture

The system is built around three main components:

1. **EventHarness** (`tiger_agent/harness.py`): PostgreSQL-backed work queue system that manages Slack `app_mention` events with atomic claiming, retry logic, and bounded concurrency across multiple worker instances
2. **TigerAgent** (`tiger_agent/agent.py`): AI-powered event processor using Pydantic-AI agents with MCP (Model Context Protocol) server integrations and Jinja2 template-based prompt generation
3. **Database Layer** (`tiger_agent/migrations/`): TimescaleDB schema with agent.event table as durable work queue and atomic database functions for event processing

### Key Integration Points

- **Slack Integration**: Uses Slack Events API with Socket Mode for real-time `app_mention` event processing
- **MCP Servers**: Extensible tool system via HTTP/STDIO MCP servers (configured in `mcp_config.json`)
- **Template System**: Jinja2 templates in `/prompts/` directory for dynamic context-aware prompt generation
- **Observability**: Full Logfire instrumentation for tracing event flow and database operations

## Development Commands

### Essential Commands

```bash
# Install dependencies
uv sync

# Run the bot
uv run tiger_agent run

# Run database migrations
uv run tiger_agent migrate

# Lint code
uv run ruff check

# Format code
uv run ruff format

# Lint and auto-fix
uv run ruff check --fix
```

### Database Setup

Tiger Agent requires a PostgreSQL database with TimescaleDB extension. For development:

```bash
# Run TimescaleDB in Docker
docker run -d --name tiger-agent \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=tsdb \
  -e POSTGRES_USER=tsdbadmin \
  -p 127.0.0.1:5432:5432 \
  timescale/timescaledb-ha:pg17
```

### Configuration

- Environment variables: Copy `.env.sample` to `.env` and configure:
  - `SLACK_APP_TOKEN` (xapp-*) and `SLACK_BOT_TOKEN` (xoxb-*)
  - `ANTHROPIC_API_KEY` (sk-ant-*)
  - PostgreSQL connection vars (PGHOST, PGDATABASE, etc.)
  - Optional: `LOGFIRE_TOKEN` for observability

- MCP servers: Configure in `mcp_config.json` for extending agent capabilities with external tools

## Code Structure

### Core Modules

- `tiger_agent/agent.py`: Main TigerAgent class implementing EventProcessor interface
- `tiger_agent/harness.py`: EventHarness orchestrator and database work queue logic
- `tiger_agent/slack.py`: Slack API integration utilities (posting, reactions, user info)
- `tiger_agent/main.py`: CLI entry point with Click commands
- `tiger_agent/migrations/`: Database schema and migration system

### Data Flow

1. Slack events → EventHarness via Socket Mode
2. Events stored in `agent.event` table with status tracking
3. Worker pool claims events atomically via database functions
4. TigerAgent processes events: template rendering → Pydantic-AI → MCP tools → Slack response
5. Event status updated (completed/failed) with full tracing

### Customization Patterns

- **Prompt Templates**: Modify Jinja2 templates in `/prompts/` for different contexts
- **MCP Integration**: Add servers to `mcp_config.json` for new capabilities (Slack search, docs, memory, etc.)
- **Subclassing**: Extend TigerAgent class for specialized processing logic
- **Custom EventProcessor**: Implement EventProcessor interface from scratch for non-AI use cases

The system emphasizes durability, observability, and horizontal scaling while maintaining simplicity for basic AI bot use cases.