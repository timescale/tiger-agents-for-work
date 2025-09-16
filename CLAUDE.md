# Tiger Agent - Architecture, Guidelines

## Architecture

### Event Processing Architecture

#### Overview

Tiger Agent implements a sophisticated event-driven architecture for processing Slack app mentions with guaranteed delivery, retry logic, and bounded concurrency. The system combines real-time event processing with robust failure handling through a hybrid queue-database approach.

#### Architecture Components

##### 1. Event Flow Pipeline

```
Slack Event → AgentHarness._on_event → Database Storage → Queue Signal → Worker Pool → Event Processing
```

**Key Files:**
- `tiger_agent/harness.py` - Core event processing logic (AgentHarness)
- `tiger_agent/main.py` - Application bootstrap and TaskGroup orchestration
- `tiger_agent/migrations/` - Database schema and stored procedures

##### 2. Core Components

###### AgentHarness Class (`tiger_agent/harness.py`)

**Purpose:** Central orchestrator that manages the entire event lifecycle from Slack webhooks to completion, including TaskGroup coordination and Slack handler management.

**Key Methods:**
- `run(app_token, task_group, num_workers)` - Main entry point that sets up workers and Slack handler
- `_on_event()` - Acknowledges Slack events and triggers processing
- `_process_event()` - Claims and processes individual events from the database
- `_worker()` - Worker loop that waits for triggers and processes events
- Database operations: `_insert_event()`, `_claim_event()`, `_delete_event()`

###### Database Schema (`migrations/`)

**Tables:**
- `agent.event` - Active events pending processing
- `agent.event_hist` - Completed/failed events (audit trail)

**Key Fields:**
- `vt` (visibility threshold) - Controls when events become available for processing
- `attempts` - Retry counter with configurable maximum
- `event_ts` - Original event timestamp for ordering and expiry

#### Design Decisions & Benefits

##### 1. TaskGroup Concurrency Management

**Design Choice:**
- Uses `asyncio.TaskGroup` for coordinated lifecycle management
- AgentHarness manages both worker pool and Slack WebSocket handler
- Single entry point (`harness.run()`) creates all concurrent tasks

**Benefits:**
- **Unified Lifecycle:** All tasks start/stop together with proper error propagation
- **Exception Handling:** Any task failure cancels all related tasks gracefully
- **Resource Cleanup:** TaskGroup ensures proper cleanup on shutdown
- **Simplified Architecture:** Single orchestration point reduces complexity

##### 2. Hybrid Queue-Database Architecture

**Design Choice:**
- Events stored in PostgreSQL for persistence
- AsyncIO queue used for real-time worker signaling
- Workers poll database with timeout fallback

**Why This Approach:**
- **Immediate Processing:** Queue signals provide near-instant event processing (millisecond latency)
- **Guaranteed Delivery:** Database persistence ensures no events are lost on crashes/restarts
- **Failure Recovery:** Failed events remain in database for retry attempts
- **Audit Trail:** Complete event history maintained in `event_hist` table

**Alternative Rejected:** Pure queue-based systems lose events on restart; pure polling adds unnecessary latency.

##### 3. Worker Pool with Bounded Concurrency

**Configuration:** 5 concurrent workers (configurable via `main.py:112`)

**Benefits:**
- **Resource Control:** Prevents database connection exhaustion during event spikes
- **Predictable Performance:** Known maximum concurrency regardless of event volume
- **Graceful Degradation:** Backpressure naturally occurs when events arrive faster than processing capacity

**Implementation Detail:**
```python
# One signal per event, multiple workers compete for processing
await self._trigger.put(True)  # Signal workers
```

##### 4. Database-Level Concurrency Control

**PostgreSQL Features Used:**
- `FOR UPDATE SKIP LOCKED` - Prevents workers from competing for same event
- `ORDER BY random()` - Prevents head-of-line blocking on difficult events
- Transactions - Ensures atomic event state changes

**Retry Logic:**
- **Visibility Threshold (vt):** Events become invisible for 10 minutes after claiming
- **Attempt Counting:** Maximum 3 attempts before permanent failure
- **Automatic Cleanup:** Expired events moved to history table

##### 5. Resilience Features

#### Timeout-Based Polling
```python
await asyncio.wait_for(self._trigger.get(), timeout=(60.0 + jitter))
```

**Purpose:** Ensures workers continue operating even if queue signals are missed

**Benefits:**
- Processes events that may have been missed due to race conditions
- Runs cleanup operations (`delete_expired_events`) regularly
- Provides heartbeat mechanism for monitoring worker health

#### Worker Staggering
```python
await asyncio.sleep(random.randint(0, 30))  # Initial staggering
```

**Purpose:** Prevents thundering herd effects when multiple workers start simultaneously

#### Jitter in Timeouts
```python
jitter = random.randint(-15, 15)
timeout = 60.0 + jitter
```

**Purpose:** Distributes worker polling to reduce database load spikes

#### Event Lifecycle

##### 1. Event Reception
1. Slack sends app mention event
2. Event stored via `agent.insert_event()` 
3. `_on_event()` acknowledges immediately (prevents Slack timeout)
4. Queue signal sent: `await self._trigger.put(True)`

##### 2. Event Processing
1. Worker awakened by queue signal (or timeout)
2. `agent.claim_event()` atomically claims next available event
3. Event marked invisible (vt = now + 10min), attempts++
4. Business logic processes event
5. `agent.delete_event()` moves completed event to history

##### 3. Failure Handling
1. Processing failure leaves event in `agent.event` table
2. Event becomes visible again after 10-minute timeout
3. Different worker can retry (up to 3 attempts total)
4. Permanently failed events moved to history by cleanup process

#### Configuration

**Worker Count:** `main.py:112` - Currently set to 5 workers
**TaskGroup Integration:** `harness.py:159-177` - Uses TaskGroup for coordinated lifecycle management
**Retry Limits:** `agent.claim_event()` - 3 attempts max, 10-minute visibility timeout
**Cleanup:** `agent.delete_expired_events()` - 1-hour maximum age for stale events
**Polling:** 60-second timeout with ±15 second jitter

#### Benefits Summary

1. **Low Latency:** Near-instant processing via queue signals
2. **High Reliability:** Database persistence ensures no lost events
3. **Automatic Recovery:** Built-in retry logic handles transient failures  
4. **Bounded Resources:** Worker pool prevents resource exhaustion
5. **Operational Simplicity:** Self-healing system requires minimal intervention
6. **Full Observability:** Complete audit trail and monitoring integration
7. **Horizontal Scalability:** Additional workers can be added easily

This architecture provides enterprise-grade reliability while maintaining the responsiveness needed for real-time chat applications.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.



### Agent Architecture

**Core Pattern**: Orchestrator + Specialized Agents architecture where the main EON agent routes requests to specialized sub-agents.

```
EON Agent (Orchestrator) → Routes to:
├── Progress Agent (team/project tracking via GitHub/Linear/Slack)
├── Docs Agent (PostgreSQL/TimescaleDB technical documentation) 
└── Sales Agent (Salesforce customer support insights)
```

**Key Components**:
- **MCP Servers**: 6 external microservices (docs, github, linear, memory, salesforce, slack)
- **Agent System**: Specialized agents for different domains accessed via tool definitions
- **Event Processing**: Slack event handling with async task management
- **Database**: TimescaleDB with custom migration system

### MCP Server Architecture

All external capabilities are provided through MCP (Model Context Protocol) servers. Each server runs in its own container and can be selectively disabled via environment variables.

**Server Configuration Pattern**:
```python
# Singleton instances with conditional instantiation
def service_mcp_server() -> MCPServer | None:
    if os.environ.get("DISABLE_SERVICE_MCP_SERVER"):
        return None
    # ... create server instance
```

**Disable Any MCP Server**: Set `DISABLE_{SERVICE}_MCP_SERVER` to any value in `.env`

## Setup

### Interactive Setup

**Trigger Phrase**: "help me setup tiger agent"

When a user says this phrase, initiate an interactive setup process:

1. **Introduction**: Introduce yourself as "eon, a TigerData agent" and explain Tiger Agent's capabilities (team collaboration, technical documentation, customer support through specialized sub-agents)

2. **Service Discovery**: 
   - Parse `docker-compose.yml` to identify MCP services with `profiles` (these are optional)
   - Cross-reference with `start.sh` script logic to confirm optional services
   - Present service selection checkboxes to user

3. **Environment Configuration**:
   - Start with `.env.sample` as template
   - Collect required variables first: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_DOMAIN`, `ANTHROPIC_API_KEY`, `LOGFIRE_TOKEN`
   - Then collect optional service tokens based on user selections
   - Progressively write to `.env` file as tokens are validated

4. **Token Validation & Browser Integration**:
   - Auto-open browser to token creation pages: `https://api.slack.com/apps`, `https://console.anthropic.com/`, etc.
   - **Important for Slack**: When creating App-Level Token, must add `connections:write` scope for Socket Mode
   - Validate each token with API calls before accepting
   - **Required tokens**: Must be valid to continue
   - **Optional tokens**: On failure, offer retry or disable service option

5. **Resume Logic**: 
   - If `.env` exists, show current configuration summary
   - Step through each variable asking if existing value is correct
   - Allow modification of any existing values

6. **Completion**: 
   - Set appropriate `DISABLE_*` variables for unselected services
   - Execute `./start.sh` to start containers with correct profile selection


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