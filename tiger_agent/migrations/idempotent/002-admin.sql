--002-admin.sql

-----------------------------------------------------------------------
-- function to extract user ID from Slack event in format <@U06S8H0V94P|nathan>
create or replace function agent.extract_user_id_from_event(_event jsonb) returns text
as $func$
    select (regexp_match(_event->>'text', '<@([A-Z0-9]+)\|[^>]+>'))[1]
$func$ language sql immutable security invoker
;

-----------------------------------------------------------------------
-- agent.admin_users
create table if not exists agent.admin_users
( event_ts timestamptz not null default now()
, user_id text primary key
, event jsonb
);

-----------------------------------------------------------------------
-- slack.insert_admin_user
create or replace function agent.insert_admin_user(_event jsonb) returns void
as $func$
    insert into agent.admin_users (user_id, event)
    select agent.extract_user_id_from_event(_event), _event
    where agent.extract_user_id_from_event(_event) is not null
    on conflict (user_id) do nothing
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.ignored_users
create table if not exists agent.ignored_users
( id serial primary key
, event_ts timestamptz not null default now()
, user_id text not null
, deleted timestamptz null
, event jsonb
, unique (user_id, deleted)
);

-- Create partial unique constraint to ensure only one active ignore per user
alter table agent.ignored_users
add constraint ignored_users_active_user_constraint
exclude (user_id with =) where (deleted is null);

-----------------------------------------------------------------------
-- agent.insert_ignored_user
create or replace function agent.insert_ignored_user(_event jsonb) returns void
as $func$
    insert into agent.ignored_users (user_id, deleted, event)
    select agent.extract_user_id_from_event(_event), null, _event
    where agent.extract_user_id_from_event(_event) is not null
    on conflict on constraint ignored_users_active_user_constraint do nothing
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.delete_ignored_user
create or replace function agent.delete_ignored_user(_event jsonb) returns void
as $func$
    update agent.ignored_users
    set deleted = now()
    where user_id = agent.extract_user_id_from_event(_event)
      and deleted is null
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.is_user_ignored
create or replace function agent.is_user_ignored(_user_id text) returns boolean
as $func$
    select exists (
        select 1 from agent.ignored_users
        where user_id = _user_id
          and deleted is null
    )
$func$ language sql immutable security invoker
;

-----------------------------------------------------------------------
-- agent.ignored_user_list
create or replace function agent.ignored_user_list() returns setof agent.ignored_users
as $func$
    select * from agent.ignored_users
    where deleted is null
    order by event_ts desc
$func$ language sql immutable security invoker
;