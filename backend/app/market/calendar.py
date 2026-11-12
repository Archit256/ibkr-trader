"""US Equities (NYSE/NASDAQ) market hours and holiday calendar."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import MarketSession, MarketStatus

ET_ZONE = ZoneInfo("America/New_York")
PRE_MARKET_START = time(4, 0)
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
AFTER_HOURS_END = time(20, 0)


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian Easter algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> set[date]:
    """Standard NYSE/NASDAQ full-day market holidays for a given year."""
    good_friday = _easter_sunday(year) - timedelta(days=2)
    holidays = {
        _observed(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day (3rd Mon Feb)
        good_friday,  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day (last Mon May)
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),  # Christmas Day
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth
    return holidays


class USMarketCalendar:
    """Evaluates US Equity regular & extended trading hours in America/New_York."""

    def __init__(self, enforce_market_hours: bool = True) -> None:
        self.enforce_market_hours = enforce_market_hours

    def status(self, now: datetime | None = None) -> MarketStatus:
        utc_dt = now or datetime.now(timezone.utc)
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        et_dt = utc_dt.astimezone(ET_ZONE)
        et_date = et_dt.date()
        et_time_str = et_dt.strftime("%Y-%m-%d %H:%M:%S ET")

        if et_date.weekday() >= 5:
            return MarketStatus(
                is_rth_open=False,
                session="CLOSED",
                et_time=et_time_str,
                enforce_market_hours=self.enforce_market_hours,
                reason="Weekend (NYSE/NASDAQ closed)",
            )

        if et_date in us_market_holidays(et_date.year):
            return MarketStatus(
                is_rth_open=False,
                session="CLOSED",
                et_time=et_time_str,
                enforce_market_hours=self.enforce_market_hours,
                reason="US Market Holiday",
            )

        t = et_dt.time()
        session: MarketSession
        is_rth = False
        if RTH_OPEN <= t < RTH_CLOSE:
            session = "RTH"
            is_rth = True
            reason = "Regular Trading Hours (09:30–16:00 ET)"
        elif PRE_MARKET_START <= t < RTH_OPEN:
            session = "PRE_MARKET"
            reason = "Pre-market session (04:00–09:30 ET)"
        elif RTH_CLOSE <= t < AFTER_HOURS_END:
            session = "AFTER_HOURS"
            reason = "After-hours session (16:00–20:00 ET)"
        else:
            session = "CLOSED"
            reason = "Overnight closed"

        return MarketStatus(
            is_rth_open=is_rth,
            session=session,
            et_time=et_time_str,
            enforce_market_hours=self.enforce_market_hours,
            reason=reason,
        )

