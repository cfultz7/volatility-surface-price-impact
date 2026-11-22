"""Minute panel construction (protocol v0.2: sampled OFI from ES bbo-1s, D-014).

Per session, from ES bbo-1s records (ts_recv = interval end, BBO state at that end):
  * per-second forward state grid from 08:30:01 NY to the close (state as of each second)
  * record-level sampled OFI increments (Cont et al. increment between consecutive valid
    records; reset at session start and after invalid states)
Per minute endpoint t:
  ofi_raw    sum of increments with ts_recv in (t-60s, t]
  depth      time-weighted mean of (Qb+Qa)/2 over (t-60s, t] (contracts)
  spread_bps time-weighted mean of (ask-bid)/mid * 1e4 over (t-60s, t]
  max_age    max observed-record age within the window (gap heuristic; >5 s invalid)
  mid(T)     state mid as of T if record age <= 5 s, else NaN
Outcomes y_h = 1e4 * log(mid(t+h)/mid(t)), delayed y_h_d from t+5s to t+5s+h, only inside
the session (no roll occurs within a session; rolls happen at session start).
"""

from __future__ import annotations

import math
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

from .acquire import _iso
from .alignment import AVAIL_LAG, CUTOFF_LAG, MAX_AGE
from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .fetch import Request
from .order_flow import F_BAD_TS_RECV, F_MAYBE_BAD_BOOK, ofi_increments

RAW = Path("data/raw")
NS = 1_000_000_000
HORIZONS = (1, 2, 5, 10, 15)
QUOTE_MAX_AGE_S = 5
SPLITS_12M = {"dev": ("2025-01-01", "2025-06-30"), "val": ("2025-07-01", "2025-08-31"),
              "final": ("2025-09-01", "2025-12-31")}
# Engineering smoke runs only (never the main analysis): override split dates via env var.
import json as _json  # noqa: E402
import os as _os  # noqa: E402

if _os.environ.get("VSIMPACT_SMOKE_SPLITS"):
    SPLITS_12M = {k: tuple(v) for k, v in _json.loads(_os.environ["VSIMPACT_SMOKE_SPLITS"]).items()}


def split_of(day: str) -> str:
    for k, (a, b) in SPLITS_12M.items():
        if a <= day <= b:
            return k
    return "pilot"


def load_bbo(s, es: str) -> pd.DataFrame:
    r = Request("GLBX.MDP3", "bbo-1s", [es], _iso(s.es_download_start_utc), _iso(s.close_utc))
    df = db.DBNStore.from_file(r.path(RAW)).to_df(price_type="float", pretty_ts=False)
    return df


def second_grid(bbo: pd.DataFrame, s, gap_reset_s: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    ts = bbo.index.to_numpy(np.int64) if not "ts_recv" in bbo.columns else bbo["ts_recv"].to_numpy(np.int64)
    bid, ask = bbo["bid_px_00"].to_numpy(float), bbo["ask_px_00"].to_numpy(float)
    bsz, asz = bbo["bid_sz_00"].to_numpy(float), bbo["ask_sz_00"].to_numpy(float)
    fl = bbo["flags"].to_numpy(np.int64)
    valid = ((fl & (F_BAD_TS_RECV | F_MAYBE_BAD_BOOK)) == 0) & np.isfinite(bid) & np.isfinite(ask) \
        & (ask > bid) & (bsz > 0) & (asz > 0)
    book = pd.DataFrame({"ts_recv": ts, "bid_px": bid, "bid_sz": bsz, "ask_px": ask, "ask_sz": asz,
                         "valid": valid, "reset": False})
    book.loc[0, "reset"] = True
    if gap_reset_s is not None:
        g = np.diff(ts, prepend=ts[0]) > gap_reset_s * NS
        book["reset"] = book["reset"] | g
    book["e"] = ofi_increments(book).to_numpy()
    t0 = int(s.es_download_start_utc.value)
    t1 = int(s.close_utc.value)
    secs = np.arange(t0 + NS, t1 + 1, NS, dtype=np.int64)
    idx = np.searchsorted(ts, secs, side="right") - 1
    has = idx >= 0
    j = np.where(has, idx, 0)
    g = pd.DataFrame({"t": secs})
    g["age"] = np.where(has, (secs - ts[j]) / NS, np.inf)
    g["valid"] = has & valid[j]
    mid = (bid[j] + ask[j]) / 2
    g["mid"] = np.where(g["valid"], mid, np.nan)
    g["depth"] = np.where(g["valid"], (bsz[j] + asz[j]) / 2, np.nan)
    g["spread_bps"] = np.where(g["valid"], (ask[j] - bid[j]) / mid * 1e4, np.nan)
    return book, g


def mid_at(g: pd.DataFrame, T: np.ndarray, max_age=QUOTE_MAX_AGE_S) -> np.ndarray:
    t = g["t"].to_numpy(np.int64)
    i = np.searchsorted(t, T, side="right") - 1
    ok = (i >= 0) & (T <= t[-1])
    i = np.clip(i, 0, len(t) - 1)
    age = g["age"].to_numpy()[i] + (T - t[i]) / NS
    m = g["mid"].to_numpy()[i]
    return np.where(ok & (age <= max_age), m, np.nan)


def minute_features(book: pd.DataFrame, g: pd.DataFrame, s, flow_gap_s: float = 5.0) -> pd.DataFrame:
    t0 = int(s.es_download_start_utc.value)
    mins = np.arange(t0 + 60 * NS, int(s.close_utc.value) + 1, 60 * NS, dtype=np.int64)
    # OFI per minute: increments with ts_recv in (t-60s, t]
    ts = book["ts_recv"].to_numpy(np.int64)
    lab = t0 + 60 * NS * np.ceil((ts - t0) / (60 * NS)).astype(np.int64)
    e = pd.Series(book["e"].to_numpy(), index=lab)
    ofi = e.groupby(level=0).sum(min_count=1).reindex(mins).fillna(0.0)
    # window aggregates over the 60 seconds (t-60, t]; second k labelled by its end
    gm = g.copy()
    gm["minute"] = t0 + 60 * NS * np.ceil((gm["t"] - t0) / (60 * NS)).astype(np.int64)
    agg = gm.groupby("minute").agg(depth=("depth", "mean"), spread_bps=("spread_bps", "mean"),
                                   max_age=("age", "max"), n_invalid=("valid", lambda v: int((~v).sum())))
    agg = agg.reindex(mins)
    f = pd.DataFrame({"t": mins, "ofi_raw": ofi.to_numpy()}).join(agg, on="t")
    f["mid"] = mid_at(g, mins)
    f["flow_ok"] = (f["max_age"] <= flow_gap_s) & (f["n_invalid"] == 0) & np.isfinite(f["depth"])
    f["flow_ok_10s"] = (f["max_age"] <= 10.0) & (f["n_invalid"] == 0) & np.isfinite(f["depth"])
    f["ofi_norm"] = np.where(f["flow_ok"], f["ofi_raw"] / f["depth"], np.nan)
    f["ofi_norm_10s"] = np.where(f["flow_ok_10s"], f["ofi_raw"] / f["depth"], np.nan)
    f["ofi_lag1"] = f["ofi_norm"].shift(1)
    lm = np.log(f["mid"])
    f["r_1m"] = 1e4 * lm.diff()                      # return over (t-60s, t] = flow-minute return
    f["r_flow"] = f["r_1m"]
    f["r_prev5"] = 1e4 * (lm.shift(1) - lm.shift(6))  # (t-6m, t-1m]
    f["r_prev60"] = 1e4 * (lm.shift(1) - lm.shift(61))  # (t-61m, t-1m]
    # trailing complete 30 one-minute returns ending at t-60s (excludes flow minute)
    sq = f["r_1m"].shift(1) ** 2
    f["rv30"] = np.sqrt(sq.rolling(30, min_periods=30).mean())
    f["log_depth"] = np.log(f["depth"])
    for h in HORIZONS:
        f[f"y_{h}"] = 1e4 * (np.log(mid_at(g, mins + h * 60 * NS)) - lm)
        a = mid_at(g, mins + 5 * NS)
        f[f"yd_{h}"] = 1e4 * (np.log(mid_at(g, mins + 5 * NS + h * 60 * NS)) - np.log(a))
        f.loc[mins + (h * 60 + 5) * NS > int(s.close_utc.value), [f"y_{h}", f"yd_{h}"]] = np.nan
    return f


def attach_state(panel: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    """Latest valid state with cutoff <= t-120s, available <= t-60s, t-cutoff <= 600s."""
    st = states[states["ok"]].sort_values("cutoff")
    if st.empty:
        for c in ("sk30", "atm_iv", "state_cutoff", "state_age_s"):
            panel[c] = np.nan
        return panel
    c_ns = st["cutoff"].astype("int64").to_numpy() if hasattr(st["cutoff"], "astype") else None
    a_ns = pd.to_datetime(st["feature_available_time"], utc=True).astype("int64").to_numpy()
    c_ns = pd.to_datetime(st["cutoff"], utc=True).astype("int64").to_numpy()
    out = {k: [] for k in ("sk30", "atm_iv", "state_cutoff", "n_expiries_usable", "usable",
                           "band_violation")}
    for t in panel["t"].to_numpy(np.int64):
        ok = (c_ns <= t - CUTOFF_LAG.value) & (a_ns <= t - AVAIL_LAG.value) & (t - c_ns <= MAX_AGE.value)
        if not ok.any():
            for k in out:
                out[k].append(np.nan)
            continue
        i = np.where(ok)[0][np.argmax(c_ns[ok])]
        r = st.iloc[i]
        out["sk30"].append(r["sk30"]); out["atm_iv"].append(r["atm_iv"])
        out["state_cutoff"].append(c_ns[i]); out["n_expiries_usable"].append(r.get("n_expiries_usable", np.nan))
        out["usable"].append(r.get("usable", np.nan)); out["band_violation"].append(r.get("band_violation", np.nan))
    for k, v in out.items():
        panel[k] = v
    panel["state_age_s"] = (panel["t"] - panel["state_cutoff"]) / NS
    return panel


def session_panel(day: str, states_by_variant: dict[str, pd.DataFrame]) -> pd.DataFrame:
    s = {x.day.isoformat(): x for x in eligible_sessions(day, day)}[day]
    sched = active_contract_schedule([s.day])
    contract, roll = sched[s.day]
    es = contract.candidate_raw_symbol()
    book, g = second_grid(load_bbo(s, es), s)
    f = minute_features(book, g, s)
    obs = s.observation_endpoints().asi8
    f = f[f["t"].isin(obs)].copy()
    f["date"] = day
    f["split"] = split_of(day)
    f["es_contract"] = es
    f["roll_day"] = roll
    f["es_dte"] = (contract.expiry - s.day).days
    f["early_close"] = s.early_close
    f["weekday"] = pd.Timestamp(day).weekday()
    L = (s.close_utc - s.open_utc).total_seconds() / 60
    m = (f["t"] - int(s.open_utc.value)) / (60 * NS)
    f["sess_min"] = m
    for k in (1, 2):
        f[f"sin{k}"] = np.sin(2 * math.pi * k * m / L)
        f[f"cos{k}"] = np.cos(2 * math.pi * k * m / L)
    out = []
    for var, st in states_by_variant.items():
        p = attach_state(f.copy(), st[st["date"] == day])
        p["variant"] = var
        out.append(p)
    return pd.concat(out, ignore_index=True)
