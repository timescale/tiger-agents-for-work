import asyncio
import datetime as dt
import re

import httpx
import logfire
import recurring_ical_events
from icalendar import Calendar

from tiger_agent.org_calendar.constants import CALENDAR_TTL_SECONDS, ICS_URL
from tiger_agent.org_calendar.types import CalendarEventType, CalenderEvent

_calendar: Calendar | None = None
_fetch_time: dt.datetime | None = None

PAYDAY_REGEX = r"^pay_day_"
FIRSTDAY_REGEX = r"^first_day-"
PTO_REGEX = r"^pto"


def get_type(uid: str) -> CalendarEventType | None:
    if re.match(PAYDAY_REGEX, uid):
        return "payday"
    if re.match(FIRSTDAY_REGEX, uid):
        return "first_day"
    if re.match(PTO_REGEX, uid):
        return "pto"

    return None


async def fetch_calendar(force_refresh: bool = False) -> Calendar | None:
    global _calendar, _fetch_time
    if not ICS_URL:
        logfire.warn("ICS_URL not defined, will not have company calendar")
        return

    if (
        not force_refresh
        and _calendar
        and _fetch_time
        and ((dt.datetime.now() - _fetch_time).total_seconds() < CALENDAR_TTL_SECONDS)
    ):
        return _calendar

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(ICS_URL)
    response.raise_for_status()
    _calendar = Calendar.from_ical(response.content)
    _fetch_time = dt.datetime.now()

    return _calendar


async def get_calender_events(
    start: dt.datetime,
    end: dt.datetime,
    force_refresh: bool = False,
    events_to_filter: list[CalendarEventType] | None = None,
) -> list[CalenderEvent] | str:
    cal = await fetch_calendar(force_refresh=force_refresh)

    if not cal:
        return "The organization calendar feed is not configured, cannot fetch info."

    events = []
    raw_events = recurring_ical_events.of(cal).between(start, end)
    for event in raw_events:
        event_type = get_type(event["UID"])

        if not event_type:
            logfire.warn("Could not detect type", event=event)
            continue

        if not events_to_filter or event_type in events_to_filter:
            events.append(
                CalenderEvent(
                    summary=str(event.summary),
                    start=event.start,
                    end=event.end,
                    type=event_type,
                )
            )
    return events


async def main() -> None:
    events = await get_calender_events(
        dt.datetime(2026, 7, 29), dt.datetime(2026, 8, 1)
    )

    print(events)


if __name__ == "__main__":
    asyncio.run(main())
