import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from tiger_agent.slack.types import BotInfo, SlackCommand
from tiger_agent.types import HarnessContext


@dataclass
class CommandContext:
    """Shared context provided to command handlers."""

    hctx: HarnessContext
    command: SlackCommand
    bot_info: BotInfo


@dataclass
class CommandBase(ABC):
    # the key is used to match the command; it can be a regex pattern or a literal string
    key: str | None = None
    name: str | None = None

    @abstractmethod
    async def __call__(self, command_text: str | list[str], ctx: CommandContext) -> str:
        pass

    def _get_args(self, command_text: str | list[str]) -> list[str] | None:
        """Normalize whitespace, then split on spaces while keeping <...> groups intact.

        Example: "admin   ignore @user" -> ['admin', 'ignore', '@user']
        """
        if isinstance(command_text, str):
            normalized = re.sub(r"\s+", " ", command_text).strip()
            return re.findall(r"<[^>]+>|\S+", normalized)
        return command_text
