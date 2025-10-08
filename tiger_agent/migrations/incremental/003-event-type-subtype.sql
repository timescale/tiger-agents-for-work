--003-event-type-subtype.sql

-----------------------------------------------------------------------
-- Add type and subtype columns to agent.event
alter table agent.event
add column type text,
add column subtype text;

-----------------------------------------------------------------------
-- Add type and subtype columns to agent.event_hist
alter table agent.event_hist
add column type text,
add column subtype text;

-----------------------------------------------------------------------
-- Update existing rows to set type = 'app_mention'
update agent.event
set type = 'app_mention';

update agent.event_hist
set type = 'app_mention';

-----------------------------------------------------------------------
-- Make type column not nullable
alter table agent.event
alter column type set not null;

alter table agent.event_hist
alter column type set not null;
