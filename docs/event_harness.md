# Event Harness Architecture

The EventHarness is the core event processing engine of Tiger Agent, providing a robust, scalable, and responsive system for handling Slack app_mention events. It combines the durability of PostgreSQL-backed queuing with the responsiveness of asyncio-based worker coordination.

## Overview

The EventHarness orchestrates a sophisticated event processing pipeline that receives Slack events, stores them durably in PostgreSQL, and coordinates multiple workers to process events efficiently. It's designed to handle high volumes of concurrent events while maintaining strong reliability guarantees.

## Key Features & Benefits

### 🎯 **Immediate Responsiveness**
Events are processed immediately upon arrival rather than waiting for periodic polling cycles. When a Slack mention occurs, processing begins within milliseconds.

### ⚡ **Bounded Concurrency**
Fixed worker pool prevents resource exhaustion and provides predictable performance characteristics. No matter how many events arrive, the system maintains controlled resource usage.

### 🔒 **Atomic Event Processing**
Database-level event claiming ensures exactly-once processing with no duplicates, even under high concurrency and failure conditions.

### 🔄 **Resilient Retry Logic**
Failed events are automatically retried with visibility thresholds. Stuck or expired events are cleaned up automatically.

### 📈 **Horizontal Scalability**
Multiple harness instances can run simultaneously, with PostgreSQL coordinating work distribution across all instances.

### 🔍 **Full Observability**
Complete instrumentation with Logfire provides detailed tracing of event flow, worker activity, and database operations.

## Architecture Components

### Core Components

#### **HarnessContext**
Shared context object providing event processors with:
- **Slack AsyncApp**: For making Slack API calls
- **Database Pool**: For data operations and persistence
- **TaskGroup**: For spawning concurrent operations

#### **Event Models**
- **AppMentionEvent**: Pydantic model for Slack event structure
- **Event**: Database representation with processing metadata

#### **Worker Coordination**
- **Multiple Workers**: Configurable pool of concurrent processors
- **Event Claiming**: Atomic database-level work distribution
- **Load Balancing**: Random event selection spreads work evenly

## Implementation Mechanisms

### 1. Immediate Event Handling ("Poke" Mechanism)

When Slack events arrive:

```python
async def _on_event(self, ack: AsyncAck, event: dict[str, Any]):
    await self._insert_event(event)      # Store durably
    await ack()                         # Acknowledge to Slack
    await self._trigger.put(True)       # Wake exactly one worker
```

**Key Behavior**: The asyncio.Queue trigger wakes exactly **one worker**, not all workers. This prevents thundering herd effects while ensuring immediate processing.

### 2. Atomic Event Claiming

Workers compete for events using PostgreSQL's atomic operations:

```sql
-- agent.claim_event() function provides:
-- - Random selection to avoid head-of-line blocking
-- - FOR UPDATE SKIP LOCKED for efficient concurrency
-- - Visibility threshold updates for retry logic
SELECT * FROM agent.claim_event(max_attempts, invisibility_interval);
```

**Guarantees**:
- Only one worker can claim each event at a time
- Failed claims don't block other workers
- Automatic retry scheduling via visibility thresholds

### 3. Resilient Worker Architecture

Each worker operates in a hybrid trigger/polling model:

```python
while True:
    try:
        # Wait for immediate trigger OR timeout for polling
        await asyncio.wait_for(
            self._trigger.get(),
            timeout=self._calc_worker_sleep()
        )
        await worker_run("triggered")  # Immediate processing
    except TimeoutError:
        await worker_run("timeout")    # Periodic cleanup
```

**Benefits**:
- **Immediate**: Most events processed within milliseconds
- **Resilient**: Periodic polling catches missed/failed events
- **Efficient**: Jittered timeouts prevent worker synchronization

### 4. Batch Event Processing

Triggered workers process events in batches for efficiency:

```python
async def _process_events(self):
    for _ in range(20):  # Process up to 20 events per trigger
        event = await self._claim_event()
        if not event:
            return  # No more work available
        if not await self._process_event(event):
            return  # Failed processing, stop and retry later
```

**Advantages**:
- **Efficient**: Single trigger processes multiple events
- **Controlled**: Bounded batch size prevents runaway processing
- **Fail-Fast**: Early termination on failures preserves retry opportunities

### 5. Database-Backed Durability

The system uses PostgreSQL's `agent.event` table as a durable work queue:

- **Insert**: New events stored with `attempts=0`, `vt=now()`
- **Claim**: Workers atomically claim events with future visibility threshold
- **Success**: Completed events moved to `agent.event_hist`
- **Failure**: Events remain visible for retry after threshold expires
- **Cleanup**: Expired events automatically moved to history

### 6. Worker Coordination & Load Balancing

#### Staggered Startup
Workers start at different times to distribute initial load:

```python
initial_sleeps = [0] + random.sample(range(1, worker_sleep_seconds), num_workers-1)
```

#### Jittered Polling
Random sleep intervals prevent thundering herd effects:

```python
def _calc_worker_sleep(self) -> int:
    jitter = random.randint(min_jitter, max_jitter)
    return base_sleep + jitter
```

#### Random Event Selection
Database function uses `ORDER BY random()` to prevent head-of-line blocking.

## Operational Characteristics

### Performance Profile
- **Latency**: Sub-millisecond event processing initiation
- **Throughput**: Scales linearly with worker count
- **Resource Usage**: Bounded by worker pool size
- **Database Load**: Efficient with connection pooling and prepared statements

### Failure Modes & Recovery
- **Worker Death**: Events auto-retry after visibility threshold
- **Database Unavailable**: Events queued in Slack until reconnection
- **Processing Failures**: Automatic retry with visibility threshold
- **Poisoned/Expired Events**: Moved to history table after max attempts or max age

### Configuration Parameters
- **num_workers**: Concurrency level (default: 5)
- **max_attempts**: Retry limit per event (default: 3)
- **max_age_minutes**: Maximum age of an event before expiring (default: 60)
- **invisibility_minutes**: Claim duration (default: 10)
- **worker_sleep_seconds**: Polling interval (default: 60)
- **worker_min/max_jitter_seconds**: Adds random jitter to worker sleep

## Monitoring & Observability

All operations are instrumented with Logfire spans providing:

- **Event Flow Tracking**: From ingestion through completion
- **Worker Activity**: Trigger vs timeout reasoning
- **Database Performance**: Query timing and connection usage
- **Failure Analysis**: Exception details and retry patterns
- **Load Distribution**: Worker utilization and event claiming patterns

The EventHarness represents a sophisticated balance of immediate responsiveness, operational resilience, and resource efficiency - providing Tiger Agent with enterprise-grade event processing capabilities.