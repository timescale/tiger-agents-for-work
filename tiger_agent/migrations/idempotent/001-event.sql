--001-event.sql

-----------------------------------------------------------------------
-- agent.insert_interaction
create or replace function agent.insert_interaction
(_type text
, _interaction jsonb
) returns void
as $func$
    insert into agent.interaction
    ( interaction_ts
    , type
    , interaction
    )
    select
      agent.to_timestamptz((_interaction->>'interaction_ts')::numeric)
    , _type
    , _interaction
    ;
$func$ language sql volatile security invoker
;


-----------------------------------------------------------------------
-- agent.claim_interaction
create or replace function agent.claim_interaction
( _max_attempts int4 default 3
, _invisible_for interval default interval '10m'
) returns setof agent.interaction
as $func$
    with x as
    (
        select i.id
        from agent.interaction i
        where i.vt <= now() -- must be visible
        and i.attempts < _max_attempts -- must not have exceeded attempts
        order by random() -- shuffle the deck
        limit 1
        for update
        skip locked
    )
    , u as
    (
        update agent.interaction u set
          vt = clock_timestamp() + _invisible_for -- invisible for a bit while we work it
        , attempts = u.attempts + 1
        , claimed = claimed || now()
        from x
        where u.id = x.id
        returning u.*
    )
    select *
    from u
$func$ language sql volatile security invoker
;


-----------------------------------------------------------------------
-- agent.delete_interaction
create or replace function agent.delete_interaction(_id int8, _processed boolean default true) returns void
as $func$
    with d as
    (
        delete from agent.interaction
        where id = _id
        returning *
    )
    insert into agent.interaction_hist
    ( id
    , interaction_ts
    , attempts
    , vt
    , claimed
    , interaction
    , processed
    , type
    )
    select
      d.id
    , d.interaction_ts
    , d.attempts
    , d.vt
    , d.claimed
    , d.interaction
    , _processed
    , d.type
    from d
    ;
$func$ language sql volatile security invoker
;


-----------------------------------------------------------------------
-- agent.delete_expired_interactions
create or replace function agent.delete_expired_interactions
( _max_attempts int default 3
, _max_vt_age interval default interval '1h'
) returns void
as $func$
    with d as
    (
        delete from agent.interaction i
        where i.attempts >= _max_attempts
        or i.vt <= (now() - _max_vt_age)
        returning *
    )
    insert into agent.interaction_hist
    ( id
    , interaction_ts
    , attempts
    , vt
    , claimed
    , interaction
    , type
    )
    select
      d.id
    , d.interaction_ts
    , d.attempts
    , d.vt
    , d.claimed
    , d.interaction
    , d.type
    from d
    ;
$func$ language sql volatile security invoker
;
