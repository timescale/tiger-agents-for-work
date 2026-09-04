"""Tunable constants for the agents.

Every limit the agents run under lives here, and every one takes an environment
override of the same name. They were previously spread across
``agent/limits.py``, ``agent/utils.py`` and the top-level ``utils.py``, which
made it hard to see how they related -- and easy to change one without the
others, as happened when the enforced context ceiling moved but the figure the
warner quotes to the model did not.
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
#   REQUEST_INPUT       run stops with UsageLimitExceeded -> partial answer
#   model window        provider would reject -- never reached
# --------------------------------------------------------------------------

AGENT_MAX_REQUESTS: int = int(os.getenv("AGENT_MAX_REQUESTS", "150"))
AGENT_MAX_OUTPUT_TOKENS: int = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "40000"))

# Stop a run before its context reaches the model's ceiling.
#
# A run was observed dying on "prompt is too long: 1002326 tokens > 1000000
# maximum". That surfaces as a provider 400, which is the worst shape available:
# the oversized request is billed, nothing is salvaged, and because the prompt
# is a deterministic function of the event, every requeue rebuilds it and fails
# identically.
#
# pydantic-ai checks this against each response's input tokens as it arrives.
# Context grows monotonically, so catching "this turn was 850K" ends the run
# before the next turn would breach 1M -- and it raises UsageLimitExceeded, so
# the warner already counts down toward it and run_and_return_partial still
# turns it into a partial answer.
#
# Sized to sit above the compaction trigger (0.9 x 800K = 720K) so context
# management gets first attempt, and far enough below a 1M window that one more
# turn cannot clear it: a single tool result is capped at 50K tokens by
# ContextManagerCapability and ~50K by MAX_TOOL_RESULT_CHARS. Only 39 of 20,652
# calls in a six-day sample exceeded 720K at all, so routine work never sees it.
AGENT_MAX_REQUEST_INPUT_TOKENS: int = int(
    os.getenv("AGENT_MAX_REQUEST_INPUT_TOKENS", "850000")
)

# The budget ContextManagerCapability manages against; compaction fires at
# 0.9x this.
AGENT_MAX_CONTEXT_TOKENS: int = int(os.getenv("AGENT_MAX_CONTEXT_TOKENS", "800000"))

# Cap on a single tool result before it enters the model's context.
AGENT_MAX_TOOL_OUTPUT_TOKENS: int = int(
    os.getenv("AGENT_MAX_TOOL_OUTPUT_TOKENS", "50000")
)

# Proposed cumulative ceiling, not yet wired to anything. Sized above the
# observed p75 for the most expensive handler (SalesforceAssignmentChanged,
# p75 ~6.1M input tokens over a 5 day sample). Deliberately not given to the
# warner: until something enforces it, counting down against it would be
# telling the model a number that never fires.
AGENT_SOFT_TOTAL_TOKENS: int = int(os.getenv("AGENT_SOFT_TOTAL_TOKENS", "8000000"))


# --------------------------------------------------------------------------
# Warner
#
# These numbers are interpolated verbatim into the message the model reads
# ("Context window: 812345/850000 tokens used"), so each must correspond to a
# limit that is actually enforced.
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
