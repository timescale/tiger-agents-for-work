--002-user.sql

-----------------------------------------------------------------------
-- slack.user
create table if not exists slack.user
( id text not null primary key
, team_id text
, user_name text not null
, real_name text
, display_name text
, real_name_normalized text
, display_name_normalized text
, email text
, tz text
, tz_label text
, tz_offset int4
, deleted bool not null default false
, is_bot bool not null default false
, updated int8
);