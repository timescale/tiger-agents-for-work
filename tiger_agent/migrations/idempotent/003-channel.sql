--003-channel.sql

-----------------------------------------------------------------------
-- slack.upsert_channel
create or replace function slack.upsert_channel(_event jsonb) returns void
as $func$
    insert into slack.channel
    ( id
    , channel_name
    , topic
    , purpose
    , is_archived
    , is_shared
    , is_ext_shared
    , is_org_shared
    , updated
    )
    values
    ( jsonb_extract_path_text(_event, 'channel', 'id')
    , jsonb_extract_path_text(_event, 'channel', 'name')
    , jsonb_extract_path_text(_event, 'channel', 'topic', 'value')
    , jsonb_extract_path_text(_event, 'channel', 'purpose', 'value')
    , jsonb_extract_path_text(_event, 'channel', 'is_archived')::boolean
    , jsonb_extract_path_text(_event, 'channel', 'is_shared')::boolean
    , jsonb_extract_path_text(_event, 'channel', 'is_ext_shared')::boolean
    , jsonb_extract_path_text(_event, 'channel', 'is_org_shared')::boolean
    , jsonb_extract_path_text(_event, 'channel', 'updated')::bigint
    )
    on conflict (id) do update set
      channel_name = excluded.channel_name
    , topic = excluded.topic
    , purpose = excluded.purpose
    , is_archived = excluded.is_archived
    , is_shared = excluded.is_shared
    , is_ext_shared = excluded.is_ext_shared
    , is_org_shared = excluded.is_org_shared
    , updated = excluded.updated
    ;
$func$ language sql volatile security invoker
;