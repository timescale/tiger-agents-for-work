import os

ICS_URL = os.environ.get(
    "ICS_URL",
    None,
)
CALENDAR_TTL_SECONDS = int(os.environ.get("CALENDAR_TTL_SECONDS", 600))
