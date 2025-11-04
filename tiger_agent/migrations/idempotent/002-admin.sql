--002-admin.sql

-----------------------------------------------------------------------
-- function to extract user ID from Slack event in format <@U06S8H0V94P|nathan>
create or replace function agent.extract_user_id_from_event(_event jsonb) returns text
as $func$
    select (regexp_match(_event->>'text', '<@([A-Z0-9]+)\|[^>]+>'))[1]
$func$ language sql immutable security invoker
;

-----------------------------------------------------------------------
-- slack.insert_admin_user
create or replace function agent.insert_admin_user(_event jsonb) returns void
as $func$
    insert into agent.admin_users (user_id, event)
    select agent.extract_user_id_from_event(_event), _event
    where agent.extract_user_id_from_event(_event) is not null
    on conflict (user_id) do nothing;

    insert into agent.admin_audits (event) values (_event);
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.delete_admin_user
create or replace function agent.delete_admin_user(_event jsonb) returns void
as $func$
    delete from agent.admin_users
    where user_id = agent.extract_user_id_from_event(_event);

    insert into agent.admin_audits (event) values (_event);
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.insert_ignored_user
create or replace function agent.insert_ignored_user(_event jsonb) returns void
as $func$
    insert into agent.ignored_users (user_id, event)
    select agent.extract_user_id_from_event(_event), _event
    where agent.extract_user_id_from_event(_event) is not null
    on conflict (user_id) do nothing;

    insert into agent.admin_audits (event) values (_event);
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.delete_ignored_user
create or replace function agent.delete_ignored_user(_event jsonb) returns void
as $func$
    delete from agent.ignored_users
    where user_id = agent.extract_user_id_from_event(_event);

    insert into agent.admin_audits (event) values (_event);
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.is_user_ignored
create or replace function agent.is_user_ignored(_user_id text) returns boolean
as $func$
    select exists (
        select 1 from agent.ignored_users
        where user_id = _user_id
    )
$func$ language sql stable security invoker
;