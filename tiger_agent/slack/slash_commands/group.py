from dataclasses import dataclass, field

from tiger_agent.slack.slash_commands.base import CommandBase, CommandContext


@dataclass
class CommandGroup(CommandBase):
    """Recursive tree node — routes a token to a nested Command or CommandGroup."""

    commands: list[CommandBase] = field(default_factory=list)
    _commands_dict: dict[str, CommandBase] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self._commands_dict = {x.key: x for x in self.commands}

    def _get_commands(self) -> str:
        lines = []
        for x in self.commands:
            lines.append(f"{x.name or x.key}")

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
            prefix = f"<{curr}> is an invalid command.\n" if curr is not None else ""
            return f"{prefix}Available commands:\n{self._get_commands()}"

        return await matching_command(args, ctx)
