"""User-defined rule evaluation and dispatch.

After a task is handled, evaluate_user_defined_rules() queries for matching rules,
uses an LLM as a judge per rule, and enqueues a UserDefinedRuleMatch for
any that match. The match event is then picked up by the normal task queue and
processed by UserDefinedRuleMatchHandler via the full TigerAgent.
"""

import json

import logfire
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from tiger_agent.db.utils import get_matching_user_defined_rules, insert_event
from tiger_agent.events import EVENT_TYPE_REGISTRY
from tiger_agent.salesforce.types import (
    UserDefinedRule,
    UserDefinedRuleMatch,
)

USER_DEFINED_RULE_JUDGE_MODEL = "anthropic:claude-sonnet-4-6"

# Build a lookup from (event_type, event_subtype) -> event_description
_EVENT_DESCRIPTION_BY_TYPE: dict[tuple[str, str | None], str] = {}
for _cls in EVENT_TYPE_REGISTRY:
    _type = _cls.model_fields["type"].default
    _subtype_field = _cls.model_fields.get("subtype")
    _subtype = (
        _subtype_field.default
        if _subtype_field and isinstance(_subtype_field.default, str)
        else None
    )
    _EVENT_DESCRIPTION_BY_TYPE[(_type, _subtype)] = _cls.event_description


class UserDefinedRuleCriteriaMatchResult(BaseModel):
    matches: bool = Field(description="Whether the event satisfies the rule criteria.")
    reason: str = Field(
        description="Explanation of why the criteria matched or did not match."
    )
    suggested_criteria: str | None = Field(
        default=None,
        description="If matches=false, a revised version of the criteria that would have matched this event, preserving the original intent. Null if matches=true.",
    )


@logfire.instrument("_evaluate_event_criteria", extract_args=True)
async def _evaluate_event_criteria(
    rule: UserDefinedRule, event_dict: dict
) -> UserDefinedRuleCriteriaMatchResult:
    event_type = event_dict.get("type")
    event_subtype = (
        event_dict.get("subtype")
        if isinstance(event_dict.get("subtype"), str)
        else None
    )
    event_description = _EVENT_DESCRIPTION_BY_TYPE.get((event_type, event_subtype), "")

    agent = Agent(
        model=USER_DEFINED_RULE_JUDGE_MODEL,
        output_type=UserDefinedRuleCriteriaMatchResult,
        system_prompt=(
            "You are an event classifier. Given an event payload and a criteria description, "
            "determine whether the event satisfies the criteria. "
            "Return matches=true only if you are confident the criteria is met.\n"
            "If matches=false, populate suggested_criteria with a revised version of the criteria "
            "that would have matched this event, preserving the user's original intent. "
            "If matches=true, leave suggested_criteria null."
        ),
    )
    result = await agent.run(
        f"Event type: {event_description}\n\n"
        f"Criteria: {rule.criteria}\n\n"
        f"Event payload:\n{json.dumps(event_dict, indent=2, default=str)}"
    )
    return result.output


@logfire.instrument("evaluate_user_defined_rules", extract_args=False)
async def evaluate_user_defined_rules(
    pool: AsyncConnectionPool,
    event_type: str,
    event_dict: dict,
) -> None:
    """Evaluate all enabled user-defined rules for the given event type and subtype.

    For each matching rule, enqueues a UserDefinedRuleMatch to be processed
    by the task queue.
    """
    event_subtype = event_dict.get("subtype")
    matching_rules = await get_matching_user_defined_rules(
        pool, event_type, event_subtype
    )
    if not matching_rules:
        return

    for rule in matching_rules:
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
            logfire.trace(
                "User-defined rule did not match",
                rule_id=rule.id,
                rule_name=rule.name,
                reason=result.reason,
            )
            continue

        logfire.info(
            "User-defined rule matched, enqueueing",
            rule_id=rule.id,
            rule_name=rule.name,
            reason=result.reason,
        )

        await insert_event(
            pool=pool,
            event=UserDefinedRuleMatch(
                rule_id=rule.id,
                rule_name=rule.name,
                owner_slack_id=rule.owner_slack_id,
                action_prompt=rule.action_prompt,
                matched_event=event_dict,
                match_reason=result.reason,
            ).model_dump(),
        )
