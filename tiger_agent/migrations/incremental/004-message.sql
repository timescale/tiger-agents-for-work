--004-message.sql

-----------------------------------------------------------------------
-- slack.message
create table slack.message
( ts timestamptz not null
, channel_id text not null
, team text
, text text
, type text
, user_id text
, blocks jsonb
, event_ts timestamptz
, thread_ts timestamptz
, channel_type text
, client_msg_id uuid
, parent_user_id text
, updated_at timestamptz
, bot_id text
, attachments jsonb
, files jsonb
, app_id text
, subtype text
, trigger_id text
, workflow_id text
, display_as_bot boolean
, upload boolean
, x_files jsonb
, icons jsonb
, language jsonb
, edited jsonb
, reactions jsonb
)
with
( tsdb.hypertable
, tsdb.partition_column='ts'
, tsdb.segmentby = 'channel_id'
, tsdb.orderby = 'ts desc, thread_ts desc'
);
create unique index on slack.message (channel_id, ts desc);
create index on slack.message (channel_id, thread_ts, ts desc) where thread_ts is not null;
