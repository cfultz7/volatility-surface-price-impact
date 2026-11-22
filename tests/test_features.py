"""SYNTHETIC bbo-1s paths -> minute features: OFI sums, returns, outcome boundaries,
quote-age rule, and no look-ahead."""

import numpy as np
import pandas as pd

from surface_impact.calendars import eligible_sessions
from surface_impact.features import minute_features, second_grid

NS = 1_000_000_000
S = eligible_sessions("2024-02-01", "2024-02-01")[0]


def bbo_path(n_sec=None, bid0=5000.0, step_at=None, quiet=None):
    t0 = int(S.es_download_start_utc.value)
    t1 = int(S.close_utc.value)
    secs = np.arange(t0 + NS, t1 + 1, NS)
    bid = np.full(len(secs), bid0)
    if step_at is not None:
        bid[secs >= step_at] += 0.25
    df = pd.DataFrame({"bid_px_00": bid, "ask_px_00": bid + 0.25, "bid_sz_00": 10.0,
                       "ask_sz_00": 10.0, "flags": 128}, index=pd.Index(secs, name="ts_recv"))
    if quiet is not None:
        a, b = quiet
        df = df[(df.index <= a) | (df.index > b)]
    return df


def feats(df):
    book, g = second_grid(df, S)
    return minute_features(book, g, S)


def test_constant_book_zero_flow_and_returns():
    f = feats(bbo_path())
    obs = f[f.t >= int(S.first_obs_utc.value)]
    assert (obs.ofi_raw == 0).all() and np.allclose(obs.y_5.dropna(), 0)
    assert np.allclose(obs.depth, 10.0)


def test_price_step_shows_in_flow_minute_and_returns():
    t_step = int(S.first_obs_utc.value) + 30 * NS  # 09:40:30 -> inside minute ending 09:41
    f = feats(bbo_path(step_at=t_step)).set_index("t")
    m = int(S.first_obs_utc.value) + 60 * NS
    # bid up: +Qb[n]=10, ask up: +Qa[n-1]=10 -> e = 20 in that minute only
    assert f.loc[m, "ofi_raw"] == 20
    assert f.loc[m - 60 * NS, "ofi_raw"] == 0
    assert f.loc[m - 60 * NS, "y_1"] > 0          # outcome from 09:40 to 09:41 sees the step
    assert f.loc[m, "y_1"] == 0                   # after the step nothing moves


def test_no_outcome_crosses_close():
    f = feats(bbo_path())
    late = f[f.t > int(S.close_utc.value) - 15 * 60 * NS]
    assert late["y_15"].isna().all()


def test_quote_age_rule_and_gap_invalidates_flow():
    q0 = int(S.first_obs_utc.value) + 10 * NS
    f = feats(bbo_path(quiet=(q0, q0 + 8 * NS))).set_index("t")
    m = int(S.first_obs_utc.value) + 60 * NS
    assert not f.loc[m, "flow_ok"]           # 8 s without records > 5 s rule
    assert f.loc[m, "flow_ok_10s"]           # but passes the 10 s sensitivity


def test_future_change_does_not_alter_past_features():
    base = feats(bbo_path())
    later = int(S.first_obs_utc.value) + 3600 * NS
    alt = feats(bbo_path(step_at=later))
    cols = ["ofi_raw", "depth", "spread_bps", "r_flow", "r_prev5", "rv30"]
    early = base.t < later
    pd.testing.assert_frame_equal(base.loc[early, cols].reset_index(drop=True),
                                  alt.loc[early, cols].reset_index(drop=True))
