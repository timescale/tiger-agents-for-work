--003-admin.sql

-----------------------------------------------------------------------
-- agent.admin_users
create table if not exists agent.admin_users
( user_id text primary key
, event jsonb
);


-----------------------------------------------------------------------
-- agent.ignored_users
create table if not exists agent.ignored_users
( user_id text primary key
, event jsonb
);

-----------------------------------------------------------------------
-- agent.admin_audit
create table if not exists agent.admin_audits
( event jsonb
);
