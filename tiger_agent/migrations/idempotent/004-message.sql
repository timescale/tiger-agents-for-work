--004-message.sql

-----------------------------------------------------------------------
-- slack.insert_message
create or replace function slack.insert_message(_event jsonb) returns void
as $func$
    insert into slack.message
    ( ts
    , channel_id
    , team
    , text
    , type
    , user_id
    , blocks
    , event_ts
    , thread_ts
    , channel_type
    , client_msg_id
    , parent_user_id
    , bot_id
    , attachments
    , files
    , app_id
    , subtype
    , trigger_id
    , workflow_id
    , display_as_bot
    , upload
    , x_files
    , icons
    , language
    , edited
    )
    values
    ( slack.to_timestamptz((_event->>'ts')::numeric)
    , _event->>'channel'
    , _event->>'team'
    , _event->>'text'
    , _event->>'type'
    , _event->>'user'
    , _event->'blocks'
    , slack.to_timestamptz((_event->>'event_ts')::numeric)
    , slack.to_timestamptz((_event->>'thread_ts')::numeric)
    , _event->>'channel_type'
    , (_event->>'client_msg_id')::uuid
    , _event->>'parent_user_id'
    , _event->>'bot_id'
    , _event->'attachments'
    , _event->'files'
    , _event->>'app_id'
    , _event->>'subtype'
    , _event->>'trigger_id'
    , _event->>'workflow_id'
    , (_event->>'display_as_bot')::boolean
    , (_event->>'upload')::boolean
    , _event->'x_files'
    , _event->'icons'
    , _event->'language'
    , _event->'edited'
    );
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- slack.update_message
create or replace function slack.update_message(_event jsonb) returns void
as $func$
    update slack.message m set
      team = jsonb_extract_path_text(_event, 'message', 'team')
    , text = jsonb_extract_path_text(_event, 'message', 'text')
    , type = jsonb_extract_path_text(_event, 'message', 'type')
    , user_id = jsonb_extract_path_text(_event, 'message', 'user')
    , blocks = jsonb_extract_path(_event, 'message', 'blocks')
    , attachments = jsonb_extract_path(_event, 'message', 'attachments')
    , files = jsonb_extract_path(_event, 'message', 'files')
    , app_id = jsonb_extract_path_text(_event, 'message', 'app_id')
    , subtype = jsonb_extract_path_text(_event, 'message', 'subtype')
    , trigger_id = jsonb_extract_path_text(_event, 'message', 'trigger_id')
    , workflow_id = jsonb_extract_path_text(_event, 'message', 'workflow_id')
    , display_as_bot = jsonb_extract_path_text(_event, 'message', 'display_as_bot')::boolean
    , upload = jsonb_extract_path_text(_event, 'message', 'upload')::boolean
    , x_files = jsonb_extract_path(_event, 'message', 'x_files')
    , icons = jsonb_extract_path(_event, 'message', 'icons')
    , language = jsonb_extract_path(_event, 'message', 'language')
    , edited = jsonb_extract_path(_event, 'message', 'edited')
    where (m.ts, m.channel_id) = 
    ( slack.to_timestamptz(jsonb_extract_path_text(_event, 'message', 'ts')::numeric)
    , _event->>'channel'
    );
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- slack.delete_message
create or replace function slack.delete_message(_event jsonb) returns void
as $func$
    delete from slack.message m
    where m.ts = slack.to_timestamptz((_event->>'deleted_ts')::numeric)
    and m.channel_id = _event->>'channel'
    ;
$func$ language sql volatile security invoker
;