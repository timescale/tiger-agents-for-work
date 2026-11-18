import os

# Read-only access to the internal docs indexed in Memory Engine. The tools are
# only offered to the agent when ME_API_KEY is set. There is deliberately no
# space setting: the caller (the skill) names the space on every call.
ME_API_KEY = os.environ.get("ME_API_KEY", "").strip()
ME_SERVER = (
    (os.environ.get("ME_SERVER") or "https://api.memory.build").strip().rstrip("/")
)
ME_TIMEOUT_SECONDS = float(os.environ.get("ME_TIMEOUT_SECONDS", 30))

ME_SEARCH_DEFAULT_LIMIT = 10
ME_SEARCH_MAX_LIMIT = 25

# One doc can be tens of thousands of characters. Bodies are cut here so a single
# read cannot swallow the context window; a `content:M:N` select reads the rest.
ME_MAX_CONTENT_CHARS = 40_000
