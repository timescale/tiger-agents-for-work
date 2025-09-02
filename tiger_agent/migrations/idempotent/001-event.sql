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

-----------------------------------------------------------------------
-- slack.insert_bot_event
create or replace function slack.insert_bot_event(_event jsonb) returns void
as $func$
    insert into slack.bot_event
    ( event_ts
    , event
    )
    select
      slack.to_timestamptz((_event->>'event_ts')::numeric)
    , _event
    ;
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- slack.delete_bot_event
create or replace function slack.delete_bot_event(_id int8) returns void
as $func$
    with d as
    (
        delete from slack.bot_event
        where id = _id
        returning *
    )
    insert into slack.bot_event_hist
    ( id
    , event_ts
    , attempts
    , vt
    , event
    )
    select
      d.id
    , d.event_ts
    , d.attempts
    , d.vt
    , d.event
    from d
    ;
$func$ language sql volatile security invoker
;
