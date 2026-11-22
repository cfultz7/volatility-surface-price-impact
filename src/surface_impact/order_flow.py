"""Event-level best-quote OFI (Cont, Kukanov & Stoikov 2014) on complete book states.

Input: a time-ordered frame of *complete* top-of-book states (one row per completed
event, i.e. after collapsing records up to F_LAST), with columns
ts_recv (int ns), bid_px, bid_sz, ask_px, ask_sz, valid (bool), reset (bool).
A ``reset`` row (snapshot/recovery/session or roll start) starts a new segment: no
increment is computed across it. Rows with valid == False are excluded and also
break the segment. Unchanged consecutive states are suppressed (they contribute 0).

e[n] = 1{Pb[n] >= Pb[n-1]} Qb[n] - 1{Pb[n] <= Pb[n-1]} Qb[n-1]
     - 1{Pa[n] <= Pa[n-1]} Qa[n] + 1{Pa[n] >= Pa[n-1]} Qa[n-1]
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Flag values from the installed DBN client (verified 2026-11-21, databento-dbn with
# databento 0.90.0: F_LAST=128, F_SNAPSHOT=32, F_BAD_TS_RECV=8, F_MAYBE_BAD_BOOK=4).
from databento_dbn import F_BAD_TS_RECV, F_LAST, F_MAYBE_BAD_BOOK, F_SNAPSHOT  # noqa: E402,F401


def collapse_events(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the book state after the last record of each event (F_LAST set)."""
    last = (df["flags"].to_numpy() & F_LAST) != 0
    return df.loc[last].copy()


def ofi_increments(book: pd.DataFrame) -> pd.Series:
    b = book.reset_index(drop=True)
    pb, qb = b["bid_px"].to_numpy(float), b["bid_sz"].to_numpy(float)
    pa, qa = b["ask_px"].to_numpy(float), b["ask_sz"].to_numpy(float)
    ok = b["valid"].to_numpy(bool)
    reset = b["reset"].to_numpy(bool)
    e = np.full(len(b), np.nan)
    for n in range(1, len(b)):
        if not ok[n] or not ok[n - 1] or reset[n]:
            continue  # segment break: no increment across invalid/reset boundary
        e[n] = ((pb[n] >= pb[n - 1]) * qb[n] - (pb[n] <= pb[n - 1]) * qb[n - 1]
                - (pa[n] <= pa[n - 1]) * qa[n] + (pa[n] >= pa[n - 1]) * qa[n - 1])
    return pd.Series(e, index=book.index, name="ofi_e")


def time_weighted_depth(book: pd.DataFrame, t0: int, t1: int) -> float:
    """Time-weighted mean of (Qb + Qa)/2 over (t0, t1], using the prevailing valid state.
    Returns NaN if no valid state covers the window."""
    ts = book["ts_recv"].to_numpy(np.int64)
    d = 0.5 * (book["bid_sz"].to_numpy(float) + book["ask_sz"].to_numpy(float))
    ok = book["valid"].to_numpy(bool)
    i0 = np.searchsorted(ts, t0, side="right") - 1
    if i0 < 0 or not ok[i0]:
        return np.nan
    idx = np.r_[i0, np.where((ts > t0) & (ts <= t1))[0]]
    edges = np.r_[t0, ts[idx[1:]], t1].astype(float)
    dur = np.diff(edges)
    vals = d[idx]
    if not ok[idx].all():
        return np.nan
    return float(np.sum(vals * dur) / np.sum(dur))
