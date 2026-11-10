"""Time helpers. Everything in the tool runs on India time (IST)."""
import time as _time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_fake_now = None  # tests set this to replay a day


def now():
    return _fake_now if _fake_now is not None else datetime.now(IST)


def set_fake(dt):
    global _fake_now
    _fake_now = dt


def sleep(seconds):
    if _fake_now is None and seconds > 0:
        _time.sleep(seconds)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def ts(dt):
    return int(dt.timestamp())


def from_ts(t):
    return datetime.fromtimestamp(t, IST)


def clock_text(dt):
    return dt.strftime("%I:%M %p").lstrip("0").lower()
