"""Shared test fixtures for tiger_agent.

Pytest auto-discovers this file for every test module in the package.
Factory helpers here build minimal, valid pydantic models so individual
tests can focus on the field(s) they care about.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_sdk.web.async_client import AsyncWebClient

from tiger_agent.slack.types import BotInfo, SlackCommand, UserInfo, UserProfile


def _make_bot_info(**overrides) -> BotInfo:
    defaults: dict = {
        "url": "https://tigerdata.slack.com/",
        "team": "TigerData",
        "team_id": "T_HOME",
        "bot_id": "B_BOT",
        "name": "eon",
        "app_id": "A_APP",
        "user_id": "U_BOT",
    }
    defaults.update(overrides)
    return BotInfo(**defaults)


def _make_user_info(**overrides) -> UserInfo:
    defaults: dict = {
        "id": "U_USER",
        "team_id": "T_HOME",
        "name": "someone",
        "profile": UserProfile(),
    }
    defaults.update(overrides)
    return UserInfo(**defaults)


def _make_slack_command(**overrides) -> SlackCommand:
    defaults: dict = {
        "channel_id": "C_CHAN",
        "channel_name": "general",
        "user_id": "U_USER",
        "user_name": "someone",
        "command": "/agent",
        "text": "",
    }
    defaults.update(overrides)
    return SlackCommand(**defaults)


def _make_async_web_client_mock(**overrides) -> MagicMock:
    """Build a MagicMock that mimics a Slack `AsyncWebClient`.

    Common Web API methods used by the codebase (`chat_postMessage`,
    `conversations_replies`, `users_info`, etc.) are pre-populated with
    `AsyncMock()` so tests can `await` them without extra wiring. Pass
    keyword overrides to swap in specific `AsyncMock(return_value=...)`
    stubs for the methods a test cares about, e.g.

        make_async_web_client_mock(
            chat_postMessage=AsyncMock(return_value={"ok": True, "ts": "1.2"}),
        )
    """
    client = MagicMock(spec=AsyncWebClient)

    method_defaults: dict[str, dict] = {
        "chat_postMessage": {"ok": True, "ts": "1.0", "channel": "C_CHAN"},
        "chat_postEphemeral": {"ok": True},
        "chat_stream": {"ok": True, "ts": "1.0", "channel": "C_CHAN"},
        "conversations_info": {"ok": True, "channel": {"id": "C_CHAN"}},
        "conversations_members": {"ok": True, "members": []},
        "conversations_replies": {"ok": True, "messages": []},
        "users_info": {"ok": True, "user": {"id": "U_USER"}},
        "reactions_add": {"ok": True},
        "reactions_remove": {"ok": True},
        "files_info": {"ok": True, "file": {}},
        "files_upload_v2": {"ok": True},
        "auth_test": {"ok": True, "user_id": "U_BOT", "team_id": "T_HOME"},
        "assistant_threads_setStatus": {"ok": True},
    }
    for method_name, default_return in method_defaults.items():
        setattr(client, method_name, AsyncMock(return_value=default_return))

    for method_name, mock in overrides.items():
        setattr(client, method_name, mock)

    return client


def _make_pool_mock() -> MagicMock:
    """Build a MagicMock that mimics the psycopg pool → connection → cursor chain.

    Handlers use ``async with pool.connection() as con, con.transaction() as _,
    con.cursor() as cur``, then ``await cur.execute(...)`` / ``await
    cur.fetchall()``. Tests can access the cursor via ``pool._cursor``.
    """
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)

    cursor_ctx = MagicMock()
    cursor_ctx.__aenter__ = AsyncMock(return_value=cursor)
    cursor_ctx.__aexit__ = AsyncMock(return_value=False)

    transaction_ctx = MagicMock()
    transaction_ctx.__aenter__ = AsyncMock(return_value=None)
    transaction_ctx.__aexit__ = AsyncMock(return_value=False)

    con = MagicMock()
    con.cursor = MagicMock(return_value=cursor_ctx)
    con.transaction = MagicMock(return_value=transaction_ctx)
    con.execute = AsyncMock()

    con_ctx = MagicMock()
    con_ctx.__aenter__ = AsyncMock(return_value=con)
    con_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.connection = MagicMock(return_value=con_ctx)

    # Expose collaborators for assertions in tests.
    pool._cursor = cursor
    pool._connection = con
    return pool


@pytest.fixture
def make_bot_info():
    return _make_bot_info


@pytest.fixture
def make_user_info():
    return _make_user_info


@pytest.fixture
def make_slack_command():
    return _make_slack_command


@pytest.fixture
def make_pool_mock():
    return _make_pool_mock


@pytest.fixture
def make_async_web_client_mock():
    return _make_async_web_client_mock
