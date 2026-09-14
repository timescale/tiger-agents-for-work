import os
from typing import Any

from pydantic_ai import UsageLimits

USER_DEFINED_EVENTS_ENABLED = os.environ.get("USER_DEFINED_EVENTS_ENABLED", False)


CASE_SUMMARY_MODEL = os.environ.get(
    "CASE_SUMMARY_MODEL", "openrouter:anthropic/claude-sonnet-5"
)

SPAM_DETECTION_MODEL = os.environ.get(
    "SPAM_DETECTION_MODEL", "openrouter:anthropic/claude-sonnet-5"
)


AGENT_MAX_REQUESTS: int = int(os.getenv("AGENT_MAX_REQUESTS", "150"))
AGENT_MAX_OUTPUT_TOKENS: int = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "40000"))

# Each delegation runs on its own AGENT_MAX_REQUESTS budget, so this is what bounds
# the tree: without it a parent could spend AGENT_MAX_REQUESTS per delegation without
# limit.
AGENT_MAX_DELEGATIONS: int = int(os.getenv("AGENT_MAX_DELEGATIONS", "12"))

AGENT_MAX_REQUEST_INPUT_TOKENS: int = int(
    os.getenv("AGENT_MAX_REQUEST_INPUT_TOKENS", "850000")
)

AGENT_MAX_CONTEXT_TOKENS: int = int(os.getenv("AGENT_MAX_CONTEXT_TOKENS", "800000"))

AGENT_MAX_TOOL_OUTPUT_TOKENS: int = int(
    os.getenv("AGENT_MAX_TOOL_OUTPUT_TOKENS", "50000")
)
AGENT_SOFT_TOTAL_TOKENS: int = int(os.getenv("AGENT_SOFT_TOTAL_TOKENS", "8000000"))


AGENT_WARNING_THRESHOLD: float = float(os.getenv("AGENT_WARNING_THRESHOLD", "0.7"))
AGENT_CRITICAL_REMAINING_REQUESTS: int = int(
    os.getenv("AGENT_CRITICAL_REMAINING_REQUESTS", "3")
)


# One tool result is truncated past this, so an unbounded payload cannot
# swallow the context window.
MAX_TOOL_RESULT_CHARS: int = int(os.getenv("MAX_TOOL_RESULT_CHARS", "200000"))

# How many times one exact (tool, arguments) pair may run in a single agent run
# before further attempts are refused. Two is a legitimate retry; a third is a
# loop.
MAX_IDENTICAL_TOOL_CALLS: int = int(os.getenv("MAX_IDENTICAL_TOOL_CALLS", "2"))


FINALIZE_MAX_REQUESTS: int = int(os.getenv("FINALIZE_MAX_REQUESTS", "2"))

SPAM_DETECTION_MAX_REQUESTS: int = int(os.getenv("SPAM_DETECTION_MAX_REQUESTS", "4"))
SPAM_DETECTION_MAX_OUTPUT_TOKENS: int = int(
    os.getenv("SPAM_DETECTION_MAX_OUTPUT_TOKENS", "2000")
)

SPAM_DETECTION_USAGE_LIMITS = UsageLimits(
    request_limit=SPAM_DETECTION_MAX_REQUESTS,
    output_tokens_limit=SPAM_DETECTION_MAX_OUTPUT_TOKENS,
)


# Only the settings matching the active model's provider prefix are read; the rest are
# ignored, so it's safe to set both Anthropic's and OpenRouter's cache keys regardless of
# whether `model` is an `anthropic:` or `openrouter:` model string.
#
# Caching instructions and tool definitions only ever covers a fixed-size prefix, so its
# value decays as an agent loop grows -- on SalesforceAssignmentChanged that was a ~21.5K
# cached block against requests averaging 137K. Also caching the conversation makes the
# cached prefix grow with the run, so each turn pays full price for the new tool result
# rather than for the whole history again.
#
# Anthropic gets `anthropic_cache` rather than `anthropic_cache_messages`: it is the
# native form, where the server moves the breakpoint forward on its own. The `_messages`
# variant is the fallback for gateways that cannot pass the top-level parameter, and the
# two are mutually exclusive. OpenRouter has no automatic equivalent, so it takes the
# explicit per-message breakpoint.
PROMPT_CACHE_MODEL_SETTINGS: dict[str, Any] = {
    "anthropic_cache_instructions": True,
    "anthropic_cache_tool_definitions": True,
    "anthropic_cache": True,
    "openrouter_cache_instructions": True,
    "openrouter_cache_tool_definitions": True,
    "openrouter_cache_messages": True,
}
