import os

CONFIRM_PROACTIVE_PROMPT = "confirm_proactive_prompt"
REJECT_PROACTIVE_PROMPT = "reject_proactive_prompt"
PG_MAX_POOL_SIZE: int = int(os.getenv("PG_MAX_POOL_SIZE", "10"))
