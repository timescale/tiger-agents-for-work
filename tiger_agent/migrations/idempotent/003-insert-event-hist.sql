--003-insert-event-hist.sql

-----------------------------------------------------------------------
-- agent.insert_event_hist
create or replace function agent.insert_event_hist(_event jsonb) returns int8
as $func$
    insert into agent.event_hist
    ( id
    , event_ts
    , attempts
    , vt
    , claimed
    , event
    , processed
    )
    values
    ( nextval('agent.event_id_seq')
    , agent.to_timestamptz((_event->>'event_ts')::numeric)
    , 0
    , now()
    , array[]::timestamptz[]
    , _event
    , true
    )
    returning id
    ;
$func$ language sql volatile security invoker
;