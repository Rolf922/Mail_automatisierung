from __future__ import annotations

from datetime import date, datetime, time, timedelta


def period_start(day: date) -> date:
    """Return the Monday of the ISO week containing *day*."""
    return day - timedelta(days=day.weekday())


def is_weekly_run_due(
    now: datetime,
    scheduled_weekday: int,
    scheduled_time: time,
    last_completed_period_start: date | None,
) -> bool:
    current_period = period_start(now.date())
    scheduled_date = current_period + timedelta(days=scheduled_weekday)
    scheduled_at = datetime.combine(
        scheduled_date,
        scheduled_time,
        tzinfo=now.tzinfo,
    )
    if now < scheduled_at:
        return False
    return last_completed_period_start != current_period

