"""Quote-perturbation measurement stress (spec §18). Calendar-selected snapshots only:
12:00 NY on the first eligible session of each final calendar month plus the pilot dates.
25 draws: every quote's bid and ask are replaced by a premium drawn uniformly inside its own
[bid, ask] interval (as a narrow pseudo-quote of half-width SPREAD_FLOOR-scale), parity and
both surfaces are refit, and SK30 is recorded. These are measurement stress ranges, not
statistical confidence intervals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .calendars import eligible_sessions
from .measure import FILTERS, expiry_instant, expiry_obs, load_quotes, snapshot, usable_expiry
from .skew import TARGET_TAU, curve_a, curve_b, skew_from_curve
from .surfaces import fit_method_a, fit_method_b

NY = "America/New_York"
N_DRAWS, SEED = 25, 1729


def stress_dates() -> list[str]:
    out = []
    for m in ("2025-09", "2025-10", "2025-11", "2025-12"):
        ss = [s.day.isoformat() for s in eligible_sessions(f"{m}-01", f"{m}-28")]
        out.append(ss[0])
    return out + ["2024-02-01", "2024-08-01"]


def skew_from_snapshot(snap: pd.DataFrame, cut, lam: float) -> dict:
    per = {}
    for exp, g in snap.groupby("exp"):
        tau = (expiry_instant(exp) - cut).total_seconds() / (365 * 86400)
        if 14 / 365 <= tau <= 90 / 365:
            fit, o, diag = expiry_obs(g, tau)
            if usable_expiry(diag):
                per[exp] = (tau, o)
    below = sorted([e for e in per if per[e][0] <= TARGET_TAU], key=lambda e: per[e][0])
    above = sorted([e for e in per if per[e][0] >= TARGET_TAU], key=lambda e: per[e][0])
    if not (below and above):
        return {"A": np.nan, "B": np.nan, "fail": "no bracket"}
    (t1, o1), (t2, o2) = per[below[-1]], per[above[0]]
    out = {}
    try:
        st = skew_from_curve(*curve_a(fit_method_a(o1, lam), t1, fit_method_a(o2, lam), t2))
        out["A"] = st.sk30 if st.ok else np.nan
    except ValueError:
        out["A"] = np.nan
    try:
        b1, b2 = fit_method_b(o1, t1), fit_method_b(o2, t2)
        st = skew_from_curve(*curve_b(b1, t1, b2, t2)) if (b1.ok and b2.ok) else None
        out["B"] = st.sk30 if st is not None and st.ok else np.nan
    except ValueError:
        out["B"] = np.nan
    return out


def run(lam: float) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    rows = []
    for day in stress_dates():
        if not Path(f"data/selection/{day}.json").exists():
            rows.append({"date": day, "draw": -1, "note": "quotes not acquired"})
            continue
        q = load_quotes(day)
        cut = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
        snap, _ = snapshot(q, cut, FILTERS["primary"])
        base = skew_from_snapshot(snap, cut, lam)
        rows.append({"date": day, "draw": 0, "A": base["A"], "B": base["B"], "note": "observed mids"})
        for d in range(1, N_DRAWS + 1):
            p = snap.copy()
            u = rng.uniform(size=len(p))
            prem = p.bid_px_00 + u * (p.ask_px_00 - p.bid_px_00)
            half = np.minimum((p.ask_px_00 - p.bid_px_00) / 2, 0.05)  # narrow pseudo-quote
            p["bid_px_00"], p["ask_px_00"] = (prem - half).clip(lower=0.0), prem + half
            r = skew_from_snapshot(p, cut, lam)
            rows.append({"date": day, "draw": d, "A": r["A"], "B": r["B"], "note": r.get("fail", "")})
    df = pd.DataFrame(rows)
    out = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "measurement_stress_draws.csv", index=False)
    summ = df[df.draw > 0].groupby("date").agg(
        A_min=("A", "min"), A_max=("A", "max"), A_sd=("A", "std"), A_fail=("A", lambda v: int(v.isna().sum())),
        B_min=("B", "min"), B_max=("B", "max"), B_sd=("B", "std"), B_fail=("B", lambda v: int(v.isna().sum())))
    summ = summ.join(df[df.draw == 0].set_index("date")[["A", "B"]].rename(columns={"A": "A_observed", "B": "B_observed"}))
    summ.to_csv(out / "measurement_stress.csv")
    return summ


if __name__ == "__main__":
    import sys

    print(run(float(sys.argv[1])).round(5).to_string())
    print(json.dumps({"dates": stress_dates()}))
