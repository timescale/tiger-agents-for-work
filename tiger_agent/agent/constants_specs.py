import importlib

import pytest

from tiger_agent.agent import constants


class TestDefaultsAreUnchanged:
    """This move must not alter any value that was previously hardcoded."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("AGENT_MAX_REQUESTS", 150),
            ("AGENT_MAX_OUTPUT_TOKENS", 40_000),
            ("AGENT_MAX_CONTEXT_TOKENS", 800_000),
            ("AGENT_MAX_TOOL_OUTPUT_TOKENS", 50_000),
            ("AGENT_SOFT_TOTAL_TOKENS", 8_000_000),
            ("MAX_TOOL_RESULT_CHARS", 200_000),
            ("MAX_IDENTICAL_TOOL_CALLS", 2),
            ("FINALIZE_MAX_REQUESTS", 2),
            ("SPAM_DETECTION_MAX_REQUESTS", 2),
            ("SPAM_DETECTION_MAX_OUTPUT_TOKENS", 2_000),
            ("AGENT_CRITICAL_REMAINING_REQUESTS", 3),
        ],
    )
    def test_default(self, name, expected):
        assert getattr(constants, name) == expected

    def test_warning_threshold_default(self):
        assert constants.AGENT_WARNING_THRESHOLD == 0.7

    def test_spam_limits_are_built_from_the_constants(self):
        assert constants.SPAM_DETECTION_USAGE_LIMITS.request_limit == (
            constants.SPAM_DETECTION_MAX_REQUESTS
        )
        assert constants.SPAM_DETECTION_USAGE_LIMITS.output_tokens_limit == (
            constants.SPAM_DETECTION_MAX_OUTPUT_TOKENS
        )


class TestOverridesReachTheLimits:
    """Constants are read at import, so an override has to survive into the
    UsageLimits objects the agents actually run under."""

    def test_request_limit_is_overridable(self, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_REQUESTS", "60")
        try:
            reloaded = importlib.reload(constants)
            limits = importlib.reload(
                importlib.import_module("tiger_agent.agent.limits")
            )
            assert reloaded.AGENT_MAX_REQUESTS == 60
            assert limits.AGENT_USAGE_LIMITS.request_limit == 60
        finally:
            monkeypatch.delenv("AGENT_MAX_REQUESTS")
            importlib.reload(constants)
            importlib.reload(importlib.import_module("tiger_agent.agent.limits"))

    def test_warner_threshold_is_overridable(self, monkeypatch):
        monkeypatch.setenv("AGENT_WARNING_THRESHOLD", "0.55")
        try:
            importlib.reload(constants)
            limits = importlib.reload(
                importlib.import_module("tiger_agent.agent.limits")
            )
            assert limits.make_limit_warner().warning_threshold == 0.55
        finally:
            monkeypatch.delenv("AGENT_WARNING_THRESHOLD")
            importlib.reload(constants)
            importlib.reload(importlib.import_module("tiger_agent.agent.limits"))
