--002-user.sql

-----------------------------------------------------------------------
-- slack.upsert_user
create or replace function slack.upsert_user(_event jsonb) returns void
as $func$
    insert into slack."user"
    ( id
    , team_id
    , user_name
    , real_name
    , display_name
    , real_name_normalized
    , display_name_normalized
    , email
    , tz
    , tz_label
    , tz_offset
    , deleted
    , is_bot
    , updated
    )
    values
    ( jsonb_extract_path_text(_event, 'user', 'id')
    , jsonb_extract_path_text(_event, 'user', 'team_id')
    , jsonb_extract_path_text(_event, 'user', 'name')
    , jsonb_extract_path_text(_event, 'user', 'real_name')
    , jsonb_extract_path_text(_event, 'user', 'profile', 'display_name')
    , jsonb_extract_path_text(_event, 'user', 'profile', 'real_name_normalized')
    , jsonb_extract_path_text(_event, 'user', 'profile', 'display_name_normalized')
    , jsonb_extract_path_text(_event, 'user', 'profile', 'email')
    , jsonb_extract_path_text(_event, 'user', 'tz')
    , jsonb_extract_path_text(_event, 'user', 'tz_label')
    , jsonb_extract_path_text(_event, 'user', 'tz_offset')::int
    , coalesce(jsonb_extract_path_text(_event, 'user', 'deleted')::boolean, false)
    , coalesce(jsonb_extract_path_text(_event, 'user', 'is_bot')::boolean, false)
    , jsonb_extract_path_text(_event, 'user', 'updated')::bigint
    )
    on conflict (id) do update set
      team_id = excluded.team_id
    , user_name = excluded.user_name
    , real_name = excluded.real_name
    , display_name = excluded.display_name
    , real_name_normalized = excluded.real_name_normalized
    , display_name_normalized = excluded.display_name_normalized
    , email = excluded.email
    , tz = excluded.tz
    , tz_label = excluded.tz_label
    , tz_offset = excluded.tz_offset
    , deleted = excluded.deleted
    , is_bot = excluded.is_bot
    , updated = excluded.updated
    ;
$func$ language sql volatile security invoker
;