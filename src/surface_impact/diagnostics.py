"""Descriptive and measurement diagnostics for the paper/appendix, from saved artifacts only.

Nothing here changes the frozen design or the primary estimate. The full-coefficient bootstrap
re-estimates the *same frozen* M_SKEW model on the final period to report every coefficient with
block-bootstrap SEs (same blocks, replications and seed as the primary).
Outputs: outputs/main/diagnostics.json, tables/*.csv, figures/fig6_skew_timeseries.png
"""

from __future__ import annotations

import glob
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .budget import BudgetConfig, Ledger
from .calendars import eligible_sessions
from .features import SPLITS_12M
from .inference import bootstrap
from .measure import expiry_instant
from .regressions import Transforms, design, eligible_rows, ols

OUT = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
TAB = OUT / "tables"
NY = "America/New_York"


def _q(s, qs=(0.05, 0.5, 0.95)):
    return [float(v) for v in np.nanquantile(s, qs)]


def descriptives(panel, cov):
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == "A_primary") & panel.date.isin(qual)]
    p = p[eligible_rows(p, "y_5")].copy()
    p["sk30_vp"] = p.sk30 * 100
    p["atm_iv_pct"] = p.atm_iv * 100
    cols = {"ofi_norm": "Normalized OFI (sampled)", "depth": "Top depth (contracts)",
            "spread_bps": "Relative spread (bps)", "r_flow": "Flow-minute return (bps)",
            "y_5": "5-min future return (bps)", "rv30": "RV proxy (bps/min)",
            "sk30_vp": "SK30 (vol pts)", "atm_iv_pct": "30d ATM IV (%)", "state_age_s": "State age (s)"}
    rows = []
    for split in ("dev", "val", "final"):
        g = p[p.split == split]
        for c, lab in cols.items():
            q5, q50, q95 = _q(g[c])
            rows.append({"split": split, "variable": lab, "mean": float(g[c].mean()), "sd": float(g[c].std()),
                         "p5": q5, "median": q50, "p95": q95, "n": int(g[c].notna().sum())})
    df = pd.DataFrame(rows)
    df.to_csv(TAB / "descriptives.csv", index=False)
    return df


def selection_stats():
    rows = []
    for f in sorted(glob.glob("data/selection/2025-*.json")):
        d = json.loads(Path(f).read_text())
        rows.append({"date": d["date"], "n_selected": d["n_selected"], "n_expiries": len(d["expiries"]),
                     "band_width_pts": d["band"][1] - d["band"][0]})
    s = pd.DataFrame(rows)
    return {"sessions": int(len(s)), "contracts_mean": float(s.n_selected.mean()),
            "contracts_min": int(s.n_selected.min()), "contracts_max": int(s.n_selected.max()),
            "expiries_per_session": s.n_expiries.value_counts().sort_index().to_dict()}


def quote_quality(states):
    a = states[states.variant == "A_primary"].copy()
    a["usable_share"] = a.usable / a.listed_with_record
    t = pd.to_datetime(a.cutoff, utc=True).dt.tz_convert(NY)
    a["tod"] = np.where(t.dt.hour < 11, "09:35-10:59", np.where(t.dt.hour < 14, "11:00-13:59", "14:00-close"))
    by_tod = a.groupby("tod").agg(usable_share=("usable_share", "mean"), state_ok=("ok", "mean")).round(4)
    okd = a[a.ok]
    corr = float(okd[["usable_share", "atm_iv"]].corr().iloc[0, 1])
    fail = a[~a.ok].reason.fillna("").str.slice(0, 60).value_counts().to_dict()
    q_atm = pd.qcut(okd.atm_iv, 4, labels=["Q1 (low IV)", "Q2", "Q3", "Q4 (high IV)"])
    by_iv = okd.groupby(q_atm, observed=True).usable_share.mean().round(4).to_dict()
    return {"cutoffs": int(len(a)), "state_ok_rate": float(a.ok.mean()),
            "mean_listed_with_record": float(a.listed_with_record.mean()),
            "mean_usable": float(a.usable.mean()), "mean_stale": float(a.stale.mean()),
            "mean_not_two_sided": float(a.not_two_sided.mean()), "mean_wide_spread": float(a.wide_spread.mean()),
            "usable_share_by_time_of_day": by_tod.usable_share.to_dict(),
            "state_ok_by_time_of_day": by_tod.state_ok.to_dict(),
            "usable_share_by_atm_iv_quartile": {str(k): v for k, v in by_iv.items()},
            "corr_usable_share_atm_iv": corr, "failure_reasons": fail,
            "band_violation_mean": float(okd.band_violation.mean()),
            "band_violation_p95": float(okd.band_violation.quantile(0.95))}


def parity_stats():
    diag = pd.read_parquet(OUT / "expiry_diagnostics.parquet")
    diag = diag[diag.variant == "A_primary"]
    recs = []
    for r in diag.itertuples():
        cut = pd.Timestamp(r.cutoff)
        for exp, d in json.loads(r.expiry_diags).items():
            tau = (expiry_instant(exp) - cut).total_seconds() / (365 * 86400)
            rate = -math.log(d["D"]) / tau if d.get("parity_ok") and d.get("D") and d["D"] > 0 else np.nan
            recs.append({"ok": bool(d.get("parity_ok")), "pairs": d.get("pairs", 0),
                         "viol": d.get("parity_violations", 0), "rate": rate,
                         "reason": d.get("parity_reason", "")})
    e = pd.DataFrame(recs)
    okr = e[e.ok]
    return {"expiry_fits": int(len(e)), "parity_ok_rate": float(e.ok.mean()),
            "pairs_median": float(okr.pairs.median()),
            "interval_violations_mean": float(okr.viol.mean()),
            "share_fits_with_any_violation": float((okr.viol > 0).mean()),
            "implied_rate_pct_quantiles_5_50_95": [100 * v for v in _q(okr.rate)],
            "parity_fail_reasons": e[~e.ok].reason.str.slice(0, 50).value_counts().head(5).to_dict()}


def method_agreement(states):
    a = states[(states.variant == "A_primary") & states.ok][["date", "cutoff", "sk30", "atm_iv"]]
    b = states[(states.variant == "B_primary") & states.ok][["date", "cutoff", "sk30", "atm_iv", "svi_dense_ok"]]
    m = a.merge(b, on=["date", "cutoff"], suffixes=("_A", "_B"))
    d = (m.sk30_B - m.sk30_A) * 100
    return {"common_states": int(len(m)), "corr_sk30_A_B": float(m[["sk30_A", "sk30_B"]].corr().iloc[0, 1]),
            "mean_diff_B_minus_A_volpts": float(d.mean()), "mean_abs_diff_volpts": float(d.abs().mean()),
            "p95_abs_diff_volpts": float(d.abs().quantile(0.95)),
            "corr_atm_iv_A_B": float(m[["atm_iv_A", "atm_iv_B"]].corr().iloc[0, 1]),
            "svi_dense_check_pass_rate": float(b.svi_dense_ok.astype(float).mean())}


def skew_figure(states):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = states[states.ok & states.variant.isin(["A_primary", "B_primary"])]
    daily = s.groupby(["date", "variant"])[["sk30", "atm_iv"]].mean().reset_index()
    daily["d"] = pd.to_datetime(daily.date)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 3.9), sharex=True)
    for v, col, ls in (("A_primary", "#2a6f97", "-"), ("B_primary", "#c8553d", "--")):
        g = daily[daily.variant == v]
        ax1.plot(g.d, g.sk30 * 100, color=col, ls=ls, lw=1, label="Method A" if v[0] == "A" else "Method B (SVI)")
    g = daily[daily.variant == "A_primary"]
    ax2.plot(g.d, g.atm_iv * 100, color="#555", lw=1)
    for ax in (ax1, ax2):
        for k, (a, b) in SPLITS_12M.items():
            if k != "dev":
                ax.axvline(pd.Timestamp(a), color="#999", lw=0.7, ls=":")
        ax.spines[["top", "right"]].set_visible(False)
    ax1.set_ylabel("SK30 (vol pts)", fontsize=8)
    ax2.set_ylabel("30d ATM IV (%)", fontsize=8)
    ax1.legend(fontsize=7, frameon=False)
    ax1.set_title("HISTORICAL 2025: daily mean of 5-minute SPXW skew states (dotted: val / final start)", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "figures" / "fig6_skew_timeseries.png", dpi=150)
    plt.close(fig)


def full_coefficients(panel, cov, tr):
    grid = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))["final_grid"]
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == "A_primary") & (panel.split == "final") & panel.date.isin(qual)]
    fin = p[eligible_rows(p, "y_5")]
    X, names = design(fin, tr, "M_SKEW")

    def stat(d):
        Xd, _ = design(d, tr, "M_SKEW")
        return ols(Xd, d.y_5.to_numpy(), names).coef

    b = bootstrap(stat, fin, grid, L=5, B=1999, seed=1729)
    tab = pd.DataFrame({"term": names, "estimate": b["theta"], "se_boot": b["se_boot"],
                        "ci_lo": b["ci_basic"][0], "ci_hi": b["ci_basic"][1], "p_null_centered": b["p_null_centered"]})
    tab.to_csv(TAB / "final_M_SKEW_all_coefficients.csv", index=False)
    return tab


def costs():
    L = pd.read_csv("data/ledger/cost_ledger.csv")
    c = L[L.event == "consume"]
    by = c.groupby("schema").amount_usd.sum()
    total = Ledger("data/ledger/cost_ledger.csv", BudgetConfig.load("configs/budget.yaml")).summary()["consumed"]
    return {"by_schema_usd": {k: round(float(v), 3) for k, v in by.items()},
            "explicit_consume_total": round(float(c.amount_usd.sum()), 3),
            "conservative_total": round(float(total), 3),
            "legacy_conservative_releases": round(float(total - c.amount_usd.sum()), 3),
            "failed_download_conservative": round(float(c[c.billing_basis != "metadata_estimate"].amount_usd.sum()), 3)}


def main():
    TAB.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(OUT / "minute_panel.parquet")
    cov = pd.read_csv(OUT / "coverage.csv")
    states = pd.read_parquet(OUT / "skew_states.parquet")
    tr = Transforms(**json.loads((OUT / "transforms.json").read_text(encoding="utf-8-sig")))
    R = json.loads((OUT / "results.json").read_text(encoding="utf-8-sig"))
    out = {"selection": selection_stats(), "quote_quality": quote_quality(states),
           "parity": parity_stats(), "method_agreement": method_agreement(states), "costs": costs()}
    descriptives(panel, cov)
    skew_figure(states)
    se = R["final"]["primary"]["se_boot"]
    out["mde_80pct_power_two_sided_5pct"] = {"approx_formula": "(1.96 + 0.84) x bootstrap SE",
                                             "se_boot": se, "mde": 2.80 * se}
    tab = full_coefficients(panel, cov, tr)
    out["full_coefficients_rows"] = int(len(tab))
    out["roll_days_2025"] = pd.read_csv(OUT / "roll_calendar.csv").query("roll_day").to_dict("records")
    out["sessions_scheduled"] = {k: len(eligible_sessions(*v)) for k, v in SPLITS_12M.items()}
    (OUT / "diagnostics.json").write_text(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    o = main()
    print(json.dumps({k: v for k, v in o.items() if k != "roll_days_2025"}, indent=1, default=str)[:5000])
