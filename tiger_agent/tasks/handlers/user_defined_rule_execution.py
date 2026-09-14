from datetime import UTC, datetime
from typing import cast

import logfire

from tiger_agent.agent.limits import run_and_return_partial
from tiger_agent.agent.types import AssessmentReport
from tiger_agent.agent.utils import create_agent_and_context
from tiger_agent.db.utils import get_user_defined_rule, insert_event_if_absent
from tiger_agent.salesforce.types import (
    FileAttachment,
    UserDefinedRule,
    UserDefinedRuleExecution,
)
from tiger_agent.slack.utils import post_response, publish_canvas_in_thread
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


def schedule_match(rule_id: int) -> dict:
    """The payload subset that identifies a queued run of one scheduled rule."""
    return {
        "type": UserDefinedRuleExecution.model_fields["type"].default,
        "rule_id": rule_id,
        "trigger": "schedule",
    }


class UserDefinedRuleExecutionHandler(TaskHandler):
    """Runs a rule's action_prompt through the full agent.

    A scheduled run re-arms itself before doing anything else, so a failing run
    cannot end the schedule; a run whose rule is gone or disabled does nothing,
    which is how `schedules remove` stops a schedule. Failures are reported to
    the destination once rather than re-raised: a queue retry would re-run the
    whole LLM job.
    """

    EVENT_TYPES = [UserDefinedRuleExecution]

    @logfire.instrument("UserDefinedRuleExecutionHandler.handle", extract_args=False)
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: UserDefinedRuleExecution = task.event

        rule = await get_user_defined_rule(pool=hctx.pool, rule_id=event.rule_id)
        if rule is None or not rule.enabled:
            logfire.info(
                "Rule missing or disabled; not running",
                rule_id=event.rule_id,
                rule_name=event.rule_name,
            )
            return

        if (
            event.trigger == "schedule"
            and event.reschedule
            and rule.repeat
            and rule.period
        ):
            await self._schedule_next(task=task, event=event, rule=rule)

        destination = rule.channel or event.channel or rule.owner_slack_id

        try:
            agent_and_ctx = await create_agent_and_context(
                hctx=hctx,
                task=task,
                agent=self._agent,
                channel_to_respond=destination,
                profile=rule.execution_profile,
                extra_context={"rule": rule},
            )
            response = await run_and_return_partial(
                agent_and_ctx.agent,
                user_prompt=agent_and_ctx.user_prompt,
                deps=agent_and_ctx.ctx,
                usage_limits=agent_and_ctx.usage_limits,
            )
            if event.trigger == "schedule":
                await self._deliver_report(
                    channel=destination,
                    rule=rule,
                    report=cast(AssessmentReport, response.output),
                )
            elif response.output:
                await post_response(
                    client=hctx.app.client,
                    channel=destination,
                    thread_ts=None,
                    text=str(response.output),
                )
        except Exception as e:
            logfire.error(
                "Rule execution failed",
                rule_id=rule.id,
                rule_name=rule.name,
                exc_info=e,
            )
            await post_response(
                client=hctx.app.client,
                channel=destination,
                thread_ts=None,
                text=f"Rule `{rule.name}` failed to run: {type(e).__name__}: {e}",
            )

    async def _schedule_next(
        self, task: Task, event: UserDefinedRuleExecution, rule: UserDefinedRule
    ) -> None:
        assert rule.period is not None
        next_event = event.model_copy(
            update={
                "channel": rule.channel or event.channel,
                "execution_profile": rule.execution_profile,
                "window_hours": rule.period.total_seconds() / 3600,
                "reschedule": True,
            }
        )
        inserted = await insert_event_if_absent(
            pool=self._hctx.pool,
            event=next_event.model_dump(),
            vt=datetime.now(UTC) + rule.period,
            match=schedule_match(rule.id),
            exclude_id=task.id,
        )
        logfire.info(
            "Scheduled next rule run",
            rule_id=rule.id,
            rule_name=rule.name,
            inserted=inserted,
        )

    async def _deliver_report(
        self, channel: str, rule: UserDefinedRule, report: AssessmentReport
    ) -> None:
        client = self._hctx.app.client
        header = await post_response(
            client=client, channel=channel, thread_ts=None, text=report.summary
        )
        thread_ts = header.data.get("ts") if header else None
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        title = f"{rule.name} — {stamp}"
        try:
            await publish_canvas_in_thread(
                client=client,
                channel=channel,
                thread_ts=thread_ts,
                title=title,
                markdown=report.report_markdown,
            )
        except Exception as e:
            logfire.warn(
                "Canvas publish failed; attaching the report as a file instead",
                rule_id=rule.id,
                exc_info=e,
            )
            await post_response(
                client=client,
                channel=channel,
                thread_ts=thread_ts,
                text="Full report attached.",
                file_attachments=[
                    FileAttachment(
                        name=f"{rule.name}-{stamp}.md",
                        body=report.report_markdown.encode(),
                        content_type="text/markdown",
                    )
                ],
            )
