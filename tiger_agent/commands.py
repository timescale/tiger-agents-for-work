import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from tiger_agent.types import CommandContext, HarnessContext, SlackCommand
from tiger_agent.utils import parse_slack_user_name, user_is_admin

"""
Command System for Tiger Agent Slack Bot

This module implements a recursive, regex-based command parsing system for handling
Slack slash commands. The system supports nested command groups and flexible pattern matching.

## Architecture Overview

The command system is built around a recursive structure where:
- `CommandBase` is the abstract base class for all commands
- `Command` handles leaf-level actions (actual command execution)
- `CommandGroup` handles hierarchical command organization and routing

## Command Structure

Commands are organized in a tree structure:
```
admin/
├── ignore/
│   ├── <@U123|user>  (regex pattern for user mentions)
│   └── list          (exact string match)
└── unignore          (exact string match)
```

## How Parsing Works

1. Input like "admin ignore <@U123|user>" is split into tokens: ["admin", "ignore", "<@U123|user>"]
2. The root CommandGroup processes "admin", finds the admin CommandGroup
3. The admin CommandGroup processes "ignore", finds the ignore CommandGroup
4. The ignore CommandGroup processes "<@U123|user>", matches the regex pattern Command
5. The matching Command executes with the remaining arguments

## Pattern Matching

Commands use `re.match()` to match patterns from the beginning of tokens:
- String keys like "admin" create exact matches
- Regex keys like `r"<@[A-Z0-9]+\|[^>]+>"` match Slack user mentions
- First matching pattern wins (order matters in command lists)

## Error Handling

If no command matches, the system returns available subcommands for that level.
Commands can validate argument counts and return appropriate error messages.
"""

@dataclass
class CommandBase(ABC):
    # this is used to match the command, can be a regex pattern or just a string
    key: str | None = None
    name: str | None = None

    @abstractmethod
    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        pass

    def _get_args(self, command_text: str | list[str]) -> list[str] | None:
        """Remove redundant spaces, then split by spaces.

        Example: "admin   ignore @user" becomes ['admin', 'ignore', '@user']
        """
        if isinstance(command_text, str):
            # First normalize whitespace
            normalized = re.sub(r"\s+", " ", command_text).strip()
            # Split on spaces but keep content between < > together
            return re.findall(r"<[^>]+>|\S+", normalized)
        return command_text
        
@dataclass
class Command(CommandBase):
    expected_parameters: int = 0
    func: Callable[[CommandContext, str | list[str]], Awaitable[str]] = lambda _: asyncio.sleep(0)
    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        args = self._get_args(command_text=command_text)
        if len(args) != self.expected_parameters:
            return f"Incorrect number of parameters given for <{self.key}>"
        return await self.func(ctx, args)
    
@dataclass
class CommandGroup(CommandBase):
    commands: list[CommandBase] = field(default_factory=list)
    _commands_dict: dict[str, CommandBase] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self._commands_dict = {x.key: x for x in self.commands}

    def _get_commands(self):
        lines = []
        for x in self.commands:
            # Add the main command
            lines.append(f"{x.name or x.key}")

            # If it's a CommandGroup, add its sub-commands indented
            if isinstance(x, CommandGroup):
                for sub_cmd in x.commands:
                    lines.append(f"\t\t{x.name or x.key} {sub_cmd.name or sub_cmd.key}")

        return "\n".join(lines)

    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        args = self._get_args(command_text=command_text)
        
        has_more_args = len(args) > 0
        curr = args.pop(0) if has_more_args else None
        matching_command = None
        if curr is not None:
            matching_command = self._commands_dict.get(curr)
        
        if matching_command is None:
            result = f"{f"<{curr}> is an invalid command.\n" if curr is not None else ""}Available commands:\n{self._get_commands()}"
            return result
        
        return await matching_command(args, ctx)

async def handle_admins_add_command(ctx: CommandContext, args: list[str]) -> str:
    username, user_id = parse_slack_user_name(args[0])
    if username is None or user_id is None:
        return "Argument needs to be a Slack username"
    async with (
        ctx.hctx.pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.insert_admin_user(%s)", (Jsonb(ctx.command),))
    return f"Unignored <{username}>"


async def handle_admins_remove_command(ctx: CommandContext, args: list[str]) -> str:
    username, user_id = parse_slack_user_name(args[0])
    if username is None or user_id is None:
        return "Argument needs to be a Slack username"
    async with (
        ctx.hctx.pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.delete_admin_user(%s)", (Jsonb(ctx.command),))
    return f"Removed admin <{username}>"

async def handle_ignored_add_command(ctx: CommandContext, args: list[str]) -> str:
    username, user_id = parse_slack_user_name(args[0])
    if username is None or user_id is None:
        return "Argument needs to be a Slack username"
    async with (
        ctx.hctx.pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.insert_ignored_user(%s)", (Jsonb(ctx.command),))
    return f"Ignored <{username}>"


async def handle_ignored_remove_command(ctx: CommandContext, args: list[str]) -> str:
    username, user_id = parse_slack_user_name(args[0])
    if username is None or user_id is None:
        return "Argument needs to be a Slack username"
    async with (
        ctx.hctx.pool.connection() as con,
        con.transaction() as _,
        con.cursor() as cur,
    ):
        await cur.execute("select agent.delete_ignored_user(%s)", (Jsonb(ctx.command),))
    return f"Unignored <{username}>"


async def handle_ignore_list_command(ctx: CommandContext, _: list[str]) -> str:
    async with (
        ctx.hctx.pool.connection() as con,
        con.cursor() as cur,
    ):
        await cur.execute("select * from agent.ignored_users")
        rows = await cur.fetchall()

        if not rows:
            return "No users are currently ignored."

        user_list = []
        for row in rows:
            user_id = row[0]
            user_list.append(f"<@{user_id}>")

        return f"Currently ignored users ({len(user_list)}):\n" + "\n".join(user_list)


async def handle_admins_list_command(ctx: CommandContext, _: list[str]) -> str:
    async with (
        ctx.hctx.pool.connection() as con,
        con.cursor() as cur,
    ):
        await cur.execute("select * from agent.admin_users")
        rows = await cur.fetchall()

        if not rows:
            return "No admin users are currently configured."

        user_list = []
        for row in rows:
            user_id = row[0]
            user_list.append(f"<@{user_id}>")

        return f"Current admin users ({len(user_list)}):\n" + "\n".join(user_list)

_slash_commands: CommandGroup | None = None

def _build_command_handlers() -> CommandGroup:
    global _slash_commands
    if _slash_commands is None:
        _slash_commands = CommandGroup(
            commands=[CommandGroup(
                key="users",
                commands=[
                    CommandGroup(
                        key="admins",
                        commands=[
                            Command(
                                key="add",
                                func=handle_admins_add_command,
                                expected_parameters=1
                            ),
                            Command(
                                key="list",
                                func=handle_admins_list_command
                            ),
                            Command(
                                key="remove",
                                func=handle_admins_remove_command,
                                expected_parameters=1
                            )
                        ]
                    ),
                    CommandGroup(
                        key="ignored",
                        commands=[
                            Command(
                                key="add",
                                expected_parameters=1,
                                func=handle_ignored_add_command
                            ),
                            Command(
                                key="list",
                                func=handle_ignore_list_command
                            ),
                            Command(
                                key="remove",
                                expected_parameters=1,
                                func=handle_ignored_remove_command
                            )
                        ]
                    )
                ]
            )]
        )
    return _slash_commands

async def handle_command(command: SlackCommand, hctx: HarnessContext) -> str:
    if not await user_is_admin(pool=hctx.pool, user_id=command.user_id):
        return "Slash commands can only be used by admins."
    ctx = CommandContext(hctx=hctx, command=command)
    handlers = _build_command_handlers()
    return await handlers(command.text, ctx)