from tiger_agent.db.utils import list_events_matching, list_scheduled_rules
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules.common import channel_link
from tiger_agent.tasks.handlers.user_defined_rule_execution import schedule_match


async def handle(ctx: CommandContext, _args: list[str]) -> str:
    rules = await list_scheduled_rules(ctx.hctx.pool)
    if not rules:
        return "No scheduled rules."

    lines = []
    for rule in rules:
        every = (
            f"every {rule.period.total_seconds() / 3600:g}h"
            if rule.period
            else "on demand"
        )
        state = "" if rule.enabled else " (disabled)"
        queued = await list_events_matching(ctx.hctx.pool, schedule_match(rule.id))
        if queued:
            runs = ", ".join(
                f"<!date^{int(e.vt.timestamp())}^{{date_short_pretty}} at {{time}}|{e.vt:%Y-%m-%d %H:%M UTC}>"
                + (f" (attempt {e.attempts}, running)" if e.attempts else "")
                for e in queued
            )
            next_run = f"next: {runs}"
        else:
            next_run = "not queued"
        lines.append(
            f"• `{rule.name}`{state} — {every} → {channel_link(rule.channel)} — {next_run}"
        )
    return "\n".join(lines)
