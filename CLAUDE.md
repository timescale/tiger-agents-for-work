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

### Event Processing Patterns

**Event Handler Development**:
1. Implement event processors in `/tiger_agent/` following existing patterns
2. Use the AgentHarness for event lifecycle management
3. Handle database operations through provided connection pools
4. Implement proper error handling and retry logic

### Debugging

**VS Code**: Configured launch configurations available (`.vscode/launch.json`)
**Remote Debug**: Port 5678 available when `DEBUG=true` and `DEBUG_WAIT_FOR_ATTACH=true`
**Docker Debugging**: Use `docker-compose logs -f [service]` for container logs

## Important Files

- `/tiger_agent/harness.py`: Core event processing harness - manages workers and event lifecycle
- `/tiger_agent/main.py`: Application bootstrap and TaskGroup orchestration
- `/tiger_agent/migrations/runner.py`: Database migration orchestrator
- `docker-compose.yml`: Multi-container setup with app + database + slack ingest
- `start.sh`: Service startup script


### Development Commands

**Package Management**: Uses UV (modern Python package manager)
```bash
# Setup and installation
uv sync                                    # Install dependencies
cp .env.sample .env                       # Setup environment variables

# Local development
uv run python -m tiger_agent.main           # Start harness locally
uv run python -m tiger_agent.migrations.runner  # Run database migrations

# Code quality
uv run ruff format                        # Format code
uv run ruff check                         # Lint code
uv run ruff format && uv run ruff check  # Format and lint together

# Docker development
./start.sh                               # Start all services
docker-compose build                     # Build all containers
docker-compose up -d app db tiger-slack-ingest  # Start core services
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