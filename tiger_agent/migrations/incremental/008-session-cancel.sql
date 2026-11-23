--008-session-cancel.sql

-----------------------------------------------------------------------
-- agent.session_cancel
-- Persistent cancellation cutoffs for agent sessions. When a user clicks
-- "Stop", the listener writes a row keyed by (channel_id, thread_ts, user_id).
-- Workers check this table at claim time and poll it during long runs so a
-- stop delivered to one replica still reaches the worker holding the task.
create table if not exists agent.session_cancel
( channel_id text not null
, thread_ts text not null
, user_id text not null
, stopped_at timestamptz not null default now()
, primary key (channel_id, thread_ts, user_id)
);
