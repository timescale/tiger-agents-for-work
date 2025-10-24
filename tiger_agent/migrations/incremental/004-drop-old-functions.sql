--004-drop-old-functions.sql

-----------------------------------------------------------------------
-- Drop old function names after renaming to interaction

drop function if exists agent.insert_event(jsonb);
drop function if exists agent.claim_event(int4, interval);
drop function if exists agent.delete_event(int8, boolean);
drop function if exists agent.delete_expired_events(int, interval);
