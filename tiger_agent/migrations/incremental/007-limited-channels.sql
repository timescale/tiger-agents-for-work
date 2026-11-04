-- 007-limited-channels.sql
-- channels where the agent is restricted to "public" (non-internal) mcp servers
-- and tools. a row is kept after the limit is lifted so the history of the
-- change is retained; `enabled` is the live value.
create table agent.limited_channels
( channel_id text primary key
, enabled    boolean not null default true
, updated_at timestamptz not null default now()
);
