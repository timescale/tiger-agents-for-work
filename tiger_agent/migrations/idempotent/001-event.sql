--001-event.sql

-----------------------------------------------------------------------
-- agent.insert_event
create or replace function agent.insert_event(_event jsonb) returns void
as $func$
    insert into agent.event
    ( event_ts
    , event
    )
    select
      agent.to_timestamptz((_event->>'event_ts')::numeric)
    , _event
    ;
$func$ language sql volatile security invoker
;


-----------------------------------------------------------------------
-- agent.claim_event
create or replace function agent.claim_event
( _max_attempts int4 default 3
, _invisible_for interval default interval '10m'
) returns setof agent.event
as $func$
    with x as
    (
        select e.id
        from agent.event e
        where e.vt <= now() -- must be visible
        and e.attempts < _max_attempts -- must not have exceeded attempts
        order by random() -- shuffle the deck
        limit 1
        for update
        skip locked
    )
    , u as
    (
        update agent.event u set
          vt = clock_timestamp() + _invisible_for -- invisible for a bit while we work it
        , attempts = u.attempts + 1
        from x
        where u.id = x.id
        returning u.*
    )
    select *
    from u
$func$ language sql volatile security invoker
;


-----------------------------------------------------------------------
-- agent.delete_event
create or replace function agent.delete_event(_id int8) returns void
as $func$
    with d as
    (
        delete from agent.event
        where id = _id
        returning *
    )
    insert into agent.event_hist
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


-----------------------------------------------------------------------
-- agent.delete_expired_events
create or replace function agent.delete_expired_events
( _max_attempts int default 3
, _max_vt_age interval default interval '1h'
) returns void
as $func$
    with d as
    (
        delete from agent.event e
        where e.attempts >= _max_attempts
        or e.vt <= (now() - _max_vt_age)
        returning *
    )
    insert into agent.event_hist
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


-----------------------------------------------------------------------
-- agent.event_error_count
create or replace function agent.event_error_count
( _bgn timestamptz default (now() - interval '10m')
, _end timestamptz default now()
) returns int8
as $func$
    -- if attempts is > 1, then it encountered attempts - 1 errors
    select sum(x.num_errors)
    from
    (
        select coalesce(sum(e.attempts - 1), 0) as num_errors
        from agent.event e
        where _bgn <= e.event_ts
        and e.event_ts <= _end
        and e.attempts > 1
        union all
        select coalesce(sum(h.attempts - 1), 0) as num_errors
        from agent.event_hist h
        where _bgn <= e.event_ts
        and e.event_ts <= _end
        and h.attempts > 1
    ) x
    ;
$func$ language sql stable security invoker
;