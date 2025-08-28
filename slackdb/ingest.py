from typing import Awaitable, Callable, Any, Optional

import logfire
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck


@logfire.instrument("upsert_user", extract_args=False)
async def upsert_user(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    #with (
    #    pool.connection() as con,
    #    con.transaction() as _,
    #    con.cursor() as cur,
    #):
    #    await cur.execute("""\
    #    
    #    """, {"_event", Jsonb(event)})
    pass


@logfire.instrument("upsert_channel", extract_args=False)
async def upsert_channel(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("insert_message", extract_args=False)
async def insert_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("update_message", extract_args=False)
async def update_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("delete_message", extract_args=False)
async def delete_message(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("add_reaction", extract_args=False)
async def add_reaction(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("remove_reaction", extract_args=False)
async def remove_reaction(pool: AsyncConnectionPool, event: dict[str, Any]) -> None:
    pass


@logfire.instrument("archive_event", extract_args=False)
async def archive_event(pool: AsyncConnectionPool, event: dict[str, Any], error: Optional[dict[str, Any]]) -> None:
    pass


async def register_event_handlers(app: AsyncApp, pool: AsyncConnectionPool) -> None:
    @app.message("")
    async def message(ack: AsyncAck, event: dict[str, Any]):
        with logfire.span("message") as span:
            await ack()
            assert event.get("type") == "message", f"event's type is not 'message'. {event.get("type")}"
            event_subtype: Optional[str] = event.get("subtype")
            span.set_attribute("subtype", event_subtype)
            error: Optional[dict[str, Any]] = None
            try:
                match event_subtype:
                    case None | "bot_message" | "thread_broadcast" | "file_share":
                        await insert_message(pool, event)
                    case "message_changed":
                        await update_message(pool, event)
                    case "message_deleted":
                        await delete_message(pool, event)
            except:
                pass
            finally:
                await archive_event(pool, event, error)

    def register_event_handler(event_name: str, fn: Callable[[AsyncConnectionPool, dict[str, Any]], Awaitable[None]]) -> None:
        async def event_handler(ack: AsyncAck, event: dict[str, Any]) -> None:
            with logfire.span(event_name) as _:
                await ack()
                error: Optional[dict[str, Any]] = None
                try:
                    await fn(pool, event)
                except:
                    pass
                finally:
                    await archive_event(pool, event, error)
        
        app.event(event_name)(event_handler)

    register_event_handler("app_mention", insert_message)
    register_event_handler("channel_created", upsert_channel)
    register_event_handler("channel_renamed", upsert_channel)
    register_event_handler("reaction_added", add_reaction)
    register_event_handler("reaction_removed", remove_reaction)
    register_event_handler("team_join", upsert_user)
    register_event_handler("user_change", upsert_user)
    register_event_handler("user_profile_changed", upsert_user)
