"""Tunable constants for the agents.

Every limit the agents run under lives here, and every one takes an environment
override of the same name. They were previously spread across
``agent/limits.py``, ``agent/utils.py`` and the top-level ``utils.py``, several
hardcoded at their use site -- which made the relationships between them
invisible, and made it easy to change one without noticing the others that
have to move with it.
"""

import os

from pydantic_ai import UsageLimits

# NOTE: deliberately left as a raw environ read. It is a bare truthiness check,
# so the string "false" currently enables the feature -- correcting that would
# flip behaviour in any environment that sets it, which belongs in its own
# change rather than riding along with a constants move.
USER_DEFINED_EVENTS_ENABLED = os.environ.get("USER_DEFINED_EVENTS_ENABLED", False)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

# Condenses a newly created case into a one-line description for the Slack post.
CASE_SUMMARY_MODEL = os.environ.get(
    "CASE_SUMMARY_MODEL", "openrouter:anthropic/claude-sonnet-5"
)

# Spam triage. Deliberately not the cheapest available model: a false positive
# sets Status/Type to Spam on a real customer's case, which nobody is watching
# for. The whole path costs a couple of dollars a year, so the saving from a
# smaller model does not justify the extra misclassification risk.
SPAM_DETECTION_MODEL = os.environ.get(
    "SPAM_DETECTION_MODEL", "openrouter:anthropic/claude-sonnet-5"
)


# --------------------------------------------------------------------------
# Main agent run budget
#
# These interact, and the ordering matters:
#
#   0.7 x CONTEXT       warner starts telling the model to converge
#   0.9 x CONTEXT       ContextManagerCapability attempts compaction
#   AGENT_MAX_REQUESTS  hard stop on turn count
# --------------------------------------------------------------------------

AGENT_MAX_REQUESTS: int = int(os.getenv("AGENT_MAX_REQUESTS", "150"))
AGENT_MAX_OUTPUT_TOKENS: int = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "40000"))

# The budget ContextManagerCapability manages against; compaction fires at
# 0.9x this.
AGENT_MAX_CONTEXT_TOKENS: int = int(os.getenv("AGENT_MAX_CONTEXT_TOKENS", "800000"))

# Cap on a single tool result before it enters the model's context.
AGENT_MAX_TOOL_OUTPUT_TOKENS: int = int(
    os.getenv("AGENT_MAX_TOOL_OUTPUT_TOKENS", "50000")
)

# The cumulative budget the warner counts down against. Advisory: nothing
# enforces it today, so a run that passes it is warned but not stopped. Sized
# above the observed p75 for the most expensive handler
# (SalesforceAssignmentChanged, p75 ~6.1M input tokens over a 5 day sample) so
# routine work never sees a warning.
AGENT_SOFT_TOTAL_TOKENS: int = int(os.getenv("AGENT_SOFT_TOTAL_TOKENS", "8000000"))


# --------------------------------------------------------------------------
# Warner
#
# These numbers are interpolated verbatim into the message the model reads
# ("Context window: 612345/800000 tokens used"), so whatever is configured here
# is what the model is told.
# --------------------------------------------------------------------------

AGENT_WARNING_THRESHOLD: float = float(os.getenv("AGENT_WARNING_THRESHOLD", "0.7"))
AGENT_CRITICAL_REMAINING_REQUESTS: int = int(
    os.getenv("AGENT_CRITICAL_REMAINING_REQUESTS", "3")
)


# --------------------------------------------------------------------------
# Tool-call guards
# --------------------------------------------------------------------------

# One tool result is truncated past this, so an unbounded payload cannot
# swallow the context window.
MAX_TOOL_RESULT_CHARS: int = int(os.getenv("MAX_TOOL_RESULT_CHARS", "200000"))

# How many times one exact (tool, arguments) pair may run in a single agent run
# before further attempts are refused. Two is a legitimate retry; a third is a
# loop.
MAX_IDENTICAL_TOOL_CALLS: int = int(os.getenv("MAX_IDENTICAL_TOOL_CALLS", "2"))


# --------------------------------------------------------------------------
# Derived budgets
# --------------------------------------------------------------------------

# The fallback call makes exactly one tool-free request, so it needs a budget
# of its own -- the run's original limits are exhausted by definition.
FINALIZE_MAX_REQUESTS: int = int(os.getenv("FINALIZE_MAX_REQUESTS", "2"))

# Spam triage reads the case text; it does not investigate it. A tool-free
# agent keeps a junk case from costing a full investigation.
SPAM_DETECTION_MAX_REQUESTS: int = int(os.getenv("SPAM_DETECTION_MAX_REQUESTS", "2"))
SPAM_DETECTION_MAX_OUTPUT_TOKENS: int = int(
    os.getenv("SPAM_DETECTION_MAX_OUTPUT_TOKENS", "2000")
)

SPAM_DETECTION_USAGE_LIMITS = UsageLimits(
    request_limit=SPAM_DETECTION_MAX_REQUESTS,
    output_tokens_limit=SPAM_DETECTION_MAX_OUTPUT_TOKENS,
)
