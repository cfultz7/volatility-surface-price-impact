"""Timeline example around 10:00 New York (spec §8) and no-lookahead checks."""

import numpy as np
import pandas as pd

from surface_impact.alignment import (as_of_quote, availability_time, select_state,
                                      snapshot_rows_usable_at_cutoff, timeline)

NY = "America/New_York"


def ny(s):
    return pd.Timestamp(f"2024-02-01 {s}", tz=NY).tz_convert("UTC")


def states(cutoffs):
    c = pd.Series([ny(x) for x in cutoffs])
    return pd.DataFrame({"snapshot_cutoff": c, "feature_available_time": c + pd.Timedelta(seconds=5),
                         "valid": True})


def test_ten_oclock_example():
    """t = 10:00:00 NY. Flow window (09:59:00, 10:00:00]. Main outcome at 10:05:00.
    Surface cutoffs every 5 min: 09:50, 09:55, 10:00. The 09:55 state has cutoff 09:55:00
    <= 09:58:00 and availability 09:55:05 <= 09:59:00, so it is used. 10:00 is from the
    future relative to the flow minute and is rejected."""
    t = ny("10:00:00")
    tl = timeline(t)
    assert tl["flow_start_exclusive"] == ny("09:59:00")
    assert tl["endpoints"][5] == ny("10:05:00")
    assert tl["delayed_endpoints"][5] == ny("10:05:05")
    st = select_state(states(["09:50", "09:55", "10:00"]), t)
    assert st["snapshot_cutoff"] == ny("09:55")


def test_slow_fit_pushes_availability():
    t = ny("10:00:00")
    s = states(["09:50", "09:55"])
    s.loc[1, "feature_available_time"] = availability_time(ny("09:55"), measured_fit_seconds=300)
    assert availability_time(ny("09:55"), 300) == ny("10:00:00")
    assert select_state(s, t)["snapshot_cutoff"] == ny("09:50")  # 09:55 not available by 09:59


def test_stale_state_rejected_and_future_perturbation_invariance():
    t = ny("10:00:00")
    assert select_state(states(["09:45"]), t) is None  # 15 min old > 600 s
    s1 = states(["09:55", "09:58", "10:05"])
    s2 = s1.copy()
    s2.loc[2, "valid"] = False  # perturb a future state
    # 09:58 cutoff == t - 120 s exactly and available 09:58:05 <= 09:59: admissible (boundary)
    assert select_state(s1, t)["snapshot_cutoff"] == select_state(s2, t)["snapshot_cutoff"] == ny("09:58")


def test_cbbo_uses_interval_end_not_trade_time():
    cut = ny("09:55")
    cbbo = pd.DataFrame({"ts_recv": [ny("09:54"), ny("09:55"), ny("09:56")],
                         "ts_event": [ny("09:20"), ny("09:54:30"), ny("09:54:59")]})
    used = snapshot_rows_usable_at_cutoff(cbbo, cut)
    assert len(used) == 2  # the 09:56 interval (ending after cutoff) is excluded


def test_as_of_quote_max_age():
    ts = np.array([0, 3, 10], dtype=np.int64) * 10**9
    v = np.array([1.0, 2.0, 3.0])
    assert as_of_quote(ts, v, 5 * 10**9, 5 * 10**9) == 2.0
    assert np.isnan(as_of_quote(ts, v, 9 * 10**9, 5 * 10**9))  # 6 s old
