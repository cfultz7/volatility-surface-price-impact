"""Surface measurement per session: snapshots -> parity -> Method A/B -> 30d 25-delta skew.

Output per (date, cutoff, method): skew state row with cutoff/availability times and full
quality diagnostics. Quote filters (spec §10) are parameterised so the prespecified
sensitivities (record age 60/180 s, relative spread 0.25/1.00) reuse the same universe.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd

from .calendars import eligible_sessions
from .parity import fit_parity
from .skew import TARGET_TAU, curve_a, curve_b, skew_from_curve
from .surfaces import SmileObs, fit_method_a, fit_method_b

NY = "America/New_York"
RAW = Path("data/raw")


@dataclass(frozen=True)
class QuoteFilter:
    max_age_s: float = 120.0
    max_rel_spread: float = 0.50
    label: str = "primary"


FILTERS = {
    "primary": QuoteFilter(),
    "age60": QuoteFilter(60.0, 0.50, "age60"),
    "age180": QuoteFilter(180.0, 0.50, "age180"),
    "spr025": QuoteFilter(120.0, 0.25, "spr025"),
    "spr100": QuoteFilter(120.0, 1.00, "spr100"),
}


# Frozen convention (D-006): every PM expiry at 16:00 NY. Cboe: expiring SPXW trading ceases at
# 1:00 pm ET on half-day holidays, so the corrected instant is the early close. The correction is
# OFF by default to keep the frozen, reported results reproducible; it is used only for the
# post-freeze sensitivity in diagnostics.py (D-024).
EARLY_CLOSE_EXPIRY = False


def expiry_instant(exp_yymmdd: str) -> pd.Timestamp:
    d = pd.to_datetime(exp_yymmdd, format="%y%m%d").date()
    if EARLY_CLOSE_EXPIRY:
        ss = eligible_sessions(d, d)
        if ss and ss[0].early_close:
            return ss[0].close_utc
    return pd.Timestamp(f"{d} 16:00", tz=NY).tz_convert("UTC")  # D-006 / D-015


def load_quotes(day: str) -> pd.DataFrame:
    sel = json.loads(Path(f"data/selection/{day}.json").read_text())
    from .acquire import CHUNK, _iso
    from .fetch import Request

    s = {x.day.isoformat(): x for x in eligible_sessions(day, day)}[day]
    frames = []
    for i in range(0, len(sel["symbols"]), CHUNK):
        r = Request("OPRA.PILLAR", "cbbo-1m", sel["symbols"][i:i + CHUNK], _iso(s.open_utc),
                    _iso(s.close_utc))
        frames.append(db.DBNStore.from_file(r.path(RAW)).to_df(price_type="float").reset_index())
    q = pd.concat(frames, ignore_index=True)
    q["exp"] = q["symbol"].str[6:12]
    q["right"] = q["symbol"].str[12]
    q["K"] = q["symbol"].str[13:].astype(int) / 1000.0
    return q.sort_values("ts_recv").reset_index(drop=True)


def snapshot(q: pd.DataFrame, cutoff: pd.Timestamp, flt: QuoteFilter) -> tuple[pd.DataFrame, dict]:
    """Latest record per instrument with interval end <= cutoff (never ts_event), filtered."""
    sub = q[q.ts_recv <= cutoff]
    last = sub.groupby("symbol", sort=False).tail(1)
    age = (cutoff - last.ts_recv).dt.total_seconds()
    mid = (last.bid_px_00 + last.ask_px_00) / 2
    cnt = {"listed_with_record": int(len(last))}
    m_age = age <= flt.max_age_s
    m_two = ((last.bid_px_00 > 0) & np.isfinite(last.bid_px_00) & np.isfinite(last.ask_px_00)
             & (last.ask_px_00 >= last.bid_px_00) & (last.bid_sz_00 > 0) & (last.ask_sz_00 > 0)
             & (mid > 0))
    rel = (last.ask_px_00 - last.bid_px_00) / mid
    m_spr = rel <= flt.max_rel_spread
    cnt |= {"stale": int((~m_age).sum()), "not_two_sided": int((m_age & ~m_two).sum()),
            "wide_spread": int((m_age & m_two & ~m_spr).sum())}
    ok = m_age & m_two & m_spr
    out = last[ok].copy()
    out["age_s"] = age[ok]
    out["rel_spread"] = rel[ok]
    cnt["usable"] = int(len(out))
    return out, cnt


def expiry_obs(g: pd.DataFrame, tau: float):
    """Parity fit + normalized observations for one expiry; returns (fit, SmileObs, diag)."""
    c = g[g.right == "C"].set_index("K")
    p = g[g.right == "P"].set_index("K")
    Ks = np.array(sorted(set(c.index) & set(p.index)))
    fit = fit_parity(Ks, c.loc[Ks, "bid_px_00"] if len(Ks) else [], c.loc[Ks, "ask_px_00"] if len(Ks) else [],
                     p.loc[Ks, "bid_px_00"] if len(Ks) else [], p.loc[Ks, "ask_px_00"] if len(Ks) else [], tau)
    diag = {"pairs": int(len(Ks)), "parity_ok": fit.ok, "parity_reason": fit.reason,
            "F": fit.A / fit.B if fit.ok else np.nan, "D": fit.B if fit.ok else np.nan,
            "parity_violations": fit.n_interval_violations}
    if not fit.ok:
        return fit, None, diag
    xs, mids, los, his, isp = [], [], [], [], []
    for K, row in c.iterrows():
        x = K / fit.F
        xs.append(x); isp.append(False)
        mids.append((row.bid_px_00 + row.ask_px_00) / 2 / fit.A)
        los.append(row.bid_px_00 / fit.A); his.append(row.ask_px_00 / fit.A)
    for K, row in p.iterrows():
        x = K / fit.F
        xs.append(x); isp.append(True)
        mids.append((row.bid_px_00 + row.ask_px_00) / 2 / fit.A + 1 - x)
        los.append(row.bid_px_00 / fit.A + 1 - x); his.append(row.ask_px_00 / fit.A + 1 - x)
    o = SmileObs(np.array(xs), np.array(mids), np.array(los), np.array(his), np.array(isp))
    ux = np.unique(o.x)
    diag |= {"distinct_strikes": int(len(ux)), "below_F": int((ux < 1).sum()),
             "above_F": int((ux > 1).sum())}
    return fit, o, diag


def usable_expiry(diag: dict) -> bool:
    return (diag["parity_ok"] and diag["pairs"] >= 4 and diag.get("distinct_strikes", 0) >= 8
            and diag.get("below_F", 0) >= 1 and diag.get("above_F", 0) >= 1)


def measure_day(day: str, lam: float, flt: QuoteFilter = FILTERS["primary"],
                cutoffs: list[pd.Timestamp] | None = None, methods=("A", "B")) -> list[dict]:
    s = {x.day.isoformat(): x for x in eligible_sessions(day, day)}[day]
    q = load_quotes(day)
    cuts = list(s.surface_cutoffs()) if cutoffs is None else cutoffs
    rows = []
    warm: dict[str, np.ndarray] = {}
    for cut in cuts:
        t0 = time.perf_counter()
        snap, cnt = snapshot(q, cut, flt)
        per = {}
        for exp, g in snap.groupby("exp"):
            ei = expiry_instant(exp)
            tau = (ei - cut).total_seconds() / (365 * 86400)
            if not (14 / 365 <= tau <= 90 / 365):
                continue
            fit, o, diag = expiry_obs(g, tau)
            per[exp] = (tau, fit, o, diag)
        good = {e: v for e, v in per.items() if usable_expiry(v[3])}
        below = sorted([e for e, v in good.items() if v[0] <= TARGET_TAU], key=lambda e: good[e][0])
        above = sorted([e for e, v in good.items() if v[0] >= TARGET_TAU], key=lambda e: good[e][0])
        base = {"date": day, "cutoff": cut, "filter": flt.label, **cnt,
                "n_expiries_in_window": len(per), "n_expiries_usable": len(good),
                "expiry_diags": json.dumps({e: v[3] for e, v in per.items()}, default=float)}
        if below and math.isclose(good[below[-1]][0], TARGET_TAU, abs_tol=1e-9):
            e1, e2 = below[-1], None
        elif below and above:
            e1, e2 = below[-1], above[0]
        else:
            for mth in methods:
                rows.append(base | {"method": mth, "ok": False, "reason": "no usable 30d bracket",
                                    "fit_seconds": time.perf_counter() - t0})
            continue
        t1, _, o1, _ = good[e1]
        t2 = good[e2][0] if e2 else None
        o2 = good[e2][2] if e2 else None
        for mth in methods:
            tm = time.perf_counter()
            rec = base | {"method": mth, "exp_lo": e1, "exp_hi": e2, "tau_lo_d": t1 * 365,
                          "tau_hi_d": t2 * 365 if t2 else np.nan,
                          "F_lo": good[e1][3]["F"], "F_hi": good[e2][3]["F"] if e2 else np.nan}
            try:
                if mth == "A":
                    f1 = fit_method_a(o1, lam)
                    f2 = fit_method_a(o2, lam) if e2 else None
                    if not f1.ok or (e2 and not f2.ok):
                        raise ValueError(f"Method A fit failed: {f1.reason or (f2.reason if f2 else '')}")
                    rec["band_violation"] = max(f1.band_violation, f2.band_violation if f2 else 0)
                    st = skew_from_curve(*curve_a(f1, t1, f2, t2))
                else:
                    f1 = fit_method_b(o1, t1, warm.get(e1))
                    f2 = fit_method_b(o2, t2, warm.get(e2)) if e2 else None
                    if not f1.ok or (e2 and not f2.ok):
                        raise ValueError(f"SVI fit failed: {f1.reason or (f2.reason if f2 else '')}")
                    warm[e1] = f1.params  # past-fit warm start only
                    if e2:
                        warm[e2] = f2.params
                    rec["svi_dense_ok"] = all(f1.dense_checks.values()) and (
                        all(f2.dense_checks.values()) if f2 else True)
                    st = skew_from_curve(*curve_b(f1, t1, f2, t2))
                rec |= {"ok": st.ok, "reason": st.reason, "sk30": st.sk30, "iv_put25": st.iv_put25,
                        "iv_call25": st.iv_call25, "atm_iv": st.atm_iv, "x_put25": st.x_put25,
                        "x_call25": st.x_call25, "support_lo": st.support[0], "support_hi": st.support[1]}
            except ValueError as e:
                rec |= {"ok": False, "reason": str(e)[:200]}
            rec["fit_seconds"] = (time.perf_counter() - t0) if mth == methods[0] else (time.perf_counter() - tm)
            rows.append(rec)
    for r in rows:  # availability no earlier than cutoff + 5 s, later if fitting took longer
        r["feature_available_time"] = r["cutoff"] + pd.Timedelta(seconds=max(5.0, r.get("fit_seconds", 0)))
    return rows
