import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from tiger_agent.types import CommandContext, HarnessContext, SlackCommand
from tiger_agent.utils import parse_slack_user_name


@dataclass
class CommandBase(ABC):
    key: str | None = None
    description: str | None = None

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

    def _get_commands(self):
        return "\n".join([f"{x.key}\t{x.description}" for x in self.commands])

    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        args = self._get_args(command_text=command_text)
        
        has_more_args = len(args) > 0
        curr = args.pop(0) if has_more_args else None
        matching_command = None
        if curr is not None:
            # find first command where regex matches
            for cmd in self.commands:
                if cmd.key is not None and re.match(cmd.key, curr):
                    if cmd.key != curr:
                        args.insert(0, curr)
                    matching_command = cmd
                    break
        
        if matching_command is None:
            result = f"{f"<{curr}> is an invalid command.\n" if curr is not None else ""}Available commands:\n{self._get_commands()}"
            return result
        
        return await matching_command(args, ctx)

async def handle_ignore_command(ctx: CommandContext, args: list[str]) -> str:
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


async def handle_unignore_command(ctx: CommandContext, args: list[str]) -> str:
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


async def handle_ignore_list_command(ctx: CommandContext, args: list[str]) -> str:
    async with (
        ctx.hctx.pool.connection() as con,
        con.cursor() as cur,
    ):
        await cur.execute("select user_id, event_ts from agent.ignored_user_list()")
        rows = await cur.fetchall()

        if not rows:
            return "No users are currently ignored."

        user_list = []
        for row in rows:
            user_id, event_ts = row
            user_list.append(f"<@{user_id}> (ignored since {event_ts.strftime('%Y-%m-%d %H:%M')})")

        return f"Currently ignored users ({len(user_list)}):\n" + "\n".join(user_list)

_slash_commands: CommandGroup | None = None

def _build_command_handlers() -> CommandGroup:
    global _slash_commands
    if _slash_commands is None:
        _slash_commands = CommandGroup(
            commands=[
                CommandGroup(
                    key="admin",
                    description="Administrative commands",
                    commands=[
                        CommandGroup(key="ignore",
                            description="",
                            commands=[
                                Command(
                                    key=r"<@[A-Z0-9]+\|[^>]+>",
                                    expected_parameters=1,
                                    func=handle_ignore_command
                                ), Command(
                                    key="list",
                                    func=handle_ignore_list_command
                                )
                            ]
                        ),
    
                        Command(key="unignore",
                            expected_parameters=1,
                            description="Unignore a user",
                            func=handle_unignore_command)
                        ]
                )
            ]
        )
    return _slash_commands

async def handle_command(command: SlackCommand, hctx: HarnessContext) -> str:
    ctx = CommandContext(hctx=hctx, command=command)
    handlers = _build_command_handlers()
    return await handlers(command.get("text"), ctx)