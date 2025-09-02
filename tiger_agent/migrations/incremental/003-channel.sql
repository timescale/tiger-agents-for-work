--003-channel.sql

-----------------------------------------------------------------------
-- slack.channel
create table if not exists slack.channel
( id text not null primary key 
, channel_name text not null
, topic text
, purpose text
, is_archived bool not null default false
, is_shared bool not null default false
, is_ext_shared bool not null default false
, is_org_shared bool not null default false
, updated int8
);