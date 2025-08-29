set local search_path = pg_catalog, pg_temp;

-- migration infrastructure
do $block$
declare
    _owner oid;
    _user oid;
begin
    select pg_catalog.to_regrole(current_user)::oid
    into strict _user
    ;

    select n.nspowner into _owner
    from pg_catalog.pg_namespace n
    where n.nspname = 'slack'
    ;

    if _owner is null then
        -- slack schema
        create schema slack;

        -- slack.version
        create table slack.version
        ( version text not null check (regexp_like(version, '((0|[1-9]+)[0-9]*)\.((0|[1-9]+)[0-9]*)\.((0|[1-9]+)[0-9]*)(-[a-z-]+[0-9]*){0,1}'))
        , at timestamptz not null default now()
        );
        create unique index on slack.version ((true)); -- ensure only one row in the table ever
        insert into slack.version (version) values ('0.0.0');

        -- slack.migration
        create table if not exists slack.migration
        ( file_name text not null primary key
        , applied_at_version text not null
        , applied_at timestamptz not null default pg_catalog.clock_timestamp()
        , body text not null
        );

    elsif _owner is distinct from _user then
        -- if the schema exists but is owned by someone other than the user running this, abort
        raise exception 'only the owner of the slackdb schema can run database migrations';
        return;
    end if
    ;
end
$block$
;

--make sure there is only one installation happening at a time
lock table slack.migration;
