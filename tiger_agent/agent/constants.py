import os

from pydantic_ai import UsageLimits

USER_DEFINED_EVENTS_ENABLED = os.environ.get("USER_DEFINED_EVENTS_ENABLED", False)

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

# Spam triage reads the case text; it does not investigate it. A tool-free
# agent keeps a junk case from costing a full investigation.
SPAM_DETECTION_USAGE_LIMITS = UsageLimits(
    request_limit=2,
    output_tokens_limit=2_000,
)
