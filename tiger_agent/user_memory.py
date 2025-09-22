from datetime import datetime
from textwrap import dedent
from typing import Any

import logfire
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel


class UserMemory(BaseModel):
    """A user-specific memory stored by the AI agent.

    Represents information the agent has learned about a user that should be
    remembered across conversations, such as preferences, context, or facts.
    """

    id: int
    """Unique identifier for this memory record"""

    user_id: str
    """The Slack user ID this memory belongs to"""

    memory: str
    """The actual memory content about the user"""

    created: datetime
    """When this memory was first created"""

    updated: datetime | None = None
    """When this memory was last updated, if ever"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserMemory":
        """Create a UserMemory instance from a dictionary of data."""
        return cls(**data)


@logfire.instrument("insert_user_memory", extract_args=["user_id"])
async def insert_user_memory(pool: AsyncConnectionPool, user_id: str, memory: str) -> UserMemory:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            dedent("""\
            insert into agent.user_memory
            ( user_id
            , memory
            )
            values
            ( %s
            , %s
            )
            returning *
            """),
            (user_id, memory)
        )
        row = await cur.fetchone()
        return UserMemory.from_dict(row)


@logfire.instrument("update_user_memory", extract_args=["id", "user_id"])
async def update_user_memory(pool: AsyncConnectionPool, id: int, user_id: str, memory: str) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute(
            dedent("""\
                update agent.user_memory set
                  memory = %(memory)s
                , updated = now()
                where id = %(id)s
                and user_id = %(user_id)s
            """),
            dict(
                id=id,
                user_id=user_id,
                memory=memory
            )
        )


@logfire.instrument("delete_user_memory", extract_args=["id", "user_id"])
async def delete_user_memory(pool: AsyncConnectionPool, id: int, user_id: str) -> None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute(
            dedent("""\
                delete from agent.user_memory 
                where id = %(id)s
                and user_id = %(user_id)s
                returning id
            """),
            dict(id=id, user_id=user_id)
        )


@logfire.instrument("list_user_memory", extract_args=["user_id"])
async def list_user_memories(pool: AsyncConnectionPool, user_id: str) -> list[UserMemory]:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            dedent("""\
                select *
                from agent.user_memory
                where user_id = %s
            """),
            (user_id,)
        )
        memories = []
        for row in await cur.fetchall():
            memories.append(UserMemory.from_dict(row))
        return memories


@logfire.instrument("get_user_memory", extract_args=["id", "user_id"])
async def get_user_memory(pool: AsyncConnectionPool, id: int, user_id: str) -> UserMemory | None:
    async with (
        pool.connection() as con,
        con.transaction() as _,
        con.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            dedent("""\
                select *
                from agent.user_memory
                where user_id = %(user_id)s
                and id = %(id)s
            """),
            dict(id=id, user_id=user_id)
        )
        row = await cur.fetchone()
        return UserMemory.from_dict(row) if row else None
