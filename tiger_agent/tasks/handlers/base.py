"""Base task handler dispatch interface.

TaskHandler and TaskProcessor define the dispatch interface. Each concrete
handler receives hctx and agent via constructor injection and only needs
the task in its handle() method.
"""

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

import logfire
from pydantic_ai import ModelHTTPError, UsageLimitExceeded

from tiger_agent.agent.constants import USER_DEFINED_EVENTS_ENABLED
from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.salesforce.types import (
    SalesforceBaseEvent,
    UserDefinedRuleMatch,
)
from tiger_agent.slack.utils import add_reaction, post_response
from tiger_agent.tasks.types import Task
from tiger_agent.tasks.user_defined_rules import evaluate_user_defined_rules
from tiger_agent.types import HarnessContext

logger = logging.getLogger(__name__)

# Providers word this differently ("prompt is too long: 1002326 tokens >
# 1000000 maximum" from Bedrock, "maximum context length" from others), and
# ModelHTTPError also covers transient 400s that SHOULD be retried -- so match
# on the message rather than the exception type alone.
_CONTEXT_OVERFLOW_MARKERS = (
    "prompt is too long",
    "maximum context length",
    "context_length_exceeded",
)


def _is_context_overflow(error: ModelHTTPError) -> bool:
    """True when the request failed because the prompt did not fit."""
    return any(marker in str(error).lower() for marker in _CONTEXT_OVERFLOW_MARKERS)


class TaskHandler(ABC):
    """Abstract base class for event handlers registered with TaskProcessor.

    Subclasses declare the event types they handle via ``EVENT_TYPES`` so the
    app can auto-register them without hard-coding the mapping.
    """

    EVENT_TYPES: ClassVar[list[type]]

    EVALUATES_USER_DEFINED_RULES: ClassVar[bool] = True
    """Whether user-defined rules should be judged after this handler runs.

    Rule evaluation costs an LLM call per candidate rule. Handlers that only
    mirror data mechanically -- and never invoke the agent themselves -- have
    nothing a rule could usefully match on, so they opt out.
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        self._hctx = hctx
        self._agent = agent

    @abstractmethod
    async def handle(self, task: Task) -> None: ...


class TaskProcessor:
    """Routes tasks to registered handlers by event type.

    Register a TaskHandler instance for each event type. When a task is
    processed, the processor dispatches to the matching handler and wraps
    the call with error handling and Slack feedback for non-Salesforce events.
    """

    def __init__(self, hctx: HarnessContext, agent: TigerAgent) -> None:
        self._hctx = hctx
        self._agent = agent
        self._handlers: dict[type, TaskHandler] = {}

    def register(self, event_types: type | list[type], handler: TaskHandler) -> None:
        if isinstance(event_types, list):
            for event_type in event_types:
                self._handlers[event_type] = handler
        else:
            self._handlers[event_types] = handler

    async def __call__(self, hctx: HarnessContext, task: Task) -> None:
        event = task.event
        handler = self._handlers.get(type(event))
        if handler is None:
            logfire.warn(
                "No handler registered for event type",
                event_type=type(event).__name__,
            )
            return
        try:
            await handler.handle(task)
        except UsageLimitExceeded as e:
            # The request was too large to answer within the agent's usage
            # limits (e.g. too many lookups in one message). Retrying the same
            # task would just hit the limit again, so ack it and tell the user
            # to split the request instead of requeueing.
            logger.warning("handler hit usage limit", exc_info=e)
            if not isinstance(event, (SalesforceBaseEvent, UserDefinedRuleMatch)):
                await add_reaction(hctx.app.client, event.channel, event.ts, "x")
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=event.thread_ts if event.thread_ts else event.ts,
                    text="That request is too large for me to handle in one go. Please split it into smaller batches (for example, fewer items per message) and try again.",
                )
            return
        except ModelHTTPError as e:
            if not _is_context_overflow(e):
                raise
            # The prompt exceeded the model's context window. Rebuilding it from
            # the same inputs produces the same oversized prompt, so requeueing
            # just burns the attempt budget at full cost. Ack it instead.
            logger.warning("handler exceeded the model context window", exc_info=e)
            logfire.warn(
                "Context window exceeded; not retrying",
                event_type=type(event).__name__,
            )
            if not isinstance(event, (SalesforceBaseEvent, UserDefinedRuleMatch)):
                await add_reaction(hctx.app.client, event.channel, event.ts, "x")
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=event.thread_ts if event.thread_ts else event.ts,
                    text="That request grew too large for me to finish. Please split it into smaller batches and try again.",
                )
            return
        except Exception as e:
            logger.exception("handler failed", exc_info=e)
            if not isinstance(event, (SalesforceBaseEvent, UserDefinedRuleMatch)):
                await add_reaction(hctx.app.client, event.channel, event.ts, "x")
                await post_response(
                    client=hctx.app.client,
                    channel=event.channel,
                    thread_ts=event.thread_ts if event.thread_ts else event.ts,
                    text="I experienced an issue trying to respond. I will try again."
                    if task.attempts < self._agent.max_attempts
                    else "I give up. Sorry.",
                )
            raise

        # skip rule evaluation for match events themselves to avoid loops
        if (
            USER_DEFINED_EVENTS_ENABLED
            and handler.EVALUATES_USER_DEFINED_RULES
            and not isinstance(event, UserDefinedRuleMatch)
        ):
            await evaluate_user_defined_rules(
                pool=hctx.pool,
                event_type=event.type,
                event_dict=task.event.model_dump(),
            )
