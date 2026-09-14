--004-scheduled-events.sql

-----------------------------------------------------------------------
-- agent.delete_events_matching
-- removes every queued event whose payload contains _filter and archives it as
-- unprocessed. returns how many were removed.
create or replace function agent.delete_events_matching(_filter jsonb) returns int8
as $func$
    with d as
    (
        delete from agent.event
        where event @> _filter
        returning *
    )
    , h as
    (
        insert into agent.event_hist
        ( id
        , event_ts
        , attempts
        , vt
        , claimed
        , event
        , processed
        )
        select
          d.id
        , d.event_ts
        , d.attempts
        , d.vt
        , d.claimed
        , d.event
        , false
        from d
        returning id
    )
    select count(*) from h
    ;
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.insert_event_if_absent
-- enqueues _event visible at _vt unless an unclaimed future event already contains
-- _match. the event being handled right now also has a future vt (claim_event
-- pushed it out) and attempts > 0, so it is excluded by both tests.
create or replace function agent.insert_event_if_absent
( _event jsonb
, _vt timestamptz
, _match jsonb
, _exclude_id int8 default null
) returns boolean
as $func$
    with i as
    (
        insert into agent.event
        ( event_ts
        , event
        , vt
        )
        select now(), _event, _vt
        where not exists
        (
            select 1
            from agent.event e
            where e.event @> _match
            and e.attempts = 0
            and e.vt > now()
            and e.id is distinct from _exclude_id
        )
        returning id
    )
    select exists (select 1 from i)
    ;
$func$ language sql volatile security invoker
;
