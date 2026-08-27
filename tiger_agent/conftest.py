"""Shared test fixtures for tiger_agent.

Pytest auto-discovers this file for every test module in the package.
Factory helpers here build minimal, valid pydantic models so individual
tests can focus on the field(s) they care about.
"""

from __future__ import annotations

import pytest

from tiger_agent.slack.types import BotInfo, UserInfo, UserProfile


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


@pytest.fixture
def make_bot_info():
    return _make_bot_info


@pytest.fixture
def make_user_info():
    return _make_user_info
