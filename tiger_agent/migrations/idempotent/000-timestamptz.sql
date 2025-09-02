--000-timestamptz.sql

-----------------------------------------------------------------------
-- agent.to_timestamptz
create or replace function agent.to_timestamptz(_ts numeric) returns timestamptz
language sql immutable security invoker 
return (_ts * interval '1s' + 'epoch'::timestamptz)
;

-----------------------------------------------------------------------
-- agent.to_timestamptz
create or replace function agent.to_timestamptz(_ts text) returns timestamptz
language sql immutable security invoker 
return agent.to_timestamptz(_ts::numeric)
;

-----------------------------------------------------------------------
-- agent.from_timestamptz
create or replace function agent.from_timestamptz(_ts timestamptz) returns numeric
language sql immutable security invoker 
return extract('epoch' from _ts)::numeric
;
