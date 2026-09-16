from datetime import UTC, datetime, timedelta

import logfire

from tiger_agent.db.utils import (
    delete_events_matching,
    insert_event,
    upsert_scheduled_rule,
)
from tiger_agent.salesforce.types import UserDefinedRuleExecution
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules.common import (
    MAX_PERIOD_HOURS,
    SKILL_NAME_RE,
    channel_link,
    parse_hours,
    resolve_channel,
    scheduled_action_prompt,
)
from tiger_agent.tasks.handlers.user_defined_rule_execution import schedule_match


async def handle(ctx: CommandContext, args: list[str]) -> str:
    skill_name = args[0]
    if not SKILL_NAME_RE.match(skill_name):
        return "The first argument must be a skill name (lowercase letters, digits, hyphens)."

    hours = parse_hours(args[1])
    if hours is None:
        return f"Hours must be a number between 0 and {MAX_PERIOD_HOURS:g}."

    channel = resolve_channel(ctx, args[2] if len(args) > 2 else None)
    if channel is None:
        return "Could not determine the channel; pass a channel id or #channel."

    period = timedelta(hours=hours)
    rule = await upsert_scheduled_rule(
        pool=ctx.hctx.pool,
        name=skill_name,
        owner_slack_id=ctx.command.user_id or "",
        action_prompt=scheduled_action_prompt(skill_name),
        period=period,
        channel=channel,
    )

    # Re-arming replaces whatever run was queued; a changed interval takes
    # effect immediately rather than after the old one fires.
    await delete_events_matching(pool=ctx.hctx.pool, match=schedule_match(rule.id))
    next_run = datetime.now(UTC) + period
    await insert_event(
        pool=ctx.hctx.pool,
        event=UserDefinedRuleExecution(
            rule_id=rule.id,
            rule_name=rule.name,
            owner_slack_id=rule.owner_slack_id,
            trigger="schedule",
            channel=channel,
            execution_profile=rule.execution_profile,
            window_hours=hours,
        ).model_dump(),
        vt=next_run,
    )
    logfire.info("Scheduled rule armed", rule_id=rule.id, name=rule.name, hours=hours)

    return (
        f"Scheduled `{skill_name}` every {hours:g}h, posting to {channel_link(channel)}. "
        f"Next run <!date^{int(next_run.timestamp())}^{{date_short_pretty}} at {{time}}|{next_run:%Y-%m-%d %H:%M UTC}>."
    )
