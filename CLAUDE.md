# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Tiger Agent is a production-ready Python library for building AI-powered Slack bots with enterprise-grade reliability. It processes thousands of concurrent conversations using a PostgreSQL + TimescaleDB-backed event system with atomic operations and horizontal scalability.

### Core Architecture

`TigerApp` (`tiger_agent/app.py`) is the top-level entry point. It wires together four main pieces, all sharing a single `HarnessContext`:

1. **ListenerHarness** (`tiger_agent/listeners/harness.py`): the **Listener pattern** — normalizes external events into durable tasks. Composes `SlackListener` (always) and `SalesforceListener` (when Salesforce credentials are configured), each implementing the `Listener.start(tasks)` interface
2. **TaskHarness** (`tiger_agent/tasks/harness.py`): PostgreSQL-backed work queue system with atomic claiming, retry logic, and bounded concurrency across multiple worker instances. Agnostic to event type — it just claims tasks and hands them to a `TaskProcessor`
3. **TaskProcessor / TaskHandler** (`tiger_agent/tasks/handlers/`): the **Handler pattern** — `TaskProcessor` dispatches each claimed task by its event type to a registered `TaskHandler` (one handler class per event type, declared via `EVENT_TYPES`), centralizing retry/error handling and Slack feedback
4. **TigerAgent** (`tiger_agent/agent/tiger_agent.py`): AI-powered response generator using Pydantic-AI agents with MCP (Model Context Protocol) server integrations and Jinja2 template-based prompt generation. Used by most `TaskHandler`s, but a handler doesn't have to call it (e.g. `SalesforceCaseStatusChangedHandler` just posts a status line)

Database Layer (`tiger_agent/migrations/`): TimescaleDB schema with `agent.event` as the durable work queue (holding every event type, not just Slack), `agent.salesforce_case_thread` linking Slack threads to Salesforce cases, and atomic database functions for task processing. See [docs/database.md](/docs/database.md).

See [docs/event_harness.md](/docs/event_harness.md) for the Listener and Handler patterns in depth, and [docs/salesforce_sync.md](/docs/salesforce_sync.md) for the Salesforce Case ↔ Slack thread sync workflows.

### Key Integration Points

- **Slack Integration**: Uses Slack Events API with Socket Mode. Handles `app_mention`, `message` (DMs, case-linked-thread replies, proactive prompts), block actions/view submissions (new-case form, feedback form, proactive-prompt buttons), and admin slash commands — not just `app_mention`
- **Salesforce Integration**: Optional; when configured, bidirectionally syncs Case activity (creation, assignment, status changes, Chatter posts/emails) with a linked Slack thread. Combines PushTopic streaming with two reconciliation pollers (`tiger_agent/salesforce/new_case_poller.py`, `tiger_agent/salesforce/case_feed_item_poller.py`) since Salesforce has no push mechanism for every object type involved
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

- `tiger_agent/app.py`: `TigerApp` — wires `ListenerHarness` + `TaskHarness` + the registered `TaskHandler`s into one runnable app; handles soft shutdown
- `tiger_agent/agent/tiger_agent.py`: Main TigerAgent class, the AI response generator most handlers call into
- `tiger_agent/listeners/harness.py`, `slack.py`, `salesforce.py`: the Listener pattern — `ListenerHarness` composes `SlackListener` and (when configured) `SalesforceListener`
- `tiger_agent/salesforce/new_case_poller.py`, `case_feed_item_poller.py`: reconciliation pollers backing `SalesforceListener` for object types with no push notification support
- `tiger_agent/tasks/harness.py`: `TaskHarness` — worker pool orchestrator and database work queue logic
- `tiger_agent/tasks/handlers/`: the Handler pattern — `TaskHandler`/`TaskProcessor` base classes plus one handler module per event type (Slack, Salesforce case sync, feedback, user-defined rules)
- `tiger_agent/tasks/types.py`: `Task` (the discriminated event union), used alongside `HarnessContext` (`tiger_agent/types.py`)
- `tiger_agent/slack/`: Slack API integration utilities (posting, reactions, user info, slash-command registry)
- `tiger_agent/salesforce/`: Salesforce client, pollers, and sync utilities
- `tiger_agent/main.py`: CLI entry point with Click commands
- `tiger_agent/migrations/`: Database schema and migration system

### Data Flow

1. Slack/Salesforce events → listeners (`SlackListener`, `SalesforceListener`, and their backing pollers) normalize the payload into a typed `Task.event`
2. Tasks stored in `agent.event` table; one worker is immediately "poked" via `HarnessContext.trigger`
3. Worker pool (`TaskHarness`) claims tasks atomically via `agent.claim_event()`
4. `TaskProcessor` dispatches the claimed task to the `TaskHandler` registered for its event type; most handlers call into `TigerAgent` (template rendering → Pydantic-AI → MCP tools → Slack/Salesforce response), a few just post a message directly
5. On success the task moves to `agent.event_hist` (`delete_event`); on failure it stays claimable for retry, with the `TaskProcessor` posting a Slack error reaction/message where applicable

See [docs/event_harness.md](/docs/event_harness.md) for this flow in depth and [docs/salesforce_sync.md](/docs/salesforce_sync.md) for the Salesforce Case ↔ Slack thread sync specifically.

### Customization Patterns

- **Prompt Templates**: Modify Jinja2 templates in `/prompts/` for different contexts
- **MCP Integration**: Add servers to `mcp_config.json` for new capabilities (Slack search, docs, memory, etc.)
- **Subclassing**: Extend TigerAgent class for specialized processing logic
- **Custom TaskHandler**: Subclass `TaskHandler`, declare `EVENT_TYPES`, and register it with a `TaskProcessor` to add handling for a new event type
- **Custom TaskProcessor**: Implement the raw `TaskProcessor` callable from scratch (bypassing the handler registry entirely) for non-AI or single-purpose use cases

The system emphasizes durability, observability, and horizontal scaling while maintaining simplicity for basic AI bot use cases.