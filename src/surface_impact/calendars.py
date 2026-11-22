"""Study session calendar.

The study session is the cash-index regular-session overlap (normally 09:30-16:00
America/New_York). We use the NYSE calendar from ``exchange_calendars`` (XNYS) for
holidays, unscheduled closures and early closes, and audit it separately against
Cboe/CME status records once acquired (see docs/decisions.md D-004).

All returned timestamps are tz-aware UTC; New York local time is used only to build
the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

NY = "America/New_York"
UTC = "UTC"

SESSION_OPEN_NY = time(9, 30)
ES_DOWNLOAD_START_NY = time(8, 30)
OBS_FIRST_NY = time(9, 40)
OBS_LAST_OFFSET = pd.Timedelta(minutes=15)  # last observation = close - 15 min


@dataclass(frozen=True)
class Session:
    """One eligible common regular session."""

    day: date
    open_utc: pd.Timestamp
    close_utc: pd.Timestamp
    early_close: bool

    @property
    def es_download_start_utc(self) -> pd.Timestamp:
        return _ny(self.day, ES_DOWNLOAD_START_NY)

    @property
    def first_obs_utc(self) -> pd.Timestamp:
        return _ny(self.day, OBS_FIRST_NY)

    @property
    def last_obs_utc(self) -> pd.Timestamp:
        return self.close_utc - OBS_LAST_OFFSET

    def observation_endpoints(self) -> pd.DatetimeIndex:
        """Minute endpoints t from 09:40 through close-15min (inclusive), UTC."""
        return pd.date_range(self.first_obs_utc, self.last_obs_utc, freq="1min")

    def surface_cutoffs(self) -> pd.DatetimeIndex:
        """Five-minute surface cutoffs aligned to the NY session clock, UTC."""
        return pd.date_range(self.open_utc + pd.Timedelta(minutes=5), self.close_utc, freq="5min")


def _ny(d: date, t: time) -> pd.Timestamp:
    return pd.Timestamp.combine(d, t).tz_localize(NY).tz_convert(UTC)


@lru_cache(maxsize=1)
def _xnys():
    return xcals.get_calendar("XNYS", start="2013-01-01")


def eligible_sessions(start: str | date, end: str | date) -> list[Session]:
    """All XNYS sessions in [start, end] (inclusive dates).

    Early closes are taken from the calendar's actual close time. Close is the
    regular 16:00 NY equity close (Cboe SPXW quotes continue to 16:15 but are outside
    the common session).
    """
    cal = _xnys()
    sched = cal.schedule.loc[str(start) : str(end)]
    out: list[Session] = []
    for d, row in sched.iterrows():
        open_utc = pd.Timestamp(row["open"]).tz_convert(UTC)
        close_utc = pd.Timestamp(row["close"]).tz_convert(UTC)
        close_ny = close_utc.tz_convert(NY)
        early = (close_ny.hour, close_ny.minute) < (16, 0)
        out.append(Session(pd.Timestamp(d).date(), open_utc, close_utc, early))
    return out


def scheduled_weekdays(start: str | date, end: str | date) -> list[date]:
    """All Mon-Fri dates, to expose non-session days explicitly in coverage tables."""
    return [d.date() for d in pd.bdate_range(str(start), str(end))]
