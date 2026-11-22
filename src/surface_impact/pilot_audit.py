"""Pilot verification on real records (engineering/measurement checks, not results).

Writes outputs/pilot/pilot_audit.json. Pilot dates are development-period engineering
checks; nothing here is an outcome analysis.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

from .calendars import eligible_sessions
from .order_flow import F_BAD_TS_RECV, F_LAST, F_MAYBE_BAD_BOOK, F_SNAPSHOT, ofi_increments
from .pilot_quotes import PILOT, build

NY = "America/New_York"


def df_of(req):
    st = db.DBNStore.from_file(req.path(Path("data/raw")))
    # databento 0.90: to_df(price_type="float") already applies the 1e-9 fixed-point scale and
    # maps UNDEF_PRICE to NaN; prices must NOT be divided by 1e9 again.
    return st, st.to_df(price_type="float", pretty_ts=True, map_symbols=True)


def book_from(df: pd.DataFrame, s, gap_s: float | None) -> pd.DataFrame:
    b = pd.DataFrame({
        "ts_recv": df.index.view("int64") if isinstance(df.index, pd.DatetimeIndex) else df["ts_recv"],
        "bid_px": df["bid_px_00"].to_numpy(float), "bid_sz": df["bid_sz_00"].to_numpy(float),
        "ask_px": df["ask_px_00"].to_numpy(float), "ask_sz": df["ask_sz_00"].to_numpy(float),
    })
    fl = df["flags"].to_numpy()
    bad = (fl & (F_BAD_TS_RECV | F_MAYBE_BAD_BOOK)) != 0
    crossed = ~(b.ask_px > b.bid_px) | ~np.isfinite(b.bid_px) | ~np.isfinite(b.ask_px)
    b["valid"] = ~(bad | crossed.to_numpy())
    b["reset"] = (fl & F_SNAPSHOT) != 0
    b.iloc[0, b.columns.get_loc("reset")] = True
    if gap_s is not None:  # explicit gap heuristic: reset after a long silence
        g = np.diff(b.ts_recv.to_numpy(), prepend=b.ts_recv.iloc[0]) > gap_s * 1e9
        b["reset"] = b["reset"] | g
    return b


def minute_ofi(book: pd.DataFrame, s) -> pd.Series:
    e = ofi_increments(book)
    t = pd.to_datetime(book.ts_recv, utc=True)
    m = t.dt.ceil("1min")  # flow window (t-60s, t] -> label at right endpoint
    ser = pd.Series(e.to_numpy(), index=m).groupby(level=0).sum(min_count=1)
    grid = s.observation_endpoints()
    return ser.reindex(grid)


def audit_day(day: str) -> dict:
    s = {x.day.isoformat(): x for x in eligible_sessions("2024-01-01", "2024-12-31")}[day]
    reqs, info = build(day)
    out = {"day": day, "selection": {k: info[k] for k in ("es_contract", "ref_close", "n_selected",
                                                           "expiries", "band")}}
    # --- OPRA cbbo-1m -------------------------------------------------------------
    cb = pd.concat([df_of(r)[1] for r in reqs if r.schema == "cbbo-1m"])
    cb = cb.reset_index()
    ts = cb["ts_recv"]
    out["cbbo"] = {
        "rows": int(len(cb)), "instruments": int(cb["instrument_id"].nunique()),
        "ts_recv_on_minute_boundary_frac": float((ts.dt.second.eq(0) & ts.dt.nanosecond.eq(0)
                                                  & ts.dt.microsecond.eq(0)).mean()),
        "ts_recv_first_ny": ts.min().tz_convert(NY).isoformat(),
        "ts_recv_last_ny": ts.max().tz_convert(NY).isoformat(),
        "ts_event_after_ts_recv": int((cb["ts_event"] > ts).sum()),
        "bid_px_quantiles": cb["bid_px_00"].quantile([0.01, 0.5, 0.99]).round(2).tolist(),
        "zero_or_nan_bid_frac": float(((cb["bid_px_00"] <= 0) | cb["bid_px_00"].isna()).mean()),
        "columns": list(cb.columns),
    }
    # two-sided strikes and parity pairs at noon cutoff
    noon = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
    snap = cb[(ts <= noon) & (ts > noon - pd.Timedelta(seconds=120))].sort_values("ts_recv")
    snap = snap.groupby("symbol").tail(1)
    ok = (snap.bid_px_00 > 0) & (snap.ask_px_00 >= snap.bid_px_00) & (snap.bid_sz_00 > 0) & (snap.ask_sz_00 > 0)
    mid = (snap.bid_px_00 + snap.ask_px_00) / 2
    ok &= ((snap.ask_px_00 - snap.bid_px_00) / mid) <= 0.5
    snap = snap[ok].copy()
    snap["exp"] = snap["symbol"].str[6:12]
    snap["right"] = snap["symbol"].str[12]
    snap["K"] = snap["symbol"].str[13:].astype(int) / 1000
    per = {}
    for e, g in snap.groupby("exp"):
        c, p = set(g[g.right == "C"].K), set(g[g.right == "P"].K)
        per[e] = {"usable_calls": len(c), "usable_puts": len(p), "parity_pairs": len(c & p),
                  "distinct_strikes": len(c | p)}
    out["noon_snapshot_quality"] = per
    # --- ES bbo-1s -----------------------------------------------------------------
    st, bb = df_of([r for r in reqs if r.schema == "bbo-1s"][0])
    ts = bb.index.to_series()
    gaps = ts.diff().dt.total_seconds().dropna()
    out["bbo1s"] = {
        "rows": int(len(bb)), "columns": list(bb.columns),
        "ts_recv_on_second_boundary_frac": float((ts.dt.microsecond.eq(0) & ts.dt.nanosecond.eq(0)).mean()),
        "gap_seconds_quantiles": gaps.quantile([0.5, 0.9, 0.99, 1.0]).tolist(),
        "n_gaps_gt_5s_in_session": int((gaps[(ts.iloc[1:] >= s.open_utc).to_numpy()] > 5).sum()),
        "max_gap_s_in_session": float(gaps[(ts.iloc[1:] >= s.open_utc).to_numpy()].max()),
        "bid_px_median": float(bb.bid_px_00.median()), "spread_ticks_median":
            float(((bb.ask_px_00 - bb.bid_px_00) / 0.25).median()),
        "flags_counts": {int(k): int(v) for k, v in bb["flags"].value_counts().items()},
    }
    ofi_s = minute_ofi(book_from(bb, s, gap_s=None), s)
    out["sampled_ofi_minutes_nonnull"] = int(ofi_s.notna().sum())
    # --- mbp-1 audit -----------------------------------------------------------------
    mreq = [r for r in reqs if r.schema == "mbp-1"]
    if mreq:
        st, mb = df_of(mreq[0])
        last = (mb["flags"].to_numpy() & F_LAST) != 0
        ev = mb.loc[last]
        ofi_e = minute_ofi(book_from(ev, s, gap_s=None), s)
        both = pd.concat({"sampled": ofi_s, "event": ofi_e}, axis=1).dropna()
        ret = None
        out["mbp1_audit"] = {
            "rows": int(len(mb)), "complete_events": int(last.sum()),
            "actions": {str(k): int(v) for k, v in mb["action"].value_counts().items()},
            "minutes_compared": int(len(both)),
            "corr_sampled_vs_event": float(both.corr().iloc[0, 1]),
            "spearman": float(both.rank().corr().iloc[0, 1]),
            "ratio_sd_sampled_to_event": float(both.sampled.std() / both.event.std()),
            "sign_agreement": float((np.sign(both.sampled) == np.sign(both.event)).mean()),
        }
        del ret
    return out


if __name__ == "__main__":
    res = [audit_day(d) for d in PILOT]
    Path("outputs/pilot").mkdir(parents=True, exist_ok=True)
    Path("outputs/pilot/pilot_audit.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=1, default=str)[:12000])
