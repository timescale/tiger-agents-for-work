from tiger_agent.db.utils import insert_event, upsert_scheduled_rule
from tiger_agent.salesforce.types import UserDefinedRuleExecution
from tiger_agent.slack.slash_commands.base import CommandContext
from tiger_agent.slack.slash_commands.handlers.schedules.common import (
    SKILL_NAME_RE,
    channel_link,
    find_scheduled_rule,
    parse_channel,
    scheduled_action_prompt,
)

DEFAULT_WINDOW_HOURS = 168.0


async def handle(ctx: CommandContext, args: list[str]) -> str:
    skill_name = args[0]
    if not SKILL_NAME_RE.match(skill_name):
        return "The first argument must be a skill name (lowercase letters, digits, hyphens)."

    channel_arg = parse_channel(args[1]) if len(args) > 1 else None
    if len(args) > 1 and channel_arg is None:
        return "Could not parse the channel; pass a channel id or #channel."

    rule = await find_scheduled_rule(ctx, skill_name)
    if rule is None:
        channel = channel_arg or ctx.command.channel_id
        if channel is None:
            return "Could not determine the channel; pass a channel id or #channel."
        rule = await upsert_scheduled_rule(
            pool=ctx.hctx.pool,
            name=skill_name,
            owner_slack_id=ctx.command.user_id or "",
            action_prompt=scheduled_action_prompt(skill_name),
            period=None,
            channel=channel,
        )

    channel = channel_arg or rule.channel
    window_hours = (
        rule.period.total_seconds() / 3600 if rule.period else DEFAULT_WINDOW_HOURS
    )
    await insert_event(
        pool=ctx.hctx.pool,
        event=UserDefinedRuleExecution(
            rule_id=rule.id,
            rule_name=rule.name,
            owner_slack_id=rule.owner_slack_id,
            trigger="schedule",
            channel=channel,
            execution_profile=rule.execution_profile,
            window_hours=window_hours,
            reschedule=False,
        ).model_dump(),
    )
    await ctx.hctx.trigger.put(True)
    return (
        f"Running `{rule.name}` now over the last {window_hours:g}h; "
        f"output goes to {channel_link(channel)}."
    )
