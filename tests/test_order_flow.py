"""Hand-computed OFI cases (SYNTHETIC)."""

import numpy as np
import pandas as pd

from surface_impact.order_flow import F_LAST, collapse_events, ofi_increments, time_weighted_depth


def book(rows, valid=None, reset=None):
    df = pd.DataFrame(rows, columns=["bid_px", "bid_sz", "ask_px", "ask_sz"])
    df["ts_recv"] = np.arange(len(df)) * 1_000_000_000
    df["valid"] = True if valid is None else valid
    df["reset"] = False if reset is None else reset
    return df


def test_same_price_size_changes():
    # bid size 10->15 (+5), ask size 20->18 (-(18-20) = +2): e = 15-10 -18+20 = 7
    e = ofi_increments(book([[100, 10, 101, 20], [100, 15, 101, 18]]))
    assert e.iloc[1] == 7


def test_moving_prices():
    # bid up 100->101: +Qb[n]=4 ; ask unchanged 102: -Qa[n]+Qa[n-1] = -7+5
    e = ofi_increments(book([[100, 9, 102, 5], [101, 4, 102, 7]]))
    assert e.iloc[1] == 4 - 7 + 5
    # bid down 100->99: -Qb[n-1] = -9 ; ask down 102->101: -Qa[n] = -3
    e = ofi_increments(book([[100, 9, 102, 5], [99, 2, 101, 3]]))
    assert e.iloc[1] == -9 - 3


def test_duplicate_state_is_zero():
    e = ofi_increments(book([[100, 9, 101, 5], [100, 9, 101, 5]]))
    assert e.iloc[1] == 0


def test_invalid_and_reset_break_segments():
    rows = [[100, 9, 101, 5], [100, 50, 101, 5], [100, 9, 101, 5], [100, 30, 101, 5]]
    e = ofi_increments(book(rows, valid=[True, False, True, True], reset=[True, False, True, False]))
    assert np.isnan(e.iloc[0]) and np.isnan(e.iloc[1]) and np.isnan(e.iloc[2])
    assert e.iloc[3] == 21  # only the post-reset change counts; restore jump not counted


def test_multi_record_event_collapse():
    raw = pd.DataFrame({"flags": [0, F_LAST, 0, 0, F_LAST], "bid_px": [1, 2, 3, 4, 5]})
    assert collapse_events(raw)["bid_px"].tolist() == [2, 5]


def test_time_weighted_depth():
    b = book([[100, 10, 101, 10], [100, 30, 101, 30]])  # depth 10 for 1s, then 30
    s = 1_000_000_000
    assert time_weighted_depth(b, 0, 2 * s) == 20.0
