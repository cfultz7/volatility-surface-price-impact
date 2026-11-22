"""Main analysis driver (protocol v0.2, 12-month design). Stages run in this order:

  panel     -> outputs/main/minute_panel.parquet, coverage.csv, attrition.csv, roll_calendar.csv
  pretest   -> transforms + validation diagnostics (dev/val only; no final outcomes read)
  freeze    -> outputs/main/design_freeze.json (immutable; refuses to overwrite)
  final     -> association (bootstrap), secondary family (Holm), frozen forecasts, robustness

Final-period outcome columns are read only by functions called after the freeze file exists.
Estimation sample (D-020): valid joint rows of *qualifying* sessions (>= 75% of scheduled
endpoints valid for the primary specification).
"""

from __future__ import annotations

import hashlib
import os
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .features import HORIZONS, SPLITS_12M, session_panel
from .inference import bootstrap
from .regressions import CONTROLS, cluster_se, design, eligible_rows, fit_transforms, ols

OUT = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
PRIMARY = "A_primary"
VARIANTS = ["A_primary", "B_primary", "A_age60", "A_age180", "A_spr025", "A_spr100"]
B_REPS, SEED, BLOCK = 1999, 1729, 5
NS = 1_000_000_000
STATES = Path(os.environ.get("VSIMPACT_STATES", "data/derived/states"))


# ------------------------------------------------------------------------- panel ---------
def _panel_day(day: str) -> pd.DataFrame | None:
    p = STATES / f"{day}.pkl"
    if not p.exists():
        return None
    st = pd.read_pickle(p)
    by = {v: st[st.variant == v] for v in VARIANTS}
    return session_panel(day, by)


def build_panel(workers=10):
    OUT.mkdir(parents=True, exist_ok=True)
    sessions = eligible_sessions("2025-01-01", "2025-12-31")
    days = [s.day.isoformat() for s in sessions]
    with ProcessPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(_panel_day, days) if p is not None]
    panel = pd.concat(parts, ignore_index=True)
    panel.to_parquet(OUT / "minute_panel.parquet")
    # coverage / attrition over every planned session
    cov = []
    prim = panel[panel.variant == PRIMARY]
    sched = active_contract_schedule([s.day for s in sessions])
    for s in sessions:
        d = s.day.isoformat()
        n_sched = len(s.observation_endpoints())
        g = prim[prim.date == d]
        rec = {"date": d, "split": _split(d), "scheduled_endpoints": n_sched,
               "early_close": s.early_close, "es_contract": sched[s.day][0].candidate_raw_symbol(),
               "roll_day": sched[s.day][1], "has_data": len(g) > 0}
        if len(g):
            rec |= {"flow_ok": int(g.flow_ok.sum()), "state_present": int(g.sk30.notna().sum()),
                    "y5_present": int(g.y_5.notna().sum()),
                    "joint_valid": int(eligible_rows(g, "y_5").sum())}
        else:
            rec |= {"flow_ok": 0, "state_present": 0, "y5_present": 0, "joint_valid": 0}
        rec["coverage"] = rec["joint_valid"] / n_sched
        rec["qualifying"] = rec["coverage"] >= 0.75
        cov.append(rec)
    cov = pd.DataFrame(cov)
    cov.to_csv(OUT / "coverage.csv", index=False)
    cov[["date", "es_contract", "roll_day"]].to_csv(OUT / "roll_calendar.csv", index=False)
    # attrition: state failure reasons (primary variant) + flow/outcome missingness
    st = pd.concat([pd.read_pickle(p) for p in sorted(STATES.glob("2025-*.pkl"))])
    stp = st[st.variant == PRIMARY]
    att = stp.assign(reason=stp["reason"].fillna("").where(~stp.ok, "ok")).groupby(
        ["reason"]).size().rename("n_cutoffs").reset_index()
    att.to_csv(OUT / "attrition.csv", index=False)
    return panel, cov


def _split(d):
    for k, (a, b) in SPLITS_12M.items():
        if a <= d <= b:
            return k
    return "outside"


def gates(cov: pd.DataFrame) -> dict:
    q = cov[cov.qualifying]
    n = {k: int((q.split == k).sum()) for k in ("dev", "val", "final")}
    g = {"total": int(len(q)), **n,
         "pass_total": len(q) >= 150, "pass_dev": n["dev"] >= 70, "pass_val": n["val"] >= 25,
         "pass_final": n["final"] >= 50}
    g["all_pass"] = all(v for k, v in g.items() if k.startswith("pass_"))
    return g


# ---------------------------------------------------------------------- pretest ----------
def pretest(panel, cov, variant=PRIMARY):
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == variant) & panel.date.isin(qual)]
    pre = p[p.split.isin(["dev", "val"])]
    pre = pre[eligible_rows(pre, "y_5")]
    tr = fit_transforms(pre)
    return pre, tr


def validation_diagnostics(panel, cov, tr):
    """Fit on dev, evaluate on val (pretest only). Diagnostics; never used to pick features."""
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == PRIMARY) & panel.date.isin(qual)]
    dev = p[(p.split == "dev")]
    val = p[(p.split == "val")]
    dev, val = dev[eligible_rows(dev, "y_5")], val[eligible_rows(val, "y_5")]
    out = {"dev_rows": len(dev), "val_rows": len(val)}
    sse = {}
    for m in ("M_PRICE", "M_FLOW", "M_SKEW"):
        X, names = design(dev, tr, m)
        fit = ols(X, dev.y_5.to_numpy(), names)
        Xv, _ = design(val, tr, m)
        e = val.y_5.to_numpy() - Xv @ np.nan_to_num(fit.coef)
        sse[m] = float(e @ e)
        out[f"{m}_rank"] = fit.rank
        out[f"{m}_cond"] = fit.cond
    bench = val.y_5.to_numpy() - dev.y_5.mean()
    out["val_oos_r2"] = {m: 1 - sse[m] / float(bench @ bench) for m in sse}
    out["val_incremental_skew_vs_flow"] = 1 - sse["M_SKEW"] / sse["M_FLOW"]
    out["contemporaneous_corr_ofi_rflow_dev"] = float(dev[["ofi_norm", "r_flow"]].corr().iloc[0, 1])
    return out


# ----------------------------------------------------------------------- freeze ----------
def src_hash() -> str:
    h = hashlib.sha256()
    for f in sorted(Path("src/surface_impact").glob("*.py")) + [Path("configs/protocol.yaml")]:
        h.update(f.read_bytes())
    return h.hexdigest()


def freeze(cov, tr, val_diag, lam_sel, gate):
    f = OUT / "design_freeze.json"
    if f.exists():
        raise FileExistsError("design_freeze.json exists; immutable. Write a new versioned file "
                              "only for exploratory changes.")
    final_grid = [s.day.isoformat() for s in eligible_sessions(*SPLITS_12M["final"])]
    doc = {
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_version": "0.2", "deviations": ["D-014 sampled OFI from ES bbo-1s"],
        "design": "12-month", "splits": SPLITS_12M, "final_grid": final_grid,
        "primary": {"coef": "delta_sell = coef(s*z)", "horizon_min": 5, "surface": "Method A",
                    "variant": PRIMARY, "test": "two-sided null-centered moving-block bootstrap",
                    "block_sessions": BLOCK, "replications": B_REPS, "seed": SEED},
        "secondary_family": {"tests": "b_z and s_z at h in {1,2,5,10,15} except primary s_z@5",
                             "adjustment": "Holm"},
        "controls": CONTROLS + ["weekday dummies"],
        "transforms": json.loads(tr.to_json()),
        "method_a_lambda": lam_sel, "estimation_sample": "valid joint rows of qualifying sessions (D-020)",
        "gates": gate, "validation_diagnostics": val_diag,
        "qualifying_sessions": cov[cov.qualifying].groupby("split").size().to_dict(),
        "source_sha256": src_hash(),
        "acquisition_ledger_sha256": hashlib.sha256(Path("data/ledger/cost_ledger.csv").read_bytes()).hexdigest(),
    }
    f.write_text(json.dumps(doc, indent=2, default=str))
    return doc


# ------------------------------------------------------------------------ final ----------
def _final_rows(panel, cov, variant, ycol, skew_col="sk30", ofi_col="ofi_norm", flow_col="flow_ok"):
    if not (OUT / "design_freeze.json").exists():
        raise RuntimeError("Final-period outcomes may only be read after the design freeze.")
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == variant) & (panel.split == "final") & panel.date.isin(qual)].copy()
    if ofi_col != "ofi_norm":
        p["ofi_norm"] = p[ofi_col]
        p["flow_ok"] = p[flow_col]
    return p[eligible_rows(p, ycol, skew_col)]


def _assoc_stat(ycols, tr):
    def stat(d):
        v = []
        for y in ycols:
            dd = d[d[y].notna()]
            X, names = design(dd, tr, "M_SKEW")
            f = ols(X, dd[y].to_numpy(), names)
            v += [f.get("s_z"), f.get("b_z"), f.get("s"), f.get("b")]
        return np.array(v)
    return stat


def holm(p: dict) -> dict:
    items = sorted(p.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - i) * v))
        out[k] = run
    return out


def run_final(panel, cov, tr):
    grid = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))["final_grid"]
    res = {}
    # --- primary + horizon family ----------------------------------------------------
    ycols = [f"y_{h}" for h in HORIZONS]
    fin = _final_rows(panel, cov, PRIMARY, "y_5")
    stat = _assoc_stat(ycols, tr)
    boot = bootstrap(stat, fin, grid, L=BLOCK, B=B_REPS, seed=SEED)
    names = [f"{c}@{h}" for h in HORIZONS for c in ("s_z", "b_z", "s", "b")]
    coef = pd.DataFrame({"term": names, "estimate": boot["theta"], "se_boot": boot["se_boot"],
                         "ci_lo": boot["ci_basic"][0], "ci_hi": boot["ci_basic"][1],
                         "pct_lo": boot["ci_percentile"][0], "pct_hi": boot["ci_percentile"][1],
                         "p_null_centered": boot["p_null_centered"]})
    sec = {r.term: r.p_null_centered for r in coef.itertuples()
           if r.term.split("@")[0] in ("s_z", "b_z") and r.term != "s_z@5"}
    hp = holm(sec)
    coef["p_holm_secondary"] = coef.term.map(hp)
    coef["role"] = np.where(coef.term == "s_z@5", "PRIMARY", np.where(coef.term.isin(hp), "secondary", "lower-order"))
    coef.to_csv(OUT / "coefficients.csv", index=False)
    i_sz, i_s = names.index("s_z@5"), names.index("s@5")
    D = boot["draws"]
    slopes = {}
    for zv in (-1, 0, 1):
        est = boot["theta"][i_s] + zv * boot["theta"][i_sz]
        dr = D[:, i_s] + zv * D[:, i_sz]
        q = np.percentile(dr, [2.5, 97.5])
        slopes[str(zv)] = {"estimate": est, "ci_basic": [2 * est - q[1], 2 * est - q[0]]}
    hl = 2 * boot["theta"][i_sz]
    q = np.percentile(2 * D[:, i_sz], [2.5, 97.5])
    res["primary"] = {"delta_sell": boot["theta"][i_sz], "ci_basic": [boot["ci_basic"][0][i_sz], boot["ci_basic"][1][i_sz]],
                      "ci_percentile": [boot["ci_percentile"][0][i_sz], boot["ci_percentile"][1][i_sz]],
                      "p_null_centered": boot["p_null_centered"][i_sz], "se_boot": boot["se_boot"][i_sz],
                      "n_success": boot["n_success"], "n_failed": boot["n_failed"],
                      "n_blocks_possible": boot["n_blocks_possible"],
                      "rows": len(fin), "sessions": fin.date.nunique(), "weeks": int(pd.to_datetime(fin.date).dt.isocalendar().week.nunique()),
                      "sell_slope_by_z": slopes, "high_minus_low_slope": {"estimate": hl, "ci_basic": [2 * hl - q[1], 2 * hl - q[0]]}}
    # block-length sensitivities + clustered SE (diagnostics)
    st5 = _assoc_stat(["y_5"], tr)
    for L in (1, 10):
        if len(grid) < L:
            res[f"block_{L}"] = {"note": f"not computable: {len(grid)} sessions < block length {L}"}
            continue
        b = bootstrap(st5, fin, grid, L=L, B=B_REPS, seed=SEED)
        res[f"block_{L}"] = {"delta_sell": b["theta"][0], "ci_basic": [b["ci_basic"][0][0], b["ci_basic"][1][0]],
                             "p_null_centered": b["p_null_centered"][0], "n_success": b["n_success"]}
    X, nm = design(fin, tr, "M_SKEW")
    f = ols(X, fin.y_5.to_numpy(), nm)
    se = cluster_se(X, fin.y_5.to_numpy(), f.coef, fin.date.to_numpy())
    res["day_clustered"] = {"delta_sell": f.get("s_z"), "se": float(se[nm.index("s_z")]),
                            "rank": f.rank, "cond": f.cond, "n": f.n}
    # nested in-sample fit on final (association, descriptive)
    r2 = {}
    for m in ("M_PRICE", "M_FLOW", "M_SKEW"):
        Xm, nmm = design(fin, tr, m)
        fm = ols(Xm, fin.y_5.to_numpy(), nmm)
        e = fin.y_5.to_numpy() - Xm @ np.nan_to_num(fm.coef)
        r2[m] = 1 - float(e @ e) / float(((fin.y_5 - fin.y_5.mean()) ** 2).sum())
    res["final_in_sample_r2"] = r2
    # contemporaneous diagnostic
    res["contemporaneous_corr_ofi_rflow_final"] = float(fin[["ofi_norm", "r_flow"]].corr().iloc[0, 1])
    # scale
    res["scale"] = {"sd_y5_bps": float(fin.y_5.std()), "median_spread_bps": float(fin.spread_bps.median()),
                    "ofi_sd_pretest": tr.ofi_sd, "skew_sd_pretest_volpts": tr.z_sd * 100}
    return res


def run_forecasts(panel, cov, tr):
    grid = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))["final_grid"]
    qual = set(cov[cov.qualifying].date)
    p = panel[(panel.variant == PRIMARY) & panel.date.isin(qual)]
    pre = p[p.split.isin(["dev", "val"])]
    pre = pre[eligible_rows(pre, "y_5")]
    fin = _final_rows(panel, cov, PRIMARY, "y_5")
    coefs, preds = {}, pd.DataFrame({"date": fin.date.to_numpy(), "t": fin.t.to_numpy(), "y": fin.y_5.to_numpy()})
    for m in ("M_PRICE", "M_FLOW", "M_SKEW"):
        X, nm = design(pre, tr, m)
        f = ols(X, pre.y_5.to_numpy(), nm)  # pretest-only coefficients, held fixed
        coefs[m] = dict(zip(nm, f.coef))
        Xf, _ = design(fin, tr, m)
        preds[m] = Xf @ np.nan_to_num(f.coef)
    preds["bench"] = float(pre.y_5.mean())
    preds.to_parquet(OUT / "forecast_predictions.parquet")
    rows = []
    for m in ("bench", "M_PRICE", "M_FLOW", "M_SKEW"):
        e = preds.y - preds[m]
        day = (e**2).groupby(preds.date).mean()
        rows.append({"model": m, "MAE": float(e.abs().mean()), "MSE": float((e**2).mean()),
                     "MSE_date_balanced": float(day.mean())})
    met = pd.DataFrame(rows).set_index("model")
    met["oos_R2_vs_pretest_mean"] = 1 - met.MSE / met.loc["bench", "MSE"]
    met["oos_R2_date_balanced"] = 1 - met.MSE_date_balanced / met.loc["bench", "MSE_date_balanced"]
    inc = 1 - met.loc["M_SKEW", "MSE"] / met.loc["M_FLOW", "MSE"]
    inc_db = 1 - met.loc["M_SKEW", "MSE_date_balanced"] / met.loc["M_FLOW", "MSE_date_balanced"]
    met.to_csv(OUT / "forecast_metrics.csv")

    def dstat(d):
        lf = ((d.y - d.M_FLOW) ** 2).sum()
        ls = ((d.y - d.M_SKEW) ** 2).sum()
        return np.array([1 - ls / lf, (lf - ls) / len(d)])
    b = bootstrap(dstat, preds, grid, L=BLOCK, B=B_REPS, seed=SEED)
    out = {"incremental_1_minus_SSE_ratio": inc, "incremental_date_balanced": inc_db,
           "incremental_ci_basic": [b["ci_basic"][0][0], b["ci_basic"][1][0]],
           "loss_diff_per_obs": b["theta"][1], "loss_diff_ci_basic": [b["ci_basic"][0][1], b["ci_basic"][1][1]],
           "n_success": b["n_success"], "rows": len(preds), "pretest_rows": len(pre)}
    pd.DataFrame(coefs).to_csv(OUT / "forecast_pretest_coefficients.csv")
    return out, met


def run_robustness(panel, cov, tr_primary):
    grid = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))["final_grid"]
    rows = []
    st5 = lambda tr: _assoc_stat(["y_5"], tr)  # noqa: E731

    def add(label, d, tr, note=""):
        if d.date.nunique() < 10:
            rows.append({"check": label, "rows": len(d), "sessions": d.date.nunique(), "note": "too few sessions"})
            return
        b = bootstrap(st5(tr), d, grid, L=BLOCK, B=B_REPS, seed=SEED)
        rows.append({"check": label, "delta_sell": b["theta"][0], "ci_lo": b["ci_basic"][0][0],
                     "ci_hi": b["ci_basic"][1][0], "p_null_centered": b["p_null_centered"][0],
                     "rows": len(d), "sessions": d.date.nunique(), "note": note})

    prim = _final_rows(panel, cov, PRIMARY, "y_5")
    add("primary (Method A, h=5)", prim, tr_primary)
    key = ["date", "t"]
    for v in VARIANTS[1:]:
        _, trv = pretest(panel, cov, v)
        dv = _final_rows(panel, cov, v, "y_5")
        add(f"{v}: separate coverage", dv, trv)
        common = dv.merge(prim[key], on=key)
        add(f"{v}: common observations", common, trv)
    add("delayed endpoint (t+5s -> t+5s+5m)", _final_rows(panel, cov, PRIMARY, "yd_5").assign(y_5=lambda d: d.yd_5), tr_primary)
    add("flow gap threshold 10s", _final_rows(panel, cov, PRIMARY, "y_5", ofi_col="ofi_norm_10s", flow_col="flow_ok_10s"), tr_primary)
    add("exclude roll-day sessions", prim[~prim.roll_day.astype(bool)], tr_primary)
    for mth, g in prim.groupby(prim.date.str[:7]):
        rows.append({"check": f"calendar month {mth} (small-sample caution)", **_point(g, tr_primary)})
    # placebo: substitute the state from 5 eligible sessions earlier at the same session minute
    allp = panel[panel.variant == PRIMARY][["date", "sess_min", "sk30"]]
    days = sorted(panel.date.unique())
    lag = {d: days[i - 5] for i, d in enumerate(days) if i >= 5}
    pl = prim.copy()
    pl["lag_date"] = pl.date.map(lag)
    pl = pl.drop(columns=["sk30"]).merge(allp.rename(columns={"date": "lag_date"}), on=["lag_date", "sess_min"], how="inner")
    pl = pl[pl.sk30.notna()]
    add("PLACEBO: state from 5 sessions earlier, same minute", pl, tr_primary,
        "breaks same-day state/flow alignment; keeps slow-moving skew level and seasonality")
    rob = pd.DataFrame(rows)
    rob.to_csv(OUT / "robustness.csv", index=False)
    # descriptive bins (plots only): pretest 20/80 skew cutoffs
    qual = set(cov[cov.qualifying].date)
    pre = panel[(panel.variant == PRIMARY) & panel.split.isin(["dev", "val"]) & panel.date.isin(qual)]
    lo, hi = np.nanpercentile(pre.sk30, [20, 80])
    bins = {}
    for name, m in (("low", prim.sk30 <= lo), ("high", prim.sk30 >= hi)):
        g = prim[m]
        bins[name] = {"rows": int(len(g)), "sessions": int(g.date.nunique()), **_point(g, tr_primary, slope_only=True)}
    bins["cutoffs_pretest_20_80"] = [lo, hi]
    bins["gate_10_sessions_each"] = bins["low"]["sessions"] >= 10 and bins["high"]["sessions"] >= 10
    return rob, bins


def _point(g, tr, slope_only=False):
    if len(g) < 50:
        return {"rows": len(g), "note": "too few rows"}
    if slope_only:
        q = np.clip(g.ofi_norm, tr.ofi_lo, tr.ofi_hi) / tr.ofi_sd
        s = np.maximum(-q, 0); b = np.maximum(q, 0)
        X = np.column_stack([np.ones(len(g)), b, s])
        c = np.linalg.lstsq(X, g.y_5.to_numpy(), rcond=None)[0]
        return {"sell_slope_bps_per_sd": float(c[2]), "buy_slope_bps_per_sd": float(c[1])}
    X, nm = design(g, tr, "M_SKEW")
    f = ols(X, g.y_5.to_numpy(), nm)
    return {"delta_sell": f.get("s_z"), "rows": len(g), "sessions": int(g.date.nunique()), "rank": f.rank}


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def run_all():
    panel, cov = build_panel()
    gate = gates(cov)
    pre, tr = pretest(panel, cov)
    vd = validation_diagnostics(panel, cov, tr)
    lam = json.loads((OUT / "lambda_selection.json").read_text(encoding="utf-8-sig"))["selected_lambda"]
    (OUT / "transforms.json").write_text(tr.to_json())
    if not (OUT / "design_freeze.json").exists():
        freeze(cov, tr, _jsonable(vd), lam, _jsonable(gate))
    status = "main analysis on sampled order flow (deviation D-014)" if gate["all_pass"] else \
        "pilot/feasibility analysis: data-quality gates NOT met"
    res = {"status": status, "gates": gate, "validation": vd}
    res["final"] = run_final(panel, cov, tr)
    fc, met = run_forecasts(panel, cov, tr)
    res["forecast"] = fc
    rob, bins = run_robustness(panel, cov, tr)
    res["skew_bins"] = bins
    (OUT / "results.json").write_text(json.dumps(_jsonable(res), indent=2, default=str))
    print(json.dumps(_jsonable(res), indent=2, default=str)[:6000])


if __name__ == "__main__":
    stage = sys.argv[1]
    if stage == "panel":
        _, cov = build_panel()
        print(json.dumps(gates(cov), indent=2))
    elif stage == "all":
        run_all()


