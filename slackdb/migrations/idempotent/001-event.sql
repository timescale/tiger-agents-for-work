--001-event.sql

-----------------------------------------------------------------------
-- slack.insert_event
create or replace function slack.insert_event(_event jsonb, _error jsonb)
returns slack.event
as $func$
    insert into slack.event
    ( event_ts
    , type
    , subtype
    , event
    , error
    )
    values
    ( slack.to_timestamptz(_event->>'event_ts')
    , _event->>'type'
    , _event->>'subtype'
    , _event
    , _error
    )
    returning *
    ;
$func$ language sql volatile security invoker
;
