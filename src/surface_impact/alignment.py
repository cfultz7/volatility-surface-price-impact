"""Locked information timeline (spec §8).

For observation endpoint t (UTC):
  flow window       (t - 60s, t]
  outcome endpoints  t + h, h in {1,2,5,10,15} min (and delayed t+5s -> t+5s+h)
  surface state: latest valid state with
      snapshot_cutoff <= t - 120s,
      feature_available_time <= t - 60s,
      t - snapshot_cutoff <= 600s.
Surface cutoffs s are 5-minute NY session-clock times; availability >= s + 5s (or the
measured later time).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CUTOFF_LAG = pd.Timedelta(seconds=120)
AVAIL_LAG = pd.Timedelta(seconds=60)
MAX_AGE = pd.Timedelta(seconds=600)
COMPUTE_ALLOWANCE = pd.Timedelta(seconds=5)


def availability_time(cutoff: pd.Timestamp, measured_fit_seconds: float | None = None) -> pd.Timestamp:
    allow = COMPUTE_ALLOWANCE
    if measured_fit_seconds is not None and measured_fit_seconds > allow.total_seconds():
        allow = pd.Timedelta(seconds=measured_fit_seconds)
    return cutoff + allow


def select_state(states: pd.DataFrame, t: pd.Timestamp) -> pd.Series | None:
    """states: columns snapshot_cutoff, feature_available_time, valid (bool), sorted by
    cutoff. Returns the latest admissible valid state, or None."""
    ok = (states["valid"]
          & (states["snapshot_cutoff"] <= t - CUTOFF_LAG)
          & (states["feature_available_time"] <= t - AVAIL_LAG)
          & (t - states["snapshot_cutoff"] <= MAX_AGE))
    cand = states.loc[ok]
    if cand.empty:
        return None
    return cand.loc[cand["snapshot_cutoff"].idxmax()]


def snapshot_rows_usable_at_cutoff(cbbo: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """OPRA cbbo-1m rows whose interval END (ts_recv, clamped interval end) is <= cutoff.
    ts_event (possibly a last-trade time) is never used for availability or quote age."""
    return cbbo.loc[cbbo["ts_recv"] <= cutoff]


def timeline(t: pd.Timestamp, horizons_min=(1, 2, 5, 10, 15)) -> dict:
    return {
        "flow_start_exclusive": t - pd.Timedelta(seconds=60),
        "flow_end_inclusive": t,
        "latest_admissible_cutoff": t - CUTOFF_LAG,
        "latest_admissible_availability": t - AVAIL_LAG,
        "oldest_admissible_cutoff": t - MAX_AGE,
        "endpoints": {h: t + pd.Timedelta(minutes=h) for h in horizons_min},
        "delayed_endpoints": {h: t + pd.Timedelta(seconds=5) + pd.Timedelta(minutes=h)
                              for h in horizons_min},
    }


def outcome_valid(t: pd.Timestamp, h_min: int, session_close: pd.Timestamp,
                  contract_at_t: str, contract_at_end: str) -> bool:
    end = t + pd.Timedelta(minutes=h_min)
    return bool(end <= session_close and contract_at_t == contract_at_end)


def as_of_quote(ts: np.ndarray, values: np.ndarray, at: int, max_age_ns: int):
    """Latest value with ts <= at and at - ts <= max_age; NaN otherwise."""
    i = np.searchsorted(ts, at, side="right") - 1
    if i < 0 or at - ts[i] > max_age_ns:
        return np.nan
    return values[i]
