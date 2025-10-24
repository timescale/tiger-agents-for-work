--003-interaction.sql

-----------------------------------------------------------------------
-- Rename agent.event table to agent.interaction and update columns

-- Rename the main table
alter table agent.event rename to interaction;

-- Rename columns in the interaction table
alter table agent.interaction rename column event_ts to interaction_ts;
alter table agent.interaction rename column event to interaction;

-- Add type column with default, backfill, then add NOT NULL constraint
alter table agent.interaction add column type text;
update agent.interaction set type = 'event';
alter table agent.interaction alter column type set not null;

-- Drop old index and create new one with updated column name
drop index agent.event_vt_attempts_idx;
create index on agent.interaction (vt, attempts);

-----------------------------------------------------------------------
-- Rename agent.event_hist table to agent.interaction_hist and update columns

-- Rename the history table
alter table agent.event_hist rename to interaction_hist;

-- Rename columns in the interaction_hist table
alter table agent.interaction_hist rename column event_ts to interaction_ts;
alter table agent.interaction_hist rename column event to interaction;

-- Add type column with default (no NOT NULL needed for history table)
alter table agent.interaction_hist add column type text;
update agent.interaction_hist set type = 'event';
alter table agent.interaction_hist alter column type set not null;
