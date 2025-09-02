--005-reaction.sql

-----------------------------------------------------------------------
-- slack.add_reaction
create or replace function slack.add_reaction(_event jsonb) returns void
as $func$
    update slack.message set reactions =
      coalesce(reactions, jsonb_build_array()) ||
      jsonb_build_array
      (
        jsonb_build_object
        ( 'user_id', _event->>'user'
        , 'reaction', _event->>'reaction'
        )
      )
    where ts = slack.to_timestamptz(jsonb_extract_path_text(_event, 'item', 'ts')::numeric)
    and channel_id = jsonb_extract_path_text(_event, 'item', 'channel')
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- slack.remove_reaction
create or replace function slack.remove_reaction(_event jsonb) returns void
as $func$
    update slack.message set reactions = 
      jsonb_path_query_array
      ( reactions
      , '$[*] ? (!(@.user == $user && @.reaction == $reaction))'
      , jsonb_build_object
        ( 'user_id', _event->>'user'
        , 'reaction', _event->>'reaction'
        )
      )
    where ts = slack.to_timestamptz(jsonb_extract_path_text(_event, 'item', 'ts')::numeric)
    and channel_id = jsonb_extract_path_text(_event, 'item', 'channel')
    and reactions is not null
$func$ language sql volatile security invoker
;
