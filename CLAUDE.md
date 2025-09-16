# Tiger Agent - Architecture, Guidelines

## Architecture

See [architecture.md](./architecture.md) for detailed technical documentation of Tiger Agent's event processing and MCP server architecture.

## Setup

### Database Migrations

**Migration System** (`/migrations/`):
- **Incremental**: Version-based schema changes (`/migrations/incremental/`)
- **Idempotent**: Repeatable operations (`/migrations/idempotent/`)
- **Safety**: Uses PostgreSQL advisory locks for concurrent execution

### Environment Configuration

**Required Variables**:
- Database: `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
- Slack: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_DOMAIN` 
- External APIs: `ANTHROPIC_API_KEY`, `LOGFIRE_TOKEN`

**MCP Server URLs**: `{SERVICE}_MCP_SERVER_URL` for each of the 6 services
**Feature Toggles**: `DISABLE_{SERVICE}_MCP_SERVER` to disable any MCP server

Run migrations: `uv run python -m migrations.runner`

## Development & Debugging

### Agent Development Patterns

**Creating New Agents**:
1. Implement in `/app/agents/` following existing patterns
2. Add as tool definition to EON orchestrator (`/app/agents/eon.py`)
3. Use `AgentContext` for user context and memory access
4. Handle MCP server connectivity gracefully (they can be disabled)

**Agent Context Structure**:
```python
class AgentContext(BaseModel):
    thread_ts: str | None              # Slack thread context
    bot_user_id: str | None           # Bot identification
    channel: str | None               # Slack channel
    memories: list[Memory] | None     # User memory/preferences
    slack_user_metadata: SlackUserResult | None  # User profile data
    user_id: str | None               # Scoping key for operations
```

### Debugging

**VS Code**: Configured launch configurations available (`.vscode/launch.json`)
**Remote Debug**: Port 5678 available when `DEBUG=true` and `DEBUG_WAIT_FOR_ATTACH=true`
**Docker Debugging**: Use `docker-compose logs -f [service]` for container logs

## Important Files

- `/app/agents/eon.py`: Main orchestrator agent - entry point for all requests
- `/app/mcp_servers.py`: MCP server factory functions with conditional instantiation
- `/app/events.py`: Slack event handling and message processing
- `/app/utils/mcp.py`: Utility functions for MCP server interactions with disable checks
- `/migrations/runner.py`: Database migration orchestrator
- `docker-compose.yml`: Multi-container setup with 6 MCP servers + app + database
- `start.sh`: Custom startup script respecting MCP server disable flags


### Development Commands

**Package Management**: Uses UV (modern Python package manager)
```bash
# Setup and installation
uv sync                                    # Install dependencies
cp .env.sample .env                       # Setup environment variables

# Local development
uv run python -m app.main                # Start agent locally
uv run python -m migrations.runner       # Run database migrations

# Code quality
uv run ruff format                        # Format code
uv run ruff check                         # Lint code
uv run ruff format && uv run ruff check  # Format and lint together

# Docker development
./start.sh                               # Start all services with selective MCP server startup
docker-compose build                     # Build all containers
docker-compose up -d app db              # Start core services only
docker-compose up -d                     # Start all services
docker-compose logs -f app               # Follow application logs
```

## Monitoring & Observability

**Logfire Integration:**
- All major operations instrumented with spans
- Database queries automatically traced
- Worker activity tracked with reason codes ("triggered" vs "timeout")
- Event data included in traces for debugging

**Key Metrics:**
- Event processing latency
- Retry rates and failure patterns
- Worker utilization and timeout frequency
- Database query performance