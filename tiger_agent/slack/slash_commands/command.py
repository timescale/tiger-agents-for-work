import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tiger_agent.slack.slash_commands.base import CommandBase, CommandContext


@dataclass
class Command(CommandBase):
    """Leaf command — executes a handler with a fixed argument count."""

    expected_parameters: int = 0
    func: Callable[[CommandContext, str | list[str]], Awaitable[str]] = lambda _: (
        asyncio.sleep(0)
    )

    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        args = self._get_args(command_text=command_text)
        if len(args) != self.expected_parameters:
            return f"Incorrect number of parameters given for <{self.key}>"
        return await self.func(ctx, args)
