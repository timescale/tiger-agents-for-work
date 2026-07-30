import datetime as dt
from typing import Literal

from pydantic import BaseModel

type CalendarEventType = Literal["first_day", "pto", "payday"]


class CalenderEvent(BaseModel):
    model_config = {"extra": "allow"}

    summary: str
    start: dt.datetime
    end: dt.datetime
    type: CalendarEventType
