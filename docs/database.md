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

Before attempting a migration, the library attempts to acquire an exclusive transaction-level advisory lock.
If successful, an exclusive lock is placed on the `agent.migration` table.
In this way, multiple instances of the library cannot apply migrations simultaneously.

