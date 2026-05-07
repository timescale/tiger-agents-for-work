"""Custom rule evaluation and dispatch.

After a task is handled, evaluate_custom_rules() queries for matching rules,
uses an LLM as a judge per rule, and enqueues a CustomRuleMatchEvent for
any that match. The match event is then picked up by the normal task queue and
processed by CustomRuleMatchHandler via the full TigerAgent.
"""

import json

import logfire
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from pydantic_ai import Agent

from tiger_agent.db.utils import get_matching_custom_rules, insert_event
from tiger_agent.salesforce.types import CustomRule, CustomRuleMatchEvent

JUDGE_MODEL = "anthropic:claude-haiku-4-5"


class JudgeResult(BaseModel):
    matches: bool
    reason: str


async def _evaluate_event_criteria(rule: CustomRule, event_dict: dict) -> JudgeResult:
    judge = Agent(
        model=JUDGE_MODEL,
        output_type=JudgeResult,
        system_prompt=(
            "You are an event classifier. Given an event payload and a criteria description, "
            "determine whether the event satisfies the criteria. "
            "Return matches=true only if you are confident the criteria is met."
        ),
    )
    result = await judge.run(
        f"Criteria: {rule.criteria}\n\nEvent:\n{json.dumps(event_dict, indent=2, default=str)}"
    )
    return result.output


@logfire.instrument("evaluate_custom_rules", extract_args=False)
async def evaluate_custom_rules(
    pool: AsyncConnectionPool,
    event_type: str,
    event_dict: dict,
) -> None:
    """Evaluate all enabled custom rules for the given event type.

    For each matching rule, enqueues a CustomRuleMatchEvent to be processed
    by the task queue.
    """
    rules = await get_matching_custom_rules(pool, event_type)
    if not rules:
        return

    for rule in rules:
        try:
            result = await _evaluate_event_criteria(rule, event_dict)
        except Exception as e:
            logfire.error(
                "Failed to evaluate event",
                rule_id=rule.id,
                rule_name=rule.name,
                exc_info=e,
            )
            continue

        if not result.matches:
            logfire.info(
                "Custom rule did not match",
                rule_id=rule.id,
                rule_name=rule.name,
                reason=result.reason,
            )
            continue

        logfire.info(
            "Custom rule matched, enqueueing",
            rule_id=rule.id,
            rule_name=rule.name,
            reason=result.reason,
        )

        match_event = CustomRuleMatchEvent(
            rule_id=rule.id,
            rule_name=rule.name,
            owner_slack_id=rule.owner_slack_id,
            action_prompt=rule.action_prompt,
            matched_event=event_dict,
            match_reason=result.reason,
        )
        await insert_event(pool=pool, event=match_event.model_dump())
