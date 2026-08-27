from tiger_agent.db.utils import user_is_admin
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.command import Command
from tiger_agent.slack.slash_commands.group import CommandGroup
from tiger_agent.slack.slash_commands.handlers.admins import add as admins_add
from tiger_agent.slack.slash_commands.handlers.admins import list as admins_list
from tiger_agent.slack.slash_commands.handlers.admins import remove as admins_remove
from tiger_agent.slack.slash_commands.handlers.ignored import add as ignored_add
from tiger_agent.slack.slash_commands.handlers.ignored import list as ignored_list
from tiger_agent.slack.slash_commands.handlers.ignored import remove as ignored_remove
from tiger_agent.slack.slash_commands.handlers.messages import delete as messages_delete
from tiger_agent.slack.slash_commands.handlers.salesforce import (
    add_customer_channel as sf_add_customer_channel,
)
from tiger_agent.slack.slash_commands.handlers.salesforce import (
    create_notification as sf_create_notification,
)
from tiger_agent.slack.slash_commands.handlers.salesforce import (
    remove_customer_channel as sf_remove_customer_channel,
)
from tiger_agent.slack.types import BotInfo, SlackCommand
from tiger_agent.types import HarnessContext

_slash_commands: CommandGroup | None = None


def _build_command_handlers() -> CommandGroup:
    global _slash_commands
    if _slash_commands is None:
        _slash_commands = CommandGroup(
            commands=[
                CommandGroup(
                    key="salesforce",
                    commands=[
                        Command(
                            key="create-notification",
                            expected_parameters=1,
                            func=sf_create_notification.handle,
                        ),
                        CommandGroup(
                            key="customer-channel",
                            commands=[
                                Command(
                                    "add",
                                    expected_parameters=2,
                                    func=sf_add_customer_channel.handle,
                                ),
                                Command(
                                    "remove",
                                    expected_parameters=1,
                                    func=sf_remove_customer_channel.handle,
                                ),
                            ],
                        ),
                    ],
                ),
                CommandGroup(
                    key="messages",
                    commands=[
                        Command(
                            key="delete",
                            expected_parameters=1,
                            func=messages_delete.handle,
                        ),
                    ],
                ),
                CommandGroup(
                    key="users",
                    commands=[
                        CommandGroup(
                            key="admins",
                            commands=[
                                Command(
                                    key="add",
                                    func=admins_add.handle,
                                    expected_parameters=1,
                                ),
                                Command(key="list", func=admins_list.handle),
                                Command(
                                    key="remove",
                                    func=admins_remove.handle,
                                    expected_parameters=1,
                                ),
                            ],
                        ),
                        CommandGroup(
                            key="ignored",
                            commands=[
                                Command(
                                    key="add",
                                    expected_parameters=1,
                                    func=ignored_add.handle,
                                ),
                                Command(key="list", func=ignored_list.handle),
                                Command(
                                    key="remove",
                                    expected_parameters=1,
                                    func=ignored_remove.handle,
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        )
    return _slash_commands


async def handle_command(
    command: SlackCommand, hctx: HarnessContext, bot_info: BotInfo
) -> str:
    if not await user_is_admin(pool=hctx.pool, user_id=command.user_id):
        return "Slash commands can only be used by admins."
    ctx = CommandContext(hctx=hctx, command=command, bot_info=bot_info)
    handlers = _build_command_handlers()
    return await handlers(command.text, ctx)
