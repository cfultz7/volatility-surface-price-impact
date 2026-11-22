"""SPXW contract selection from listed OSI symbols (pre-session, no outcome data).

OSI symbol layout: root padded to 6 chars, YYMMDD expiry, C/P, strike*1000 (8 digits),
e.g. ``"SPXW  240301P04900000"``. The OSI string is used to *pre-select* contracts for
cost estimation and requests; SPXW family, European exercise, PM settlement, multiplier
and the exact expiry instant must still be verified from definition records before any
measurement (protocol §5, §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import pandas as pd

NY = "America/New_York"
PM_EXPIRY_TIME_NY = time(16, 0)  # provisional; verify against definitions (D-006)
MIN_DAYS, MAX_DAYS, TARGET_DAYS = 14, 90, 30


@dataclass(frozen=True)
class OSI:
    raw: str
    root: str
    expiry: date
    right: str
    strike: float

    @property
    def expiry_instant_utc(self) -> pd.Timestamp:
        return (pd.Timestamp(datetime.combine(self.expiry, PM_EXPIRY_TIME_NY))
                .tz_localize(NY).tz_convert("UTC"))


def parse_osi(s: str) -> OSI | None:
    if len(s) != 21:
        return None
    root, ymd, right, k = s[:6].strip(), s[6:12], s[12], s[13:]
    if right not in "CP" or not (ymd.isdigit() and k.isdigit()):
        return None
    exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return OSI(s, root, exp, right, int(k) / 1000.0)


def bracket_expiries(expiries: list[pd.Timestamp], cutoffs: pd.DatetimeIndex) -> set[pd.Timestamp]:
    """Union over cutoffs of the nearest expiry at/below and at/above cutoff+30d, among
    expiries 14-90 calendar days from the cutoff. An exact match is used alone."""
    exps = sorted(set(expiries))
    out: set[pd.Timestamp] = set()
    for s in cutoffs:
        tgt = s + pd.Timedelta(days=TARGET_DAYS)
        elig = [e for e in exps if pd.Timedelta(days=MIN_DAYS) <= e - s <= pd.Timedelta(days=MAX_DAYS)]
        below = [e for e in elig if e <= tgt]
        above = [e for e in elig if e >= tgt]
        if below and below[-1] == tgt:
            out.add(below[-1])
            continue
        if below:
            out.add(below[-1])
        if above:
            out.add(above[0])
    return out


def select_contracts(listed: list[str], ref_price: float, cutoffs: pd.DatetimeIndex,
                     band: tuple[float, float] = (0.85, 1.15), root: str = "SPXW") -> dict:
    parsed = [p for p in (parse_osi(s) for s in listed) if p is not None and p.root == root]
    exps = bracket_expiries([p.expiry_instant_utc for p in parsed], cutoffs)
    lo, hi = band[0] * ref_price, band[1] * ref_price
    sel = sorted(p.raw for p in parsed
                 if p.expiry_instant_utc in exps and lo <= p.strike <= hi)
    return {"symbols": sel, "expiries": sorted(e.date().isoformat() for e in exps),
            "band": [lo, hi], "n_listed_root": len(parsed)}
