from tiger_agent.db.utils import delete_events_matching, toggle_user_defined_rule
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules.common import (
    find_scheduled_rule,
)
from tiger_agent.tasks.handlers.user_defined_rule_execution import schedule_match


async def handle(ctx: CommandContext, args: list[str]) -> str:
    rule = await find_scheduled_rule(ctx, args[0])
    if rule is None:
        return f"No scheduled rule named `{args[0]}`."

    removed = await delete_events_matching(
        pool=ctx.hctx.pool, match=schedule_match(rule.id)
    )
    # Disabling as well means a run that is already in flight will not re-arm.
    await toggle_user_defined_rule(
        pool=ctx.hctx.pool,
        rule_id=rule.id,
        owner_slack_id=ctx.command.user_id or "",
        enabled=False,
    )
    return (
        f"Removed {removed} queued run(s) of `{rule.name}` and disabled the rule. "
        "A run already in progress will finish but not reschedule."
    )
