# Observability

Tiger Agent uses [Pydantic Logfire](https://logfire.pydantic.dev/) for comprehensive observability, tracing, and monitoring. Logfire provides distributed tracing, structured logging, and performance metrics to help monitor and debug the agent's behavior.

## Configuration

### Environment Setup

Tiger Agent automatically configures Logfire observability when the `LOGFIRE_TOKEN` environment variable is present. If no token is provided, the system falls back to standard console logging.

```bash
# Required for Logfire integration
LOGFIRE_TOKEN=your_logfire_token_here

# Optional: Override service name (defaults to "tiger-agent")
SERVICE_NAME=my-custom-service-name
```

### Automatic Configuration

The logging setup in `tiger_agent/logging.py:10-63` handles all Logfire configuration:

- **Service identification**: Uses `SERVICE_NAME` environment variable or defaults to provided service name
- **Version tracking**: Automatically includes the Tiger Agent version in all traces
- **Graceful degradation**: Falls back to standard console logging when Logfire token is unavailable

### Instrumentation

Tiger Agent automatically instruments multiple libraries and systems:

#### Core Instrumentation
- **PostgreSQL (`logfire.instrument_psycopg()`)**: All database queries, transactions, and connection pool activity
- **Pydantic AI (`logfire.instrument_pydantic_ai()`)**: LLM interactions, token usage, and AI agent conversations
- **MCP Servers (`logfire.instrument_mcp()`)**: Model Context Protocol server communications and tool calls
- **HTTP Requests (`logfire.instrument_httpx()`)**: Outbound HTTP requests including Slack API calls

#### System Metrics
Tiger Agent collects comprehensive system performance metrics:
- **CPU usage**: User/system time and utilization per core
- **Memory usage**: Virtual and physical memory consumption
- **Thread count**: Active thread monitoring

## Instrumentation Patterns

### Function-Level Tracing

Tiger Agent uses `@logfire.instrument()` decorators extensively for automatic span creation:

```python
@logfire.instrument("function_name", extract_args=["arg1", "arg2"])
async def my_function(arg1: str, arg2: int, sensitive_data: str):
    # Function execution automatically wrapped in a span
    # Only arg1 and arg2 are included in trace data
    pass
```

**Key instrumented functions:**
- **Event Management** (`tiger_agent/harness.py`): `insert_event`, `claim_event`, `delete_event`, `process_events`
- **Agent Operations** (`tiger_agent/agent.py`): `generate_response`, `make_system_prompt`, `make_user_prompt`
- **Slack Integration** (`tiger_agent/slack.py`): `add_reaction`, `post_response`, `fetch_user_info`
- **Database Migrations** (`tiger_agent/migrations/runner.py`): `migrate_db`, `run_incremental`, `run_idempotent`

### Context-Aware Spans

For more complex operations, Tiger Agent uses manual span creation with contextual information:

```python
# Event processing with event ID context
with logfire.span("process_event", event_id=event.id):
    await self._event_processor(self._make_harness_context(), event)

# Worker activity with worker ID and trigger reason
with logfire.span("worker_run", worker_id=worker_id, reason=reason):
    await self._process_events()
```

### Migration Script Tracing

Database migration scripts are individually traced with script names:

```python
# Incremental migrations
with logfire.span("incremental_sql", script=path.name):
    await cur.execute(migration_sql)

# Idempotent migrations
with logfire.span("idempotent_sql", script=path.name):
    await cur.execute(sql)
```

## Key Traces and Metrics

### Event Processing Lifecycle

Tiger Agent creates comprehensive traces for the complete event processing lifecycle:

1. **Event Ingestion** (`insert_event`): When Slack events are received and stored
2. **Event Claiming** (`claim_event`): When workers claim events for processing
3. **Event Processing** (`process_event`): The complete processing workflow including AI response generation
4. **Event Completion** (`delete_event`): When events are successfully processed and archived

### Worker Activity Monitoring

Worker behavior is fully traced with context about activity patterns:

- **Trigger-based execution**: When workers are triggered by new events (`reason="triggered"`)
- **Timeout-based execution**: When workers run on schedule (`reason="timeout"`)
- **Worker identification**: Each worker has a unique `worker_id` for tracking individual worker performance

### Database Operations

All database operations are automatically instrumented:

- **Query execution time and parameters**
- **Transaction boundaries and rollbacks**
- **Connection pool usage and health**
- **Migration script execution and timing**

### AI Agent Operations

AI interactions are fully traced including:

- **Prompt generation**: System and user prompt creation with template rendering
- **LLM API calls**: Token usage, response times, and model parameters
- **MCP tool calls**: External tool usage and responses
- **Error handling**: Failed generations and retry attempts

### Slack API Integration

All Slack operations are traced:

- **Message posting**: Response delivery with channel and thread context
- **Reaction management**: Adding/removing status reactions (>, , L)
- **User information fetching**: Profile and timezone data retrieval
- **Bot information**: Authentication and bot metadata

## Error Tracking and Debugging

### Exception Handling

Tiger Agent includes structured exception logging with trace correlation:

```python
logger.exception(
    "event processing failed",
    extra={"event_id": event.id},
    exc_info=e
)
```

### Retry Logic Visibility

The system provides visibility into retry attempts:

- **Event retry counts**: Tracked in database and included in traces
- **Worker retry behavior**: When events fail processing but remain available for retry
- **Maximum attempt limits**: When events exceed retry limits and are moved to history

## Integration with Logfire UI

When configured with a valid `LOGFIRE_TOKEN`, all traces, logs, and metrics are automatically sent to the Logfire platform where you can:

- **View distributed traces**: See complete request flows across components
- **Monitor performance**: Track response times and throughput
- **Debug errors**: Examine exception stack traces with full context
- **Analyze usage patterns**: Understand worker behavior and event processing patterns
- **Track resource usage**: Monitor CPU, memory, and database performance

## Development and Debugging

### Local Development

For local development without Logfire:
- Omit `LOGFIRE_TOKEN` from environment
- All instrumentation gracefully degrades to standard Python logging
- Console output includes structured log messages with event context

### Custom Instrumentation

To add custom instrumentation in your event processors:

```python
import logfire

async def my_event_processor(ctx: HarnessContext, event: Event):
    with logfire.span("custom_operation", custom_field="value"):
        # Your custom logic here
        logfire.info("Custom operation completed", extra={"result": "success"})
```

The automatic instrumentation will capture this custom span as part of the overall event processing trace.