import re

from tiger_agent.db.utils import list_scheduled_rules
from tiger_agent.salesforce.types import UserDefinedRule
from tiger_agent.slack.slash_commands.base import CommandContext

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
MAX_PERIOD_HOURS = 720.0


def scheduled_action_prompt(skill_name: str) -> str:
    """The rule prompt `schedules add` stores; the skill carries the procedure."""
    return (
        f"Discover and follow the skill `{skill_name}` for the window given below. "
        "Report per the output contract."
    )


def parse_channel(arg: str) -> str | None:
    """Accept a bare channel id or the `<#C123|name>` form Slack sends for mentions."""
    match = re.fullmatch(r"<#([CG][A-Z0-9]+)(?:\|[^>]*)?>", arg)
    channel = match.group(1) if match else arg
    return channel if CHANNEL_ID_RE.match(channel) else None


def resolve_channel(ctx: CommandContext, arg: str | None) -> str | None:
    """The channel argument if given, else the channel the command was typed in."""
    if arg is not None:
        return parse_channel(arg)
    return ctx.command.channel_id


def parse_hours(arg: str) -> float | None:
    try:
        hours = float(arg)
    except ValueError:
        return None
    return hours if 0 < hours <= MAX_PERIOD_HOURS else None


async def find_scheduled_rule(ctx: CommandContext, name: str) -> UserDefinedRule | None:
    return next(
        (r for r in await list_scheduled_rules(ctx.hctx.pool) if r.name == name), None
    )


def channel_link(channel: str | None) -> str:
    return f"<#{channel}>" if channel else "(owner DM)"
