--002-event-processed.sql

-----------------------------------------------------------------------
-- Add processed column to agent.event_hist
alter table agent.event_hist
add column processed boolean not null default true
;

drop function if exists agent.delete_event(int8);
