"""Recursive, regex-based command parsing for Slack slash commands.

## Architecture Overview

The command system is built around a recursive structure where:
- `CommandBase` is the abstract base class for all commands
- `Command` handles leaf-level actions (actual command execution)
- `CommandGroup` handles hierarchical command organization and routing

## Command Structure

Commands are organized in a tree structure:

    admin/
    ├── ignore/
    │   ├── <@U123|user>  (regex pattern for user mentions)
    │   └── list          (exact string match)
    └── unignore          (exact string match)

## How Parsing Works

1. Input like "admin ignore <@U123|user>" is split into tokens:
   ["admin", "ignore", "<@U123|user>"]
2. The root CommandGroup processes "admin", finds the admin CommandGroup
3. The admin CommandGroup processes "ignore", finds the ignore CommandGroup
4. The ignore CommandGroup processes "<@U123|user>", matches the regex Command
5. The matching Command executes with the remaining arguments

## Pattern Matching

Commands use exact-key lookup by default:
- String keys like "admin" create exact matches
- First matching pattern wins (order matters in command lists)

## Error Handling

If no command matches, the system returns available subcommands for that level.
Commands can validate argument counts and return appropriate error messages.
"""

from tiger_agent.slack.slash_commands.base import CommandBase, CommandContext
from tiger_agent.slack.slash_commands.command import Command
from tiger_agent.slack.slash_commands.group import CommandGroup
from tiger_agent.slack.slash_commands.registry import handle_command

__all__ = [
    "Command",
    "CommandBase",
    "CommandContext",
    "CommandGroup",
    "handle_command",
]
