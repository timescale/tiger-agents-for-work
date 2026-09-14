-- 007-user-defined-rules-scheduling.sql
-- a rule can now run on a schedule instead of matching incoming events.
-- scheduled rules carry event_type = 'schedule', which no real event has, so the
-- judge in evaluate_user_defined_rules never selects them; their runs are enqueued
-- directly with a future vt. criteria is optional because a schedule has none.
alter table agent.user_defined_rules
  alter column criteria drop not null
, add column repeat            boolean not null default false
, add column period            interval
, add column channel           text
, add column execution_profile text not null default 'full'
    check (execution_profile in ('full', 'limited'))
, add column updated_at        timestamptz not null default now()
;
