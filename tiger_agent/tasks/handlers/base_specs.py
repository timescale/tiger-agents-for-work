from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import ModelHTTPError, UsageLimitExceeded

from tiger_agent.tasks.handlers.base import (
    TaskHandler,
    TaskProcessor,
    _is_context_overflow,
)

BEDROCK_OVERFLOW = (
    "status_code: 400, model_name: anthropic/claude-opus-4-8, body: "
    "{'message': 'Provider returned error', 'code': 400, 'metadata': "
    "{'raw': '{\"message\":\"prompt is too long: 1002326 tokens > 1000000 maximum\"}'}}"
)


class _Event:
    """A user-facing event, so the notification branches are exercised."""

    type = "app_mention"
    channel = "C123"
    ts = "1.0"
    thread_ts = None

    def model_dump(self) -> dict:
        return {"type": self.type, "subtype": "test"}


@pytest.fixture(autouse=True)
def stub_slack_notifications():
    """These paths post to Slack; the logic under test is the control flow."""
    with (
        patch("tiger_agent.tasks.handlers.base.add_reaction", new=AsyncMock()),
        patch("tiger_agent.tasks.handlers.base.post_response", new=AsyncMock()),
    ):
        yield


def _processor(handler_exc=None, evaluates_rules=True):
    hctx = MagicMock()
    hctx.app.client = MagicMock()
    hctx.pool = MagicMock()

    agent = MagicMock()
    agent.max_attempts = 3

    class Handler(TaskHandler):
        EVENT_TYPES = [_Event]
        EVALUATES_USER_DEFINED_RULES = evaluates_rules

        async def handle(self, task):
            if handler_exc is not None:
                raise handler_exc

    processor = TaskProcessor(hctx, agent)
    processor.register(_Event, Handler(hctx, agent))
    return processor, hctx


def _task():
    task = MagicMock()
    task.event = _Event()
    task.attempts = 1
    return task


class TestContextOverflowDetection:
    def test_recognises_the_bedrock_wording(self):
        assert _is_context_overflow(ModelHTTPError(400, "m", body=BEDROCK_OVERFLOW))

    @pytest.mark.parametrize(
        "body",
        ["maximum context length is 200000 tokens", "context_length_exceeded"],
    )
    def test_recognises_other_provider_wordings(self, body):
        assert _is_context_overflow(ModelHTTPError(400, "m", body=body))

    def test_does_not_match_an_unrelated_400(self):
        """ModelHTTPError also covers transient 400s that should still retry."""
        err = ModelHTTPError(400, "m", body="{'message': 'Could not process image'}")

        assert not _is_context_overflow(err)


class TestOverflowIsNotRequeued:
    async def test_overflow_is_acked_rather_than_raised(self):
        """Rebuilding the same prompt overflows again -- retrying burns budget."""
        exc = ModelHTTPError(400, "m", body=BEDROCK_OVERFLOW)
        processor, hctx = _processor(handler_exc=exc)

        await processor(hctx, _task())  # must not raise

    async def test_an_unrelated_model_error_still_requeues(self):
        exc = ModelHTTPError(400, "m", body="{'message': 'Could not process image'}")
        processor, hctx = _processor(handler_exc=exc)

        with pytest.raises(ModelHTTPError):
            await processor(hctx, _task())

    async def test_other_failures_still_requeue(self):
        processor, hctx = _processor(handler_exc=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await processor(hctx, _task())

    async def test_usage_limit_is_still_acked(self):
        processor, hctx = _processor(handler_exc=UsageLimitExceeded("too much"))

        await processor(hctx, _task())  # must not raise


class TestRuleEvaluationGate:
    @patch(
        "tiger_agent.tasks.handlers.base.evaluate_user_defined_rules", new=AsyncMock()
    )
    @patch("tiger_agent.tasks.handlers.base.USER_DEFINED_EVENTS_ENABLED", True)
    async def test_runs_for_a_normal_handler(self):
        from tiger_agent.tasks.handlers import base

        processor, hctx = _processor(evaluates_rules=True)
        await processor(hctx, _task())

        base.evaluate_user_defined_rules.assert_awaited_once()

    @patch(
        "tiger_agent.tasks.handlers.base.evaluate_user_defined_rules", new=AsyncMock()
    )
    @patch("tiger_agent.tasks.handlers.base.USER_DEFINED_EVENTS_ENABLED", True)
    async def test_skipped_when_the_handler_opts_out(self):
        """A mechanical mirror has nothing a rule could usefully match on."""
        from tiger_agent.tasks.handlers import base

        processor, hctx = _processor(evaluates_rules=False)
        await processor(hctx, _task())

        base.evaluate_user_defined_rules.assert_not_awaited()

    def test_the_feed_item_handler_opts_out(self):
        from tiger_agent.tasks.handlers.salesforce_feed_item import (
            SalesforceFeedItemHandler,
        )

        assert SalesforceFeedItemHandler.EVALUATES_USER_DEFINED_RULES is False

    def test_handlers_evaluate_rules_by_default(self):
        from tiger_agent.tasks.handlers.slack import SlackTaskHandler

        assert SlackTaskHandler.EVALUATES_USER_DEFINED_RULES is True
