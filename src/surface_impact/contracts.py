"""ES quarterly contract calendar and fixed roll rule.

Roll rule (protocol v0.1): switch from the expiring quarterly contract to the next
quarterly contract at the start of the regular session on the Thursday eight calendar
days before the standard third-Friday expiry. If that Thursday is not an eligible
common session, use the next eligible common session before expiry.

Candidate raw symbols are *generated* here only as lookup keys; every symbol must be
verified against Databento symbology/definitions before use (contracts are never
assumed to exist from the string format alone).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

QUARTER_MONTHS = {3: "H", 6: "M", 9: "U", 12: "Z"}


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


@dataclass(frozen=True)
class QuarterlyContract:
    year: int
    month: int

    @property
    def expiry(self) -> date:
        return third_friday(self.year, self.month)

    @property
    def nominal_roll_thursday(self) -> date:
        return self.expiry - timedelta(days=8)

    def candidate_raw_symbol(self) -> str:
        # Databento GLBX raw symbols use a single-digit year code (verified via
        # symbology.resolve on 2024-02-01: ESH4 -> instrument_id 17077).
        return f"ES{QUARTER_MONTHS[self.month]}{self.year % 10}"

    def next(self) -> "QuarterlyContract":
        m = self.month + 3
        return QuarterlyContract(self.year + (m > 12), m - 12 if m > 12 else m)


def first_contract_on_or_after(d: date) -> QuarterlyContract:
    for m in (3, 6, 9, 12):
        if third_friday(d.year, m) >= d:
            return QuarterlyContract(d.year, m)
    return QuarterlyContract(d.year + 1, 3)


def roll_date(contract: QuarterlyContract, eligible: set[date]) -> date:
    """First session on which the *next* contract becomes active."""
    d = contract.nominal_roll_thursday
    while d not in eligible:
        d += timedelta(days=1)
        if d >= contract.expiry:
            raise ValueError(f"No eligible roll session before expiry of {contract}")
    return d


def active_contract_schedule(
    sessions: list[date], eligible: set[date] | None = None
) -> dict[date, tuple[QuarterlyContract, bool]]:
    """Map each session to (active contract, is_roll_day).

    ``eligible`` is the full set of eligible common sessions used to locate roll
    dates; by default it is the study calendar around the requested sessions, so a
    roll Thursday is found even when it lies outside the requested list.
    """
    if eligible is None:
        from .calendars import eligible_sessions

        lo, hi = min(sessions) - timedelta(days=120), max(sessions) + timedelta(days=120)
        eligible = {s.day for s in eligible_sessions(lo, hi)}
    out: dict[date, tuple[QuarterlyContract, bool]] = {}
    for d in sorted(sessions):
        c = first_contract_on_or_after(d)
        r = roll_date(c, eligible)
        if d >= r:
            out[d] = (c.next(), d == r)
        else:
            out[d] = (c, False)
    return out
