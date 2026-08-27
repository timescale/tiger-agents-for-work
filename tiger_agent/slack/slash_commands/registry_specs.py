from unittest.mock import AsyncMock, MagicMock

import pytest

from tiger_agent.slack.slash_commands import registry
from tiger_agent.slack.slash_commands.command import Command
from tiger_agent.slack.slash_commands.group import CommandGroup
from tiger_agent.slack.slash_commands.registry import (
    _build_command_handlers,
    handle_command,
)


def _find(group: CommandGroup, key: str):
    for cmd in group.commands:
        if cmd.key == key:
            return cmd
    return None


class TestCommandTreeTopology:
    def test_root_has_three_top_level_groups(self):
        root = _build_command_handlers()
        assert {c.key for c in root.commands} == {"salesforce", "messages", "users"}

    def test_salesforce_group_exposes_create_notification_and_customer_channel(self):
        root = _build_command_handlers()
        salesforce = _find(root, "salesforce")
        keys = {c.key for c in salesforce.commands}
        assert keys == {"create-notification", "customer-channel"}

    def test_salesforce_customer_channel_has_add_and_remove(self):
        root = _build_command_handlers()
        customer_channel = _find(_find(root, "salesforce"), "customer-channel")
        keys = {c.key for c in customer_channel.commands}
        assert keys == {"add", "remove"}

    def test_users_group_has_admins_and_ignored_subgroups(self):
        root = _build_command_handlers()
        users = _find(root, "users")
        keys = {c.key for c in users.commands}
        assert keys == {"admins", "ignored"}

    def test_users_admins_has_add_list_remove(self):
        root = _build_command_handlers()
        admins = _find(_find(root, "users"), "admins")
        keys = {c.key for c in admins.commands}
        assert keys == {"add", "list", "remove"}

    def test_users_ignored_has_add_list_remove(self):
        root = _build_command_handlers()
        ignored = _find(_find(root, "users"), "ignored")
        keys = {c.key for c in ignored.commands}
        assert keys == {"add", "list", "remove"}

    def test_messages_group_has_delete(self):
        root = _build_command_handlers()
        messages = _find(root, "messages")
        keys = {c.key for c in messages.commands}
        assert keys == {"delete"}

    def test_expected_parameters_are_set_on_arg_taking_leaves(self):
        root = _build_command_handlers()
        assert _find(_find(root, "salesforce"), "create-notification").expected_parameters == 1
        cc = _find(_find(root, "salesforce"), "customer-channel")
        assert _find(cc, "add").expected_parameters == 2
        assert _find(cc, "remove").expected_parameters == 1
        assert _find(_find(root, "messages"), "delete").expected_parameters == 1

    def test_leaves_are_command_not_group(self):
        root = _build_command_handlers()
        leaf = _find(_find(_find(root, "users"), "admins"), "add")
        assert isinstance(leaf, Command)

    def test_memoized_returns_same_instance_on_repeated_calls(self):
        first = _build_command_handlers()
        second = _build_command_handlers()
        assert first is second


@pytest.fixture(autouse=True)
def reset_memoized_tree(monkeypatch):
    """Some tests below patch handler funcs; force a rebuild each time so the
    tree captures the patched references, not the ones bound at first import."""
    monkeypatch.setattr(registry, "_slash_commands", None)


@pytest.fixture
def hctx():
    hctx = MagicMock()
    hctx.pool = MagicMock()
    return hctx


class TestHandleCommandAdminGating:
    async def test_rejects_non_admin_user(self, hctx, make_slack_command, make_bot_info, monkeypatch):
        monkeypatch.setattr(registry, "user_is_admin", AsyncMock(return_value=False))
        cmd = make_slack_command(user_id="U_NOT_ADMIN", text="users admins list")

        result = await handle_command(command=cmd, hctx=hctx, bot_info=make_bot_info())

        assert result == "Slash commands can only be used by admins."

    async def test_dispatches_to_admin_command_when_user_is_admin(
        self, hctx, make_slack_command, make_bot_info, monkeypatch
    ):
        monkeypatch.setattr(registry, "user_is_admin", AsyncMock(return_value=True))
        # Patch an intermediate leaf's `handle` so we can observe dispatch without
        # touching the DB. The registry builds the tree lazily; the autouse
        # fixture above wiped the memo so this patch takes effect.
        fake = AsyncMock(return_value="dispatched-to-admins-list")
        monkeypatch.setattr(registry.admins_list, "handle", fake)

        cmd = make_slack_command(user_id="U_ADMIN", text="users admins list")
        result = await handle_command(command=cmd, hctx=hctx, bot_info=make_bot_info())

        assert result == "dispatched-to-admins-list"
        fake.assert_awaited_once()

    async def test_unknown_command_returns_help_text(
        self, hctx, make_slack_command, make_bot_info, monkeypatch
    ):
        monkeypatch.setattr(registry, "user_is_admin", AsyncMock(return_value=True))
        cmd = make_slack_command(user_id="U_ADMIN", text="not-a-real-command")

        result = await handle_command(command=cmd, hctx=hctx, bot_info=make_bot_info())

        assert "<not-a-real-command> is an invalid command" in result
        assert "Available commands:" in result

    async def test_passes_ctx_with_hctx_command_and_bot_info_to_handler(
        self, hctx, make_slack_command, make_bot_info, monkeypatch
    ):
        monkeypatch.setattr(registry, "user_is_admin", AsyncMock(return_value=True))
        fake = AsyncMock(return_value="ok")
        monkeypatch.setattr(registry.admins_list, "handle", fake)

        cmd = make_slack_command(user_id="U_ADMIN", text="users admins list")
        bot = make_bot_info()

        await handle_command(command=cmd, hctx=hctx, bot_info=bot)

        ctx_arg = fake.call_args.args[0]
        assert ctx_arg.hctx is hctx
        assert ctx_arg.command is cmd
        assert ctx_arg.bot_info is bot
