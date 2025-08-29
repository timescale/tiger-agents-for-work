--000-timestamptz.sql

-----------------------------------------------------------------------
-- slack.to_timestamptz
create or replace function slack.to_timestamptz(_ts numeric) returns timestamptz
language sql immutable security invoker 
return (_ts * interval '1s' + 'epoch'::timestamptz)
;

-----------------------------------------------------------------------
-- slack.to_timestamptz
create or replace function slack.to_timestamptz(_ts text) returns timestamptz
language sql immutable security invoker 
return slack.to_timestamptz(_ts::numeric)
;

-----------------------------------------------------------------------
-- slack.from_timestamptz
create or replace function slack.from_timestamptz(_ts timestamptz) returns numeric
language sql immutable security invoker 
return extract('epoch' from _ts)::numeric
;
