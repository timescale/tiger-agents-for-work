# Database

Tiger Agent requires a PostgreSQL database with the TimescaleDB extension.

Tiger Agent creates and uses an `agent` schema.

The `agent.event` table stores all the Slack events (currently only app_mention events) that have not yet been processed.

**agent.event**
- **id** an integer surrogate key
- **event_ts** the event's timestamp (according to Slack)
- **attempts** a count of the number of times we have attempted to process the event
- **vt** visibility threshold -- the time at which the event is "visible" for processing
- **claimed** an array of timestamps corresponding to each time the event was claimed for processing
- **event** a jsonb containing the actual event payload

This table is used as a durable work queue for coordinating multiple workers.

When an event is received from Slack, it is inserted into this table. `attempts` will be zero, `vt` will be `now()`, and `claimed` will be empty.

Workers attempt to claim an `agent.event` row using the `agent.claim_event()` database function.
They look for an unlocked, visible row where `attempts` is less than the max attempts any one event is allowed.
`LIMIT 1 FOR UPDATE SKIP LOCKED` is used to efficiently find and lock a row if one is available.
Then, the `vt` is transactionally updated to a future time (10 minutes), `now()` is appended to `claimed`, and the row is returned.

In this manner, the worker has atomically claimed an event and made it invisible for a period of time while it works.
If the worker dies while working it, the event will automatically become available for new attempts when `vt` passes.

Once a worker successfully processes an event, it calls the `agent.delete_event()` database function.
This function "moves" the event to a history table by deleting the row from the `agent.event` table and inserting it into `agent.event_hist`.

Workers also periodically sweep the `agent.event` table for any event that have been attempted too many times or are too old.
See the `agent.delete_expired_events()` database function.
These events are similarly "moved" to the `agent.event_hist` table.

The `agent.event_hist` table has the same schema as the `agent.event` table and is a TimescaleDB hypertable partitioned on the `event_ts`.
This historical table allows for post-analysis. It also makes it easy to "move" events back into `agent.event` for reprocessing if necessary.

## Database Functions

Tiger Agent provides several database functions for managing events and handling Slack timestamps:

### Event Management Functions

**agent.insert_event(_event jsonb)**
- Inserts a Slack event into the `agent.event` table
- Automatically converts Slack's numeric timestamp to PostgreSQL timestamptz
- Parameters:
  - `_event`: The complete Slack event payload as JSONB

**agent.claim_event(_max_attempts int4 = 3, _invisible_for interval = '10m')**
- Atomically claims an event for processing by a worker
- Uses `ORDER BY random()` to randomly select events for load balancing across workers
- Utilizes `FOR UPDATE SKIP LOCKED` for efficient concurrent access
- Parameters:
  - `_max_attempts`: Maximum retry attempts before giving up (default: 3)
  - `_invisible_for`: How long to make the event invisible while processing (default: 10 minutes)
- Returns: The claimed event row, or nothing if no events are available

**agent.delete_event(_id int8)**
- Marks an event as successfully processed by moving it to `agent.event_hist`
- Atomically deletes from `agent.event` and inserts into `agent.event_hist`
- Parameters:
  - `_id`: The event ID to mark as completed

**agent.delete_expired_events(_max_attempts int = 3, _max_vt_age interval = '1h')**
- Cleans up events that have exceeded retry limits or are stuck
- Moves expired events to `agent.event_hist` table
- Parameters:
  - `_max_attempts`: Events with this many attempts or more are expired (default: 3)
  - `_max_vt_age`: Events invisible for longer than this are expired (default: 1 hour)

### Timestamp Conversion Functions

**agent.to_timestamptz(_ts numeric) / agent.to_timestamptz(_ts text)**
- Converts Slack's Unix timestamp format to PostgreSQL timestamptz
- Handles both numeric and text input formats
- Used internally by `agent.insert_event()` for timestamp conversion

**agent.from_timestamptz(_ts timestamptz)**
- Converts PostgreSQL timestamptz back to Unix timestamp numeric format
- Useful for API responses that need Slack-compatible timestamps

## Migrations

Tiger Agent manages its own database migrations.

The `agent.version` table contains a single row. The library's version (`__version__`) is compared to the version in this table.

* If the library's version is older than the database's, the library exits with an error.
* If the library's version matches the database's, no migrations are required and the library continues.
* If the library's version is newer than the database's, the library applies the database migrations to bring the database up to the current version.

Migration scripts are either **idempotent** or **incremental**.

* Idempotent scripts are run on EVERY migration. These contain DDL that is safe to rerun. e.g. `CREATE OR REPLACE FUNCTION`
* Incremental scripts are guaranteed to run exactly ONCE. These contain DDL like `CREATE TABLE` or `CREATE INDEX`

The `agent.migration` table records all the incremental scripts that have been applied to the database.

Incremental scripts go in the [/tiger_agent/migrations/incremental](/tiger_agent/migrations/incremental) directory.
Idempotent scripts go in the [/tiger_agent/migrations/idempotent](/tiger_agent/migrations/idempotent) directory.
Scripts must have a three-digit prefix such that all scripts are strictly ordered with no gaps. Scripts are executed in this order.

The [/tiger_agent/migrations/runner.py](/tiger_agent/migrations/runner.py) module handles database migrations.

All migration scripts are executed in a single-transaction. The entire migration either succeeds or fails. 
The database cannot be left in some middle-ground state halfway between two versions.

Before attempting a migration, the library uses a dual-locking approach for maximum safety:

1. **Advisory Lock**: Attempts to acquire an exclusive transaction-level advisory lock (key: 31321898691465844) with retry logic (up to 10 attempts with 10-second delays)
2. **Table Lock**: Places an exclusive lock on the `agent.migration` table within the transaction

This dual-locking approach ensures that multiple instances of the library cannot apply migrations simultaneously, even in edge cases where advisory locks might not be sufficient.

### Migration Security

The migration system includes a **schema ownership check** for security. Only the user who owns the `agent` schema can run database migrations. If the schema exists but is owned by a different user, the migration will abort with an error. This prevents unauthorized users from modifying the database schema.

