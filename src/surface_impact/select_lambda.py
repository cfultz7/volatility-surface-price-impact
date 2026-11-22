"""Method A roughness selection by grouped held-out strike pairs (spec §11).

Development sessions only (Jan-Jun 2025), 12:00 NY cutoff (calendar rule), primary quote
filter, the two 30-day bracketing usable expiries. For each expiry, distinct strikes are
ranked and assigned to 5 folds by rank mod 5. A fold holds out *all* quotes (call and put)
at its strikes. Parity is re-estimated on the training pairs only; Method A is fit on the
training observations for each lambda; held-out quotes inside the training support are
predicted (puts via the training parity relation) and scored by the dimensionless loss
mean(((pred - mid) / max(half_spread, floor))^2) in normalized units. Folds whose training
parity is unidentified are skipped with a recorded reason. No return data are used.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .calendars import eligible_sessions
from .measure import FILTERS, expiry_instant, expiry_obs, load_quotes, snapshot, usable_expiry
from .skew import TARGET_TAU
from .surfaces import LAMBDA_GRID, SPREAD_FLOOR, fit_method_a

NY = "America/New_York"
K_FOLDS = 5


def day_losses(day: str) -> list[dict]:
    try:
        q = load_quotes(day)
    except FileNotFoundError:
        return [{"date": day, "skip": "no quotes"}]
    cut = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
    snap, _ = snapshot(q, cut, FILTERS["primary"])
    per = {}
    for exp, g in snap.groupby("exp"):
        tau = (expiry_instant(exp) - cut).total_seconds() / (365 * 86400)
        if 14 / 365 <= tau <= 90 / 365:
            fit, o, diag = expiry_obs(g, tau)
            if usable_expiry(diag):
                per[exp] = (tau, g)
    below = sorted([e for e in per if per[e][0] <= TARGET_TAU], key=lambda e: per[e][0])
    above = sorted([e for e in per if per[e][0] >= TARGET_TAU], key=lambda e: per[e][0])
    chosen = ([below[-1]] if below else []) + ([above[0]] if above else [])
    out = []
    for exp in dict.fromkeys(chosen):
        tau, g = per[exp]
        strikes = np.array(sorted(g.K.unique()))
        fold_of = {k: i % K_FOLDS for i, k in enumerate(strikes)}
        for f in range(K_FOLDS):
            test_k = {k for k, v in fold_of.items() if v == f}
            tr, te = g[~g.K.isin(test_k)], g[g.K.isin(test_k)]
            fit, o, diag = expiry_obs(tr, tau)  # parity re-estimated without held-out pairs
            if o is None or not usable_expiry(diag):
                out.append({"date": day, "exp": exp, "fold": f, "skip": "training parity/support"})
                continue
            lo, hi = o.x.min(), o.x.max()
            x = te.K.to_numpy() / fit.F
            inside = (x >= lo) & (x <= hi)
            te, x = te[inside], x[inside]
            if len(te) == 0:
                out.append({"date": day, "exp": exp, "fold": f, "skip": "no held-out inside support"})
                continue
            mid = ((te.bid_px_00 + te.ask_px_00) / 2).to_numpy() / fit.A
            hs = np.maximum(((te.ask_px_00 - te.bid_px_00) / 2).to_numpy() / fit.A, SPREAD_FLOOR)
            isput = (te.right == "P").to_numpy()
            rec = {"date": day, "exp": exp, "fold": f, "n_test": int(len(te))}
            for lam in LAMBDA_GRID:
                fa = fit_method_a(o, lam)
                if not fa.ok:
                    rec[f"loss_{lam}"] = np.nan
                    continue
                c = fa(x)
                pred = np.where(isput, c - (1 - x), c)  # normalized put via training parity
                rec[f"loss_{lam}"] = float(np.mean(((pred - mid) / hs) ** 2))
            out.append(rec)
    return out


def run(start="2025-01-01", end="2025-06-30", workers=10):
    days = [s.day.isoformat() for s in eligible_sessions(start, end)]
    days = [d for d in days if Path(f"data/selection/{d}.json").exists()]
    with ProcessPoolExecutor(workers) as ex:
        res = [r for rr in ex.map(day_losses, days) for r in rr]
    df = pd.DataFrame(res)
    Path("outputs/main").mkdir(parents=True, exist_ok=True)
    df.to_csv("outputs/main/lambda_cv_folds.csv", index=False)
    scored = df.dropna(subset=[f"loss_{l}" for l in LAMBDA_GRID])
    summary = {str(l): float(scored[f"loss_{l}"].mean()) for l in LAMBDA_GRID}
    # per-day means, so heavily quoted days do not dominate (reported, not the rule)
    by_day = scored.groupby("date")[[f"loss_{l}" for l in LAMBDA_GRID]].mean().mean()
    best = min(LAMBDA_GRID, key=lambda l: summary[str(l)])
    rep = {"dev_sessions_used": int(scored.date.nunique()), "folds_scored": int(len(scored)),
           "folds_skipped": int(df["skip"].notna().sum()) if "skip" in df else 0,
           "skip_reasons": df["skip"].value_counts().to_dict() if "skip" in df else {},
           "mean_loss": summary, "mean_of_day_means": {k.replace('loss_', ''): float(v) for k, v in by_day.items()},
           "selected_lambda": best, "rule": "argmin of mean held-out loss over all scored folds"}
    Path("outputs/main/lambda_selection.json").write_text(json.dumps(rep, indent=2))
    return rep


if __name__ == "__main__":
    print(json.dumps(run(*sys.argv[1:3]) if len(sys.argv) > 2 else run(), indent=2))
