"""Time helpers: business hours in Pacific/Auckland, and delays drawn from a stream."""

from datetime import date, datetime, timedelta
from random import Random

from sr_simulator.config import NZ, SimConfig


def seconds(rng: Random, low: float, high: float) -> timedelta:
    return timedelta(seconds=rng.uniform(low, high))


def business_moment(rng: Random, day: date) -> datetime:
    """A random time between 08:00 and 17:30 local time on `day`."""
    minute = rng.randint(8 * 60, 17 * 60 + 29)
    return datetime(
        day.year, day.month, day.day, minute // 60, minute % 60, rng.randint(0, 59), tzinfo=NZ
    )


def next_business_day(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def after_business_days(rng: Random, start: datetime, max_days: int) -> datetime:
    """Later the same day, or during business hours up to `max_days` business days later."""
    days = rng.randint(0, max_days)
    if days == 0:
        return start + timedelta(minutes=rng.randint(5, 240))
    day = start.astimezone(NZ).date()
    for _ in range(days):
        day = next_business_day(day)
    return business_moment(rng, day)


def created_at(rng: Random, config: SimConfig) -> datetime:
    """A business-hours moment within the history window, before the anchor time."""
    anchor_day = config.anchor_time.astimezone(NZ).date()
    day = anchor_day - timedelta(days=rng.randrange(config.history_days))
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    moment = business_moment(rng, day)
    if moment >= config.anchor_time:
        moment = config.anchor_time - timedelta(minutes=rng.randint(1, 90))
    return moment
