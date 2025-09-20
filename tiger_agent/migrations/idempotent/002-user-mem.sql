--002-user-mem.sql

-----------------------------------------------------------------------
-- agent.insert_user_memory
create or replace function agent.insert_user_memory(_user_id text, _memory text) returns agent.user_memory
as $func$
    insert into agent.user_memory
    ( user_id
    , memory
    )
    values
    ( _user_id
    , _memory
    )
    returning *
    ;
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.update_user_memory
create or replace function agent.update_user_memory(_id int8, _user_id text, _memory text) returns void
as $func$
    update agent.user_memory set
      memory = _memory
    , updated = now()
    where id = _id
    and user_id = _user_id
    ;
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.delete_user_memory
create or replace function agent.delete_user_memory(_id int8, _user_id text) returns void
as $func$
    delete from agent.user_memory 
    where id = _id 
    and user_id = _user_id
    returning id
    ;
$func$ language sql volatile security invoker
;

-----------------------------------------------------------------------
-- agent.list_user_memories
create or replace function agent.list_user_memories(_user_id text) returns setof agent.user_memory
as $func$
    select *
    from agent.user_memory
    where user_id = _user_id
    ;
$func$ language sql stable security invoker
;

-----------------------------------------------------------------------
-- agent.get_user_memory
create or replace function agent.get_user_memory(_id int8, _user_id text) returns setof agent.user_memory
as $func$
    select *
    from agent.user_memory
    where user_id = _user_id
    and id = _id
    ;
$func$ language sql stable security invoker
;