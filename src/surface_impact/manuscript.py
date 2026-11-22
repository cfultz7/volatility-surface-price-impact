"""Generate the working paper and technical appendix from saved artifacts only (no recomputation).

Outputs: paper/manuscript.md (main text + references), paper/appendix.md, paper/main.tex (verbatim
mirror for LaTeX users), outputs/main/paper.html and outputs/main/paper.pdf (main text + appendix;
xhtml2pdf renderer, the documented fallback because no LaTeX/pandoc is installed).
Every number is read from outputs/main (results, diagnostics, tables) or the pilot audit.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd

OUT = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
PAPER = Path(os.environ.get("VSIMPACT_PAPER", "paper"))
VERSION = "1.0"


def f(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and x != x) else f"{x:.{nd}f}"


def ci(c, nd=3):
    return f"[{f(c[0], nd)}, {f(c[1], nd)}]"


def pct(x, nd=1):
    return f"{100 * x:.{nd}f}%"


def jload(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else default


def load():
    A = {
        "R": jload(OUT / "results.json"), "F": jload(OUT / "design_freeze.json"),
        "D": jload(OUT / "diagnostics.json", {}), "ES": jload(OUT / "expiry_sensitivity.json", {}),
        "lam": jload(OUT / "lambda_selection.json", {}), "sim": jload(OUT / "simulation_size_power.json"),
        "pilot": jload("outputs/pilot/pilot_audit.json", []),
        "cov": pd.read_csv(OUT / "coverage.csv"), "coef": pd.read_csv(OUT / "coefficients.csv"),
        "rob": pd.read_csv(OUT / "robustness.csv"),
        "met": pd.read_csv(OUT / "forecast_metrics.csv", index_col=0),
    }
    for name in ("measurement_stress.csv",):
        A["ms"] = pd.read_csv(OUT / name) if (OUT / name).exists() else None
    for key, name in (("desc", "tables/descriptives.csv"), ("full", "tables/final_M_SKEW_all_coefficients.csv")):
        A[key] = pd.read_csv(OUT / name) if (OUT / name).exists() else None
    # skew variance decomposition (Method A primary states): between-day vs within-day
    st = pd.read_parquet(OUT / "skew_states.parquet", columns=["date", "variant", "ok", "sk30", "atm_iv"])
    st = st[(st.variant == "A_primary") & st.ok]
    within = st.groupby("date").sk30.var().mean()
    daily = st.groupby("date")[["sk30", "atm_iv"]].mean()
    A["skewvar"] = {"total_sd_volpts": float(st.sk30.std() * 100),
                    "within_day_share": float(within / st.sk30.var()),
                    "corr_daily_sk30_atmiv": float(daily.corr().iloc[0, 1])}
    return A


def verdict(p):
    lo, hi = p["ci_basic"]
    if lo > 0 or hi < 0:
        sign = "negative" if p["delta_sell"] < 0 else "positive"
        return (f"The 95% block-bootstrap interval excludes zero and the estimate is {sign}. "
                + ("This matches the sign of the directional hypothesis." if sign == "negative"
                   else "This is the opposite sign to the directional hypothesis."))
    return ("The 95% block-bootstrap interval includes zero, so the primary null of no "
            "sell-pressure × skew interaction is not rejected at the 5% level. The result is inconclusive "
            "about the interaction, not evidence that it is exactly zero.")


def md_table(df: pd.DataFrame, floatfmt=3) -> str:
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: f"{v:.{floatfmt}f}" if v == v else "")
        else:
            d[c] = d[c].map(lambda v: "" if (v is None or (isinstance(v, float) and v != v)) else v)
    cols = list(d.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in d.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


def fig(n, name, caption):
    return f"**Figure {n}.** {caption}\n\n![Figure {n}](../outputs/main/figures/{name}.png)\n"


def desc_table(desc: pd.DataFrame) -> str:
    rows = []
    for var, g in desc.groupby("variable", sort=False):
        r = {"Variable": var}
        for s in ("dev", "val", "final"):
            x = g[g.split == s].iloc[0]
            r[f"{s} mean (sd)"] = f"{x['mean']:.3f} ({x['sd']:.3f})"
        r["final p5 / p50 / p95"] = " / ".join(f"{g[g.split == 'final'].iloc[0][k]:.2f}" for k in ("p5", "median", "p95"))
        rows.append(r)
    return md_table(pd.DataFrame(rows))


def build():
    A = load()
    R, F, D, ES, lam, sim = A["R"], A["F"], A["D"], A["ES"], A["lam"], A["sim"]
    cov, coef, rob, met, ms, desc, full = A["cov"], A["coef"], A["rob"], A["met"], A["ms"], A["desc"], A["full"]
    p, g, fc, fin = R["final"]["primary"], R["gates"], R["forecast"], R["final"]
    from .budget import BudgetConfig, Ledger

    spend = Ledger("data/ledger/cost_ledger.csv", BudgetConfig.load("configs/budget.yaml")).summary()["consumed"]
    q = cov[cov.qualifying]
    n_sched = {k: int((cov.split == k).sum()) for k in ("dev", "val", "final")}
    n_q = {k: int((q.split == k).sum()) for k in ("dev", "val", "final")}
    status, today = R["status"], date.today().isoformat()
    slopes, sc = p["sell_slope_by_z"], fin["scale"]
    has_ci = {"delta_sell", "ci_lo", "ci_hi"} <= set(rob.columns)
    rb = rob.dropna(subset=["delta_sell", "ci_lo"]) if has_ci else rob.iloc[0:0]
    rb = rb[~rb.check.str.startswith(("primary", "PLACEBO"))]  # robustness checks only
    same_sign = int((rb.delta_sell * p["delta_sell"] > 0).sum())
    excl0 = int(((rb.ci_lo > 0) | (rb.ci_hi < 0)).sum())
    max_dev = float((rb.delta_sell - p["delta_sell"]).abs().max()) if len(rb) else float("nan")
    sv = A["skewvar"]
    msd = (ms.A_sd * 100) if ms is not None else pd.Series(dtype=float)
    q4 = D.get("quote_quality", {}).get("usable_share_by_atm_iv_quartile", {})
    sel_dir = ("Selection does not run toward stress: the usable share is higher in high-volatility states."
               if q4.get("Q4 (high IV)", 0) >= q4.get("Q1 (low IV)", 1) else
               "Selection runs toward stress: the usable share is lower in high-volatility states.")
    hor = coef[coef.term.str.startswith(("s_z@", "b_z@"))][["term", "estimate", "ci_lo", "ci_hi", "p_null_centered", "p_holm_secondary", "role"]]
    n_sec_sig = int((hor.p_holm_secondary < 0.05).sum())
    sec_min = hor[hor.role == "secondary"].sort_values("p_null_centered").iloc[0]
    months = rob[rob.check.str.startswith("calendar month")]
    pl = rob[rob.check.str.startswith("PLACEBO")]
    better = [m for m in ("M_PRICE", "M_FLOW", "M_SKEW") if met.loc[m, "MSE"] < met.loc["bench", "MSE"]]
    beat_bench = ("Models with lower final-period MSE than the pretest-mean benchmark: " + ", ".join(better) + "."
                  if better else
                  "No model beats the pretest-mean benchmark out of sample: all three have negative "
                  "out-of-sample R². Five-minute ES midquote returns are close to unpredictable with these "
                  "linear specifications.")
    placebo_txt = ""
    if len(pl) and has_ci:
        placebo_txt = (f"The placebo estimate is {f(pl.delta_sell.iloc[0])} (CI {ci([pl.ci_lo.iloc[0], pl.ci_hi.iloc[0]])}, "
                       f"p = {f(pl.p_null_centered.iloc[0])}).")
        if abs(pl.delta_sell.iloc[0]) >= abs(p["delta_sell"]):
            placebo_txt += (" It is no smaller than the primary estimate, which is consistent with the primary "
                            "estimate reflecting noise rather than same-day state dependence.")
    mde = D.get("mde_80pct_power_two_sided_5pct", {}).get("mde", float("nan"))
    sel, qq, par, ag, cst = D.get("selection", {}), D.get("quote_quality", {}), D.get("parity", {}), D.get("method_agreement", {}), D.get("costs", {})
    aud = next((x.get("mbp1_audit") for x in A["pilot"] if x.get("mbp1_audit")), {}) or {}
    rolls = ", ".join(f"{r['date']} → {r['es_contract']}" for r in D.get("roll_days_2025", []))
    ub = p["ci_basic"][1]
    lbv = p["ci_basic"][0]
    bound_txt = (f"The interval bounds the interaction to between {f(lbv, 2)} and {f(ub, 2)} bps per SD of selling pressure "
                 f"per SD of skew, against a five-minute return SD of {f(sc['sd_y5_bps'], 2)} bps and a median quoted "
                 f"spread of {f(sc['median_spread_bps'], 2)} bps.")
    eo = ES.get("ok_changed")
    expl = ES.get("ok_change_explanation", "")

    abstract = (
        f"We study whether option-implied downside skew conditions the short-horizon response of S&P 500 "
        f"E-mini futures (ES) prices to observed order-flow pressure. Using Databento historical data for all "
        f"{len(cov)} regular sessions of 2025 ({g['total']} qualifying), we build a strictly lagged 30-day "
        f"25-delta skew from consolidated SPXW option quotes every five minutes, with forwards and discount "
        f"factors from put-call parity and a shape-constrained convex fit of normalized call prices. ES order "
        f"flow is a 1-second sampled best-quote imbalance (a budget-driven deviation from event-level data). "
        f"In the locked final period (September-December 2025, {p['sessions']} sessions), the coefficient on "
        f"selling pressure × standardized skew for the five-minute midquote return is {f(p['delta_sell'])} bps "
        f"per standard deviation of flow and skew (95% moving-block bootstrap interval {ci(p['ci_basic'])}; "
        f"null-centered p = {f(p['p_null_centered'])}). {verdict(p).split('. ')[0]}. Frozen pretest forecasts "
        f"show an incremental out-of-sample change 1 − SSE(M_SKEW)/SSE(M_FLOW) = "
        f"{f(fc['incremental_1_minus_SSE_ratio'], 4)} (interval {ci(fc['incremental_ci_basic'], 4)}). "
        f"Estimates are conditional, observational associations; the main limitations are sampled order "
        f"flow, a single year and market pair, and cross-market timing.")
    nwords = len(abstract.split())

    md = f"""---
title: "Volatility Surface Skew and State-Dependent Price Responses in S&P 500 Futures"
author: "Chantry Fultz"
date: "{today}"
version: "Working paper v{VERSION}, protocol v0.2 (status: {status})"
---

# Volatility Surface Skew and State-Dependent Price Responses in S&P 500 Futures

**Chantry Fultz** · Working paper, version {VERSION} (protocol v0.2), generated {today}.
Status: **{status}**. Not peer reviewed.

**Abstract.** {abstract}

## 1. Introduction
Two minutes can show similar selling pressure in the E-mini S&P 500 futures book while downside
protection in the S&P 500 options market is priced very differently. If the price of crash protection
carries information about how fragile liquidity is, or about how other participants will respond to
further selling, the price path *after* the pressure might differ between those two minutes. This
paper asks whether it does. The options state is measured strictly before the pressure occurs, and the
response is measured strictly after it.

The object of study is a **conditional, observational price response**, not a structural price-impact
estimate. We do not identify the effect of an externally imposed trade, informed trading, dealer hedging
or permanent changes in fundamental value. Skew here is a relative-pricing coordinate of the option
surface (the implied-volatility gap between 25-delta puts and calls at a constant 30-day maturity). It is
not a physical crash probability.

The design has four parts.

1. **Skew state.** Every five minutes we build a 30-day surface from consolidated SPXW quotes, with
   forwards and discount factors estimated from the quotes themselves, and read off the 25-delta skew
   under two different surface constructions.
2. **Order flow.** We measure best-quote order-flow imbalance (OFI) in the dated ES contract over a
   one-minute window. Under the project budget it is sampled at one-second resolution.
3. **Response.** We relate the ES midquote return over the following one to fifteen minutes to buying
   and selling pressure, their interaction with the lagged skew state, and controls for volatility,
   spread, depth, recent returns and seasonality.
4. **Locked design.** Every modelling choice was frozen at {F['frozen_utc'][:16].replace('T', ' ')} UTC
   (`design_freeze.json`, source hash `{F['source_sha256'][:12]}`), before outcomes from the final
   September-December 2025 period were read.

**Findings.**

- **Primary estimate.** The sell-pressure × skew coefficient at five minutes is {f(p['delta_sell'])} bps
  (95% CI {ci(p['ci_basic'])}, p = {f(p['p_null_centered'])}).
- **Secondary family.** {n_sec_sig} of {len(hor[hor.role == 'secondary'])} secondary interaction tests
  are significant after Holm adjustment.
- **Robustness.** The estimate does not change materially across the second surface method, quote
  filters, delayed endpoints or roll exclusion.
- **Forecasting.** Adding skew to the flow model does not improve frozen out-of-sample forecasts; the
  change in squared error is {f(fc['incremental_1_minus_SSE_ratio'], 4)}.

We read this as a well-measured null within the precision of one year of data. The minimum detectable
effect at 80% power is about {f(mde, 2)} bps per SD², so small interactions cannot be ruled out.

The contribution is narrow:

- a strictly lagged, constant-maturity skew state built consistently from option premiums;
- a short-horizon response specification that separates the flow interval from future returns;
- controls and interactions that challenge the idea that skew simply stands in for volatility or depth;
- robustness to a second surface method and to quote-quality filters;
- inference that respects day-level dependence;
- a reproducible, budget-audited pipeline from vendor metadata to paper.

Section 2 places the question in the literature. Section 3 describes data, conventions and sample.
Section 4 covers option measurement, and Section 5 covers order flow, timing, models and inference.
Sections 6-8 report the association, forecasts and robustness. Sections 9-11 discuss, list limitations
and conclude. The appendix contains derivations, exact formulations, the variable dictionary, the
bootstrap algorithm, full tables and reproducibility details.

## 2. Related literature and novelty scope
**Order flow and price formation.** Cont, Kukanov and Stoikov (2014) define best-quote order-flow
imbalance and show that it explains most contemporaneous short-interval price changes, with a slope
inversely related to depth. Cont, Cucuringu and Zhang (2023) extend the measure to multiple book levels
and across assets. They find that multi-level OFI improves contemporaneous fit and that lagged
cross-asset OFI has short-lived predictive content. Bouchaud, Gefen, Potters and Wyart (2004) document
that price impact is partly transient, which is why we report the whole one-to-fifteen-minute horizon
profile rather than a single "permanent impact".

**The E-mini and state dependence.** Hasbrouck (2003) finds that most S&P 500 price discovery occurs in
the E-mini contract, which makes ES the natural venue for the response variable. Closest in market and
spirit, Takahashi (2025, preprint) estimates a structural VAR on one-second ES best-quote data and finds
that price-flow dynamics change around macroeconomic announcements. That work conditions on news
events; we condition on a continuously measured option-implied state.

**Option-implied downside risk and liquidity.** Christoffersen, Feunou, Jeon and Ornthanalai (2021)
show at daily frequency that option-implied crash risk tracks stock-market illiquidity more closely than
spot variance. This is the most direct motivation for expecting skew to condition liquidity-sensitive
price responses. Our study moves the question to intraday horizons and uses depth and spread as explicit
competing controls.

**Measurement of the surface.** Ulrich and Walther (2020) show that option-implied moments depend on
how the volatility surface is constructed. We therefore build skew with two different methods.
Gatheral and Jacquier (2014) characterize when SVI surfaces are free of static arbitrage, and Cohen,
Reisinger and Wang (2020) repair traded option prices to remove arbitrage within bid/ask bounds. Our
primary method is a shape-constrained convex fit in that spirit; our robustness method is raw SVI, for
which we make no global no-arbitrage claim.

**Information between options and the underlying.** Option markets contribute to price discovery
(Chakravarty, Gulen and Mayhew 2004), and option-induced order flow predicts underlying returns (Hu 2014).
Option prices are also shaped by market-maker inventory (Muravyev 2016) and respond to informed trading
in the underlying (Collin-Dufresne, Fos and Muravyev 2021). The last point implies that skew can respond
*to* ES order flow. This is why our state is strictly lagged before the flow window.

**Inference.** Overlapping-horizon returns and persistent regressors make naive standard errors badly
optimistic (Hodrick 1992). We use a moving-block bootstrap (Künsch 1989) over whole trading-session
blocks.

**Novelty scope.** Our search (two extended web searches, one peer-reviewed index search and reference
checks; see `docs/novelty_audit.md`) found no study that conditions the forward ES response to OFI on a
strictly lagged, constant-maturity SPXW skew state with two-method measurement robustness. That search
was limited, and its result is not evidence that no such study exists. The literature review is verified
at the bibliographic and abstract level; full-text reading of the closest papers is listed as future work.

## 3. Data, conventions and sample
### 3.1 Sources and budget
All data are Databento historical data:

- OPRA.PILLAR `cbbo-1m` consolidated NBBO snapshots for explicitly selected SPXW contracts;
- GLBX.MDP3 `bbo-1s` for the active dated ES contract;
- SPXW and ES definitions, ES status records and daily ES reference bars.

Every billable request was costed with Databento's metadata API, padded by 20% and reserved against a
project ceiling of $31.09 under a file lock. Total data spend is **${spend:.2f}**. This is on a
metadata-estimate basis, with failed downloads counted as spent because they may have been billed.

| Item | Spend (USD) |
|---|---|
| ES bbo-1s | {cst.get('by_schema_usd', {}).get('bbo-1s', float('nan')):.2f} |
| SPXW cbbo-1m (selected contracts) | {cst.get('by_schema_usd', {}).get('cbbo-1m', float('nan')):.2f} |
| Definitions (SPXW + ES) | {cst.get('by_schema_usd', {}).get('definition', float('nan')):.2f} |
| ES mbp-1 audit day (2024-02-01) | {cst.get('by_schema_usd', {}).get('mbp-1', float('nan')):.2f} |
| Reference bars and status | {cst.get('by_schema_usd', {}).get('ohlcv-1d', 0) + cst.get('by_schema_usd', {}).get('status', 0):.2f} |
| Failed requests counted conservatively (earlier ledger version) | {cst.get('legacy_conservative_releases', float('nan')):.2f} |
| **Total** | **{spend:.2f}** |

The event-level `mbp-1` design in the original protocol would have cost about $210 for 2025 alone, so
the budget forced the order-flow deviation described in Section 3.5.

### 3.2 Contract family and conventions
SPXW options are Cboe's weekly and end-of-month S&P 500 index options, described by Cboe as
European-style, cash-settled and PM-settled, with a multiplier of 100. Cboe's product page states that
"Trading in SPXW options will ordinarily cease on the day of expiration, 4:00 pm ET, and at 1:00 pm ET
for any half day holiday." Standard AM-settled SPX options are excluded by root. Databento definitions
confirm the root, underlying, option type and strikes in index points. They leave the multiplier and
settlement style undefined (sentinel values), so those rest on Cboe's specification. Definition
`expiration` fields are dates encoded at 00:00 UTC, not settlement instants.

The frozen protocol used 16:00 New York on the expiry date as the economic expiry instant. Section 4.6
quantifies the effect of instead using the 13:00 close for half-day expiries. Premiums and strikes stay
in index points throughout; the multiplier never enters implied-volatility inversion.

### 3.3 Contract selection
Contracts were selected before each session from that day's definitions:

- **Expiries:** the nearest PM expiries at or below and at or above 30 days across the session's
  five-minute cutoffs, within 14-90 days;
- **Strikes:** all listed strikes within 0.85-1.15 times the previous session's ES reference close;
- **Types:** calls and puts.

On average {f(sel.get('contracts_mean', float('nan')), 0)} contracts were requested per session (range
{sel.get('contracts_min', 'n/a')}-{sel.get('contracts_max', 'n/a')}). Two expiries were needed on
{sel.get('expiries_per_session', {}).get('2', 'n/a')} sessions and three on
{sel.get('expiries_per_session', {}).get('3', 'n/a')}. The prespecified 0.75-1.25 expansion was never
needed: 25-delta roots stayed inside quoted support.

### 3.4 Session calendar and ES roll
The study session is the cash-index regular session, 09:30-16:00 New York, on the XNYS calendar
(shortened to 13:00 on early closes). Observation endpoints run from 09:40 to 15 minutes before the
close. ES is the dated quarterly contract, rolled at the start of the session on the Thursday eight
calendar days before its third-Friday expiry. The 2025 rolls were: {rolls}. No order flow, return or
rolling state is carried across a roll; outcomes are built inside each session.

### 3.5 Timestamps and the sampled order-flow deviation
**SPXW snapshots.** For `cbbo-1m`, `ts_recv` is the clamped end of each one-minute interval, and we use
it as the conservative availability time. `ts_event` can be a last-trade time, so it is never used as
quote age. A record exists only for intervals with a quote update or trade.

**ES order flow.** `bbo-1s` records the best bid and offer at the end of each one-second interval in which
the book or a trade changed. The original protocol called for event-level `mbp-1`. Because event-level
data would have cost 7-12 times the budget, we construct a 1-second sampled OFI and label it as such
throughout (deviation D-014). On the development audit day (2024-02-01, {aud.get('minutes_compared', 'n/a')}
minutes), sampled and event-level OFI per minute have:

- correlation {f(aud.get('corr_sampled_vs_event'), 2)} (Spearman {f(aud.get('spearman'), 2)});
- sign agreement {pct(aud.get('sign_agreement', float('nan')), 0)};
- a sampled-to-event SD ratio of {f(aud.get('ratio_sd_sampled_to_event'), 2)}, reflecting netting within each second.

SPXW and ES are collected in different locations, so we never rely on cross-dataset nanosecond ordering.
The options state is deliberately lagged instead (Section 5.2).

### 3.6 Sample, splits and data quality
The 12-month design was chosen by the protocol's cost rule before outcomes were examined; the 24-month
design did not fit the ceiling. All 250 scheduled sessions were acquired. Forty initially failed on
connection aborts and an account monthly limit and were recovered *before* the design freeze.

| Split | Months | Scheduled sessions | Qualifying (≥ 75% valid endpoints) |
|---|---|---|---|
| Development | Jan-Jun 2025 | {n_sched['dev']} | {n_q['dev']} |
| Validation | Jul-Aug 2025 | {n_sched['val']} | {n_q['val']} |
| Final | Sep-Dec 2025 | {n_sched['final']} | {n_q['final']} |

Every gate in the protocol passes: {'yes' if g.get('all_pass') else 'no'}. Skew states could be formed
at {pct(qq.get('state_ok_rate', float('nan')), 2)} of five-minute cutoffs. Table 2 summarizes the main
variables on estimation rows (h = 5, primary specification).

{fig(2, 'fig2_coverage', 'Valid joint observations per 2025 session (primary specification, h = 5); x marks ES roll days. Every session qualifies.')}

**Table 2. Descriptive statistics by split (estimation rows).**

{desc_table(desc) if desc is not None else 'Descriptive table not available.'}

## 4. Option measurement
### 4.1 Parity, forwards and normalization
For each expiry and cutoff we fit C(K) − P(K) = A − BK on matched strikes, so that D = B and F = A/B.
The fit is a robust Huber regression weighted by the width of each bid/ask parity interval
[C_bid − P_ask, C_ask − P_bid]. An expiry needs at least four matched pairs and an implied effective
rate inside (−10%, 25%).

- **Fits:** {par.get('expiry_fits', 'n/a')} expiry fits across all cutoffs; {pct(par.get('parity_ok_rate', float('nan')), 2)} succeeded, with a median of {f(par.get('pairs_median'), 0)} pairs.
- **Interval violations:** only {pct(par.get('share_fits_with_any_violation', float('nan')), 2)} of fits violate any bid/ask parity interval.
- **Implied rates:** median {f(par.get('implied_rate_pct_quantiles_5_50_95', [float('nan')] * 3)[1], 2)}%, 5th-95th percentile {f(par.get('implied_rate_pct_quantiles_5_50_95', [float('nan')] * 3)[0], 2)}-{f(par.get('implied_rate_pct_quantiles_5_50_95', [float('nan')] * 3)[2], 2)}%. These are plausible 2025 financing rates, recovered from quotes alone without an external rate curve.

With x = K/F and normalized call c = C/A (puts map via c = p + 1 − x), the measurement is invariant to
any common rescaling of premiums (Appendix A).

### 4.2 Quote filters and selection into quotes
A quote is usable if:

- its latest snapshot interval ended at most 120 s before the cutoff;
- it is two-sided with positive sizes and a positive midpoint;
- its relative spread is at most 0.50.

Per cutoff, an average of {f(qq.get('mean_listed_with_record'), 0)} selected contracts had a record, of
which {f(qq.get('mean_usable'), 0)} were usable. The rest were {f(qq.get('mean_stale'), 1)} stale,
{f(qq.get('mean_not_two_sided'), 1)} not two-sided and {f(qq.get('mean_wide_spread'), 1)} too wide on
average.

**Selection into quotes.** {sel_dir} The usable share is
{pct(qq.get('usable_share_by_atm_iv_quartile', {}).get('Q4 (high IV)', float('nan')))} in the
highest-ATM-IV quartile against {pct(qq.get('usable_share_by_atm_iv_quartile', {}).get('Q1 (low IV)', float('nan')))}
in the lowest (correlation with ATM IV {f(qq.get('corr_usable_share_atm_iv'), 2)}). It is slightly lower
late in the day ({pct(qq.get('usable_share_by_time_of_day', {}).get('14:00-close', float('nan')))} after
14:00 versus {pct(qq.get('usable_share_by_time_of_day', {}).get('09:35-10:59', float('nan')))} before 11:00).

### 4.3 Method A: shape-constrained normalized call prices (primary)
For each expiry, Method A solves a convex quadratic program for normalized call values on the observed
strike knots, constrained to:

- price bounds max(1 − x, 0) ≤ c ≤ 1;
- slopes between adjacent knots in [−1, 0];
- nondecreasing slopes (convexity).

These are exactly the finite-strike static no-arbitrage conditions. The objective averages
spread-normalized squared midpoint errors, adds a soft penalty for leaving each bid/ask interval and a
curvature roughness penalty λ·Σ(Δs)²/Δx (Appendix C).

λ was chosen from the prespecified grid {{0, 1e-4, 1e-2, 1}} by grouped held-out strike-pair price
reconstruction on {lam.get('dev_sessions_used', 'n/a')} development 12:00 snapshots
({lam.get('folds_scored', 'n/a')} folds). A held-out call and its parity-equivalent put leave
together, and parity is re-estimated without them. The selected value is λ = {lam.get('selected_lambda', 'n/a')},
at the edge of the grid, which was not extended. Fitted values stay essentially inside the quoted
intervals (mean band violation {qq.get('band_violation_mean', float('nan')):.1e} in normalized price units).

### 4.4 Method B: independent raw SVI (robustness)
Method B fits raw SVI total variance w(k) = a + b[ρ(k − m) + √((k − m)² + σ²)] per expiry. It uses the
same quotes and parity normalization as Method A, a spread-normalized premium loss, bounded parameters,
deterministic initialization and past-fit warm starts.

Parameter bounds do not imply absence of arbitrage, so every fit is checked on a dense grid inside its
observed support. Only {pct(ag.get('svi_dense_check_pass_rate', float('nan')), 1)} of SVI fits pass all
finite-grid checks. In a diagnostic sample the failures were all slope violations in the far upside
wing, where fitted call prices rise slightly with strike. These lie beyond the 25-delta call strike,
and no convexity failures occurred. Method B is therefore a measurement-robustness check, not an
arbitrage-free surface.

### 4.5 Constant maturity, skew and agreement between methods
Between the bracketing expiries, Method A interpolates normalized call prices at common x and Method B
interpolates total variance at common k. Both are linear in elapsed maturity to exactly 30 days and
stay inside common strike support with no extrapolation.

SK30 = σ(25Δ put) − σ(25Δ call) uses the undiscounted forward-delta convention: call delta Φ(d₁), put
delta Φ(d₁) − 1. Each root must be unique and bracketed inside support. Across
{ag.get('common_states', 'n/a')} cutoffs where both methods succeed:

- **SK30:** correlation {f(ag.get('corr_sk30_A_B'), 4)}, mean absolute difference {f(ag.get('mean_abs_diff_volpts'), 3)} vol points (95th percentile {f(ag.get('p95_abs_diff_volpts'), 3)});
- **ATM IV:** correlation {f(ag.get('corr_atm_iv_A_B'), 5)}.

Figure 3 shows a calendar-chosen smile and Figure 6 the 2025 skew path.

{fig(3, 'fig3_smile', 'Representative SPXW smile, earliest qualifying development session (calendar rule), 12:00 NY. Illustration only; it does not validate any statistical result.')}

{fig(6, 'fig6_skew_timeseries', 'Daily means of five-minute 30-day skew states (Methods A and B) and 30-day ATM IV in 2025; dotted lines mark the start of the validation and final periods.')}

### 4.6 Measurement uncertainty and the half-day expiry instant
**Quote perturbation.** At calendar-chosen 12:00 snapshots (the first session of each final month plus
the two pilot days), we drew 25 premium sets uniformly inside every quote's bid/ask interval and refit
parity and both surfaces. Method A's SK30 moved with a standard deviation of {f(msd.min(), 2)}-{f(msd.max(), 2)}
vol points across snapshots, against a pretest skew SD of {f(sc['skew_sd_pretest_volpts'], 2)} vol points.
There were {int(ms.A_fail.sum() + ms.B_fail.sum()) if ms is not None else 'n/a'} failed refits (Table 6).
These are measurement stress ranges, not confidence intervals.

**Half-day expiry instant.** A post-freeze review found that SPXW expiring on a half-day holiday stop
trading at 13:00, not 16:00. Recomputing Method A states with the corrected instant for the
{ES.get('sessions_checked', 'n/a')} sessions whose brackets can include such an expiry changes SK30 by
{f(ES.get('mean_abs_dSK30_volpts'), 3)} vol points on average (maximum {f(ES.get('max_abs_dSK30_volpts'), 3)})
at the {ES.get('cutoffs_with_early_close_expiry', 'n/a')} affected cutoffs. It also changes whether a
state can be formed at {eo if eo is not None else 'n/a'} cutoffs. {expl} We report the frozen results
and document the correction (decision D-024).

{"**Table 6. Quote-perturbation measurement stress (SK30, decimal IV).**" + chr(10) + chr(10) + md_table(ms, 4) if ms is not None else ""}

## 5. Order flow, timing, models and inference
### 5.1 Sampled order-flow imbalance and controls
Between consecutive valid ES `bbo-1s` records n−1 and n, the Cont-Kukanov-Stoikov increment is

e = 1{{P_b ≥ P_b⁻}}·Q_b − 1{{P_b ≤ P_b⁻}}·Q_b⁻ − 1{{P_a ≤ P_a⁻}}·Q_a + 1{{P_a ≥ P_a⁻}}·Q_a⁻,

where ⁻ marks the previous record. At unchanged prices both indicators apply and the term reduces to the
size change. Increments are summed over the flow window (t − 60 s, t] and divided by time-weighted mean
top depth (Q_b + Q_a)/2 in contracts.

A flow window is invalid if:

- any record gap exceeds 5 s (10 s as a sensitivity);
- a state is flagged bad or crossed.

Segments reset at session start and after invalid states, so restoration jumps never count as flow.

Controls are:

- 30-day ATM IV;
- a trailing 30-minute RMS one-minute return (excluding the flow minute);
- time-weighted relative spread and log depth;
- the flow-minute return (a control, never an outcome);
- prior 5- and 60-minute returns;
- lagged OFI;
- first and second Fourier harmonics of the session minute;
- weekday dummies, an early-close flag, ES days to expiry and a roll-day flag.

The data dictionary is in Appendix G.

### 5.2 Locked information timeline
Observations are minute endpoints t. The skew state used at t is the latest valid state that satisfies:

- cutoff s ≤ t − 120 s;
- available by t − 60 s;
- t − s ≤ 600 s.

States are computed every five minutes from snapshot intervals ending at or before s, and are available
at s + max(5 s, measured fit time). Outcomes are ES midquote log returns over (t, t + h] for
h in {{1, 2, 5, 10, 15}} minutes. The quote at each endpoint must be at most 5 s old. A delayed variant
runs from t + 5 s to t + 5 s + h.

Example: for t = 10:00 New York, the flow window is (09:59, 10:00]. The state comes from the 09:55
cutoff (available 09:55:05), the 10:00 state is rejected as future information, and the primary outcome
ends at 10:05 (Figure 1). This example is a unit test.

{fig(1, 'fig1_timeline', 'Schematic of the locked information timeline (not to scale).')}

### 5.3 Models and transforms
Let q be the pretest-winsorized (0.5th/99.5th percentile) normalized OFI divided by its pretest SD,
without demeaning so that zero flow stays zero. Then b = max(q, 0), s = max(−q, 0), and z is SK30
standardized with pretest mean and SD. All controls are standardized with pretest moments. Three nested
models are fit on identical rows:

- **M_PRICE:** y = α + Xθ;
- **M_FLOW:** M_PRICE plus b, s, b·RV, s·RV, b·log depth and s·log depth;
- **M_SKEW:** M_FLOW plus z, b·z and s·z.

The primary coefficient is **δ_sell**, on s·z, at h = 5 minutes with Method A. A negative δ_sell would
mean that higher skew goes with a more negative subsequent response to selling pressure.

### 5.4 Inference and precision
The final calendar grid lists all 85 scheduled final sessions. Each bootstrap replication draws
moving blocks of five consecutive session positions. It keeps every observation of each drawn session,
relabels repeated copies and refits the regression, with 1,999 replications and seed 1729. Block
boundaries create no new returns. The two-sided test is null-centered,
p = (1 + #{{|θ* − θ_hat| ≥ |θ_hat|}})/(B + 1), and the primary interval is the basic bootstrap interval
(Appendix H).

On a dependent synthetic panel (persistent skew, autocorrelated flow and errors, skew-dependent error
variance), the test rejected a true null in {pct(sim['null']['reject_rate_5pct']) if sim else 'n/a'} of
{sim['null']['replications'] if sim else 'n/a'} replications (Monte Carlo SE {pct(sim['null']['mc_se']) if sim else 'n/a'}).
It rejected δ = −0.25 in {pct(sim['alt_-0.25']['reject_rate_5pct']) if sim else 'n/a'} of replications
(synthetic). With the empirical bootstrap SE of {f(p['se_boot'])}, the approximate minimum detectable
effect for a two-sided 5% test at 80% power is 2.80 × SE ≈ **{f(mde, 2)} bps per SD of flow per SD of
skew**.

## 6. Results: final-period association
**Table 3. Primary estimate (final period, h = 5 minutes, Method A, M_SKEW).**

| Quantity | Value |
|---|---|
| δ_sell (bps per SD flow per SD skew) | **{f(p['delta_sell'])}** |
| 95% basic block-bootstrap CI | {ci(p['ci_basic'])} |
| 95% percentile CI | {ci(p['ci_percentile'])} |
| Two-sided null-centered p | {f(p['p_null_centered'])} |
| Bootstrap SE (5-session blocks) | {f(p['se_boot'])} |
| Day-clustered SE (diagnostic) | {f(fin['day_clustered']['se'])} |
| 1-session / 10-session block p | {f(fin['block_1'].get('p_null_centered'))} / {f(fin['block_10'].get('p_null_centered'))} |
| Observations / sessions / ISO weeks | {p['rows']} / {p['sessions']} / {p['weeks']} |
| Successful replications | {p['n_success']} of 1,999 |

{verdict(p)} {bound_txt}

**Marginal selling-pressure slopes.** The slope β_sell + δ_sell·z at z = −1, 0, +1 is:

| z (pretest SD of skew) | Slope (bps per SD of selling pressure) | 95% CI |
|---|---|---|
| −1 | {f(slopes['-1']['estimate'])} | {ci(slopes['-1']['ci_basic'])} |
| 0 | {f(slopes['0']['estimate'])} | {ci(slopes['0']['ci_basic'])} |
| +1 | {f(slopes['1']['estimate'])} | {ci(slopes['1']['ci_basic'])} |

The high-minus-low difference 2δ_sell is {f(p['high_minus_low_slope']['estimate'])}, with CI
{ci(p['high_minus_low_slope']['ci_basic'])}.

Selling pressure has essentially no forward five-minute effect at average skew. By contrast, the same
measure is strongly associated with the contemporaneous flow-minute return (correlation
{f(fin['contemporaneous_corr_ofi_rflow_final'], 2)} in the final period), as Cont, Kukanov and Stoikov
(2014) would lead one to expect. The flow measure works; it just has no detectable *forward*
interaction with skew. In-sample R² on the final period is {f(fin['final_in_sample_r2']['M_PRICE'], 4)}
(M_PRICE), {f(fin['final_in_sample_r2']['M_FLOW'], 4)} (M_FLOW) and {f(fin['final_in_sample_r2']['M_SKEW'], 4)}
(M_SKEW).

**Horizon profile.** Figure 4 and Table 4 report the buy and sell interactions at every horizon.
{n_sec_sig} of {len(hor[hor.role == 'secondary'])} secondary tests are significant after Holm adjustment.
The smallest unadjusted p-value is {f(sec_min.p_null_centered)} ({sec_min.term}).

{fig(4, 'fig4_horizon', 'Final-period interaction coefficients by horizon with 95% moving-block bootstrap intervals; 5 minutes is primary.')}

**Table 4. Horizon profile (secondary family, Holm-adjusted).**

{md_table(hor)}

## 7. Frozen pretest forecasts
Coefficients are estimated once on development plus validation data ({fc['pretest_rows']} rows) and
held fixed on the {fc['rows']} final-period rows. The benchmark is the pretest mean return.

**Table 5. Final-period forecast metrics (h = 5 minutes).**

{md_table(met.reset_index().rename(columns={'index': 'model'}), 4)}

{beat_bench}

The incremental change from adding skew is 1 − SSE(M_SKEW)/SSE(M_FLOW) = **{f(fc['incremental_1_minus_SSE_ratio'], 4)}**
(paired five-session block bootstrap interval {ci(fc['incremental_ci_basic'], 4)}; date-balanced
{f(fc['incremental_date_balanced'], 4)}). The squared-error loss difference per observation,
M_FLOW minus M_SKEW, is {f(fc['loss_diff_per_obs'], 3)} with CI {ci(fc['loss_diff_ci_basic'], 3)}.
Adding the skew terms makes out-of-sample forecasts slightly worse, so the skew state has no
incremental forecasting value here. The validation period showed the same sign
({f(R['validation']['val_incremental_skew_vs_flow'], 4)}).

## 8. Robustness
Of {len(rb)} prespecified checks with intervals, {same_sign} share the sign of the primary estimate and
{excl0} exclude zero (Table 7, Figure 5). The checks cover:

- the SVI method, with both common-observation and separate-coverage estimates;
- quote filters at 60 or 180 s record age and 0.25 or 1.00 relative spread;
- delayed endpoints and a 10 s flow-gap rule;
- exclusion of the two final-period roll days.

All differ from the primary by at most {f(max_dev, 3)} bps, with similar interval widths. Calendar-month estimates (no intervals; small samples) range from
{f(months.delta_sell.min(), 2)} to {f(months.delta_sell.max(), 2)} and change sign. This is what
sampling noise looks like, not a stable regime effect. Robustness results are not treated as new
chances to find a headline.

{placebo_txt} The placebo substitutes the state from five eligible sessions earlier at the same session
minute. This breaks same-day alignment between skew and flow while preserving the slow-moving skew level
and intraday seasonality.

The pretest-defined skew groups (20th and 80th percentiles) contain {R['skew_bins']['low']['sessions']}
(low) and {R['skew_bins']['high']['sessions']} (high) final sessions. Within them, descriptive
selling-pressure slopes are {f(R['skew_bins']['low'].get('sell_slope_bps_per_sd'))} and
{f(R['skew_bins']['high'].get('sell_slope_bps_per_sd'))} bps per SD. These are descriptive only; the
continuous interaction remains primary.

{fig(5, 'fig5_robustness', 'Robustness of δ_sell (h = 5) across surface method, quote filters, endpoints, gap rule, roll exclusion, calendar months (point estimates only) and placebo.')}

**Table 7. Prespecified robustness checks for δ_sell (h = 5).**

{md_table(rob.drop(columns=[c for c in ('note', 'rank') if c in rob.columns]), 3)}

## 9. Discussion
**What the null rules out.** The interval excludes interactions larger than about {f(max(abs(lbv), abs(ub)), 2)}
bps per SD² in either direction. A one-SD selling-pressure minute in a high-skew state (z = +1) is
followed by a five-minute return that differs from a low-skew state (z = −1) by
{f(p['high_minus_low_slope']['estimate'], 2)} bps (CI {ci(p['high_minus_low_slope']['ci_basic'], 2)}).
That is small next to the {f(sc['sd_y5_bps'], 2)} bps standard deviation of five-minute returns. An
interaction large enough that a one-SD rise in skew shifts the five-minute response to a one-SD selling
minute by more than about {f(max(abs(lbv), abs(ub)), 2)} bps is inconsistent with these data. Effects
below roughly {f(mde, 2)} bps per SD² cannot be resolved.

**Why the null is plausible.**

- **Fast absorption.** At five minutes the ES market is very liquid. Most price adjustment to flow is
  contemporaneous (correlation {f(fin['contemporaneous_corr_ofi_rflow_final'], 2)}), leaving little
  forward drift for any state to modulate.
- **Skew is slow-moving.** Only {pct(sv['within_day_share'])} of the variance of five-minute SK30 states
  is within-day; the rest is between days (Figure 6). Daily mean SK30 and ATM IV correlate at
  {f(sv['corr_daily_sk30_atmiv'], 2)}. Much of skew's information is about regimes already reflected in
  ATM IV, realized volatility and depth, all of which are controlled.
- **Sampled flow.** Second-level sampling attenuates flow variation (SD ratio
  {f(aud.get('ratio_sd_sampled_to_event'), 2)} on the audit day) and adds measurement error, which biases
  interaction estimates toward zero and could hide sub-second effects. With a per-minute correlation of
  {f(aud.get('corr_sampled_vs_event'), 2)} between sampled and event-level OFI, the attenuation is likely
  modest. It cannot plausibly hide an effect several times larger than the interval bounds, but it does
  weaken the claim for small effects.
- **Earlier evidence is daily.** The daily-frequency link between option-implied crash risk and
  illiquidity (Christoffersen et al. 2021) need not imply an intraday conditional-response effect.

**Measurement.** The result does not hinge on surface construction (A versus B correlation
{f(ag.get('corr_sk30_A_B'), 3)}; nearly identical estimates) or on quote filters. Quote-perturbation
standard deviations (at most {f(msd.max(), 2)} vol points) are small next to the pretest skew SD of
{f(sc['skew_sd_pretest_volpts'], 2)} vol points. {sel_dir}

## 10. Limitations
- Order flow is a 1-second sampled best-quote imbalance, not event-level OFI (D-014).
- One market pair and one year; the final period is four months (85 sessions).
- Skew is partly a volatility-regime variable; ATM IV and realized-volatility controls address this only in part.
- SPXW and ES timestamps come from different collection locations; the deliberate lag guards against look-ahead, not every cross-market timing artifact.
- Over half of SVI fits violate a finite-grid slope condition in the far upside wing; Method B is a robustness check, not an arbitrage-free surface.
- The frozen expiry instant ignores half-day early expirations (measured effect is small; Section 4.6).
- The last observation of each session is dropped at h = 15 by an over-conservative endpoint mask (1 of 366 rows per session; secondary horizon only; D-024).
- Midquote returns are not tradable P&L, and no causal mechanism is identified.
- The literature review is verified at the bibliographic and abstract level only.
- The pipeline was tested on Windows only.

## 11. Conclusion
{verdict(p)} The interaction is small relative to return dispersion and is stable across surface
methods, quote filters and endpoint definitions. It adds no out-of-sample forecasting value. Within the
stated precision, the price of S&P 500 downside protection does not change how E-mini prices continue
or reverse after observed selling pressure over the next one to fifteen minutes. The locked, budgeted and
fully logged pipeline makes it straightforward to revisit the question with event-level order flow,
a longer sample or another market pair.

**AI-assistance statement.** Software, analysis code and drafts were produced with assistance from an AI
coding assistant under the author's direction. All numbers are generated from saved run artifacts.

## References

- Bouchaud, Gefen, Potters & Wyart (2004) *Quantitative Finance* 4(2):176-190, doi:10.1088/1469-7688/4/2/007.
- Chakravarty, Gulen & Mayhew (2004) *Journal of Finance* 59(3):1235-1257, doi:10.1111/j.1540-6261.2004.00661.x.
- Christoffersen, Feunou, Jeon & Ornthanalai (2021) *Review of Finance* 25(4):1261-1298, doi:10.1093/rof/rfaa040.
- Cohen, Reisinger & Wang (2020) *Applied Mathematical Finance* 27(5):345-373, doi:10.1080/1350486X.2020.1846573.
- Collin-Dufresne, Fos & Muravyev (2021) *JFQA* 56(6):1945-1984, doi:10.1017/S0022109020000629.
- Cont, Cucuringu & Zhang (2023) *Quantitative Finance* 23(10):1373-1393, doi:10.1080/14697688.2023.2236159.
- Cont, Kukanov & Stoikov (2014) *Journal of Financial Econometrics* 12(1):47-88, doi:10.1093/jjfinec/nbt003.
- Gatheral & Jacquier (2014) *Quantitative Finance* 14(1):59-71, doi:10.1080/14697688.2013.819986.
- Hasbrouck (2003) *Journal of Finance* 58(6):2375-2400, doi:10.1046/j.1540-6261.2003.00609.x.
- Hodrick (1992) *Review of Financial Studies* 5(3):357-386, doi:10.1093/rfs/5.3.357.
- Hu (2014) *Journal of Financial Economics* 111(3):625-645, doi:10.1016/j.jfineco.2013.12.004.
- Künsch (1989) *Annals of Statistics* 17(3):1217-1241, doi:10.1214/aos/1176347265.
- Muravyev (2016) *Journal of Finance* 71(2):673-708, doi:10.1111/jofi.12380.
- Takahashi (2025) Returns and Order Flow Imbalances: Intraday Dynamics and Macroeconomic News Effects, arXiv:2508.06788 (preprint).
- Ulrich & Walther (2020) *Review of Derivatives Research* 23(3):323-355, doi:10.1007/s11147-020-09166-0.
"""
    PAPER.mkdir(exist_ok=True)
    (PAPER / "manuscript.md").write_text(md, encoding="utf-8")
    app = appendix(A, spend)
    (PAPER / "appendix.md").write_text(app, encoding="utf-8")
    return md + "\n\n" + app, nwords


def appendix(A, spend) -> str:
    R, F, D, lam, sim, full = A["R"], A["F"], A["D"], A["lam"], A["sim"], A["full"]
    dd = Path("docs/data_dictionary.md").read_text(encoding="utf-8")
    dd_table = dd[dd.index("| Column"):dd.index("\nTransforms")].strip() if "| Column" in dd else ""
    lam_tab = pd.DataFrame({"lambda": list(lam.get("mean_loss", {}).keys()),
                            "mean held-out loss": list(lam.get("mean_loss", {}).values())})
    full_tab = md_table(full, 4) if full is not None else "Not available."
    cst = D.get("costs", {})
    lb = jload(OUT / "synthetic_lambda_bias.json", {})
    sel_l = str(lam.get("selected_lambda"))
    lam_bias_txt = ""
    if lb and sel_l in lb.get("by_lambda", {}):
        e = lb["by_lambda"][sel_l]
        e0 = lb["by_lambda"].get("0.0", {})
        lam_bias_txt = (f"**Smoothing bias (SYNTHETIC).** On a smooth, noise-free asymmetric smile (true SK30 "
                        f"{f(lb['true_sk30_volpts'], 3)} vol points), the selected λ = {sel_l} understates SK30 by "
                        f"{f(-e['sk30_err_volpts'], 3)} vol points ({pct(-e['sk30_rel_err'])}) and overstates ATM IV by "
                        f"{f(e['atm_err_volpts'], 3)} vol points. λ = 0 errs by {f(e0.get('sk30_err_volpts'), 4)}. "
                        f"The cross-validation preferred λ = {sel_l} on real, noisy quotes. A near-constant proportional "
                        f"compression of skew is removed by standardizing z, so it should not affect the sign or "
                        f"test of δ_sell. It does mean SK30 levels are slightly conservative.")
    return f"""# Technical appendix

## A. Cash-settled European parity and normalization
For a European, cash-settled call and put with strike K and the same expiry T, the payoff difference
is (S_T − K)⁺ − (K − S_T)⁺ = S_T − K. Under any pricing measure with discount factor D = P(0, T) for
cash paid at T, and forward F = E[S_T] for the index level at T,

C(K) − P(K) = D·(F − K) = A − B·K,  with A = D·F and B = D, so F = A/B.

Neither D nor F is assumed: both come from the cross-section of matched strikes at each cutoff.
Dividends, financing and settlement conventions are absorbed into F and D for that expiry, so the ES
price, which has a different maturity, is never used as the forward.

**Normalization.** Define x = K/F, c = C/A and p = P/A. Then c − p = 1 − x. Multiplying every premium of
an expiry by a common positive constant λ (for example, converting index points to dollars with the
multiplier) multiplies A and B by λ, leaves F = A/B unchanged and leaves c, p, x unchanged. Implied
volatilities and skew are therefore invariant to the unit of premium; unit tests verify this. Mixing
units (dollar premiums with index-point strikes) is rejected because the implied rate leaves the
guardrail.

## B. Black normalized pricing, implied variance and delta
With total variance w = σ²τ, c(x, w) = Φ(d₁) − xΦ(d₂), d₁ = (−ln x + w/2)/√w, d₂ = d₁ − √w. c is strictly
increasing in w on (max(1 − x, 0), 1). The implied w is found by bracketed root search (Brent; a
vectorized bisection on ln w for grids). Prices outside the open bounds are rejected, never clamped.
τ is ACT/365 elapsed time from the cutoff to the expiry instant. The quoting delta is the undiscounted
forward delta Φ(d₁) (call) and Φ(d₁) − 1 (put). The 25-delta put is where the call delta equals 0.75.

## C. Method A quadratic program
At distinct observed strike knots x₁ < … < x_m with fitted values c_j and slopes
s_j = (c_{{j+1}} − c_j)/(x_{{j+1}} − x_j):

minimize  (1/n)Σ_i ((c(x_i) − c_mid,i)/h_i)² + 100·(1/n)Σ_i ((c(x_i) − c_hi,i)⁺ + (c_lo,i − c(x_i))⁺)²/h_i² + λ·Σ_j (s_{{j+1}} − s_j)²/Δx̄_j

subject to max(1 − x_j, 0) ≤ c_j ≤ 1, −1 ≤ s_j ≤ 0, s_{{j+1}} ≥ s_j,

where i runs over all call and put observations (puts converted by parity), h_i = max(half-spread_i,
2×10⁻⁵) and Δx̄_j = (x_{{j+2}} − x_j)/2. Between knots the fitted call is linear, so the constraints hold on the
whole observed support. The problem is solved with CLARABEL via CVXPY. The 30-day curve is
c₃₀(x) = a·c₁(x) + (1 − a)·c₂(x) with a = (τ₂ − τ₃₀)/(τ₂ − τ₁). This is a convex combination, which
preserves the strike constraints on common support. λ selection uses five strike folds per expiry
(strike rank mod 5); held-out quotes inside the training support are scored. Results:

{md_table(lam_tab, 4)}

{lam_bias_txt}

## D. Method B raw SVI
Parameters (a, b, ρ, m, σ) are bounded to a in [−0.5, 1], b in [10⁻⁴, 5], ρ in [−0.999, 0.999],
m in [−1, 1] and σ in [10⁻⁴, 2]. Initialization is deterministic: a 12-point grid least-squares fit to
implied total variance of mid quotes, followed by premium-space refinement with the spread-normalized
loss (warm-started from the same expiry's previous fit within the session). The checks are: positive w,
call bounds, slopes in [−1, 0] and convexity on a 401-point grid inside support. 30-day interpolation is
linear in total variance at fixed k. Failing checks are flagged and reported (Section 4.4).

## E. Order-flow construction details
ES `bbo-1s` records give the BBO state at the end of each printed one-second interval. Records are valid
if both sides are finite, ask > bid, sizes are positive and no bad-timestamp or bad-book flag is set.
The increment of Section 5.1 is computed between consecutive valid records within a segment. Depth is the
time-weighted mean of (Q_b + Q_a)/2 over the 60 one-second states in the window. Hand-computed unit
tests cover same-price size changes, moving prices, duplicated states, multi-record events (collapsing on
F_LAST for event-level data), invalid states and resets.

## F. Timing example (10:00 New York)
- t = 10:00:00. Flow window (09:59:00, 10:00:00].
- Admissible state: cutoff ≤ 09:58:00, available ≤ 09:59:00, cutoff ≥ 09:50:00. The 09:55 state (available
  09:55:05) is used; the 10:00 state is future information and rejected.
- Outcomes: 10:01, 10:02, **10:05 (primary)**, 10:10, 10:15; delayed variant 10:00:05 → 10:05:05.
- `tests/test_alignment.py` verifies this example, including a slow-fit case where availability moves
  past t − 60 s and the previous state is used.

## G. Variable dictionary
{dd_table}

## H. Bootstrap algorithm and tests
1. List the G = {len(F['final_grid'])} scheduled final sessions in calendar order, including any without
   valid rows.
2. For b = 1, …, 1999 (seed 1729): draw ceil(G/5) block starts uniformly from the G − 4 possible starts, and
   concatenate the five consecutive session positions of each block, truncated to G.
3. Collect every original observation of each drawn session, relabelling repeated copies. Returns are
   never recomputed across block boundaries.
4. Refit the frozen M_SKEW regression to get θ*_b.
5. Report θ_hat, the basic interval [2θ_hat − q(0.975), 2θ_hat − q(0.025)] of the draws, the percentile
   interval, and the null-centered p = (1 + #{{|θ*_b − θ_hat| ≥ |θ_hat|}})/(B + 1).

The secondary family (b·z and s·z at 1, 2, 5, 10, 15 minutes, excluding the primary) is Holm-adjusted.
Forecast comparisons resample paired daily blocks of losses. Synthetic checks (labelled SYNTHETIC):
null rejection {pct(sim['null']['reject_rate_5pct']) if sim else 'n/a'} (Monte Carlo SE
{pct(sim['null']['mc_se']) if sim else 'n/a'}, {sim['null']['replications'] if sim else 'n/a'} replications);
power {pct(sim['alt_-0.25']['reject_rate_5pct']) if sim else 'n/a'} at δ = −0.25.

## I. Full final-period M_SKEW coefficients (h = 5, Method A)
All regressors are standardized with pretest moments. Intervals are basic 95% moving-block bootstrap
intervals (5-session blocks, 1,999 replications). Rank and conditioning: rank
{R['final']['day_clustered']['rank']}, condition number {f(R['final']['day_clustered']['cond'], 1)}.

{full_tab}

## J. Additional diagnostics
- **Parity:** {D.get('parity', {}).get('expiry_fits', 'n/a')} expiry fits, success rate {pct(D.get('parity', {}).get('parity_ok_rate', float('nan')), 2)}, median {f(D.get('parity', {}).get('pairs_median'), 0)} pairs, mean interval violations {f(D.get('parity', {}).get('interval_violations_mean'), 4)} per fit. Failures were all too few matched pairs ({sum(D.get('parity', {}).get('parity_fail_reasons', {}).values())} fits).
- **Quote quality:** {f(D.get('quote_quality', {}).get('mean_usable'), 1)} of {f(D.get('quote_quality', {}).get('mean_listed_with_record'), 1)} contracts usable per cutoff. Of {D.get('quote_quality', {}).get('cutoffs', 'n/a')} primary-state cutoffs, {sum(D.get('quote_quality', {}).get('failure_reasons', {}).values())} failed: {'; '.join(f'{k} ({v})' for k, v in D.get('quote_quality', {}).get('failure_reasons', {}).items())}.
- **Method agreement:** SK30 correlation {f(D.get('method_agreement', {}).get('corr_sk30_A_B'), 4)}; mean B − A difference {f(D.get('method_agreement', {}).get('mean_diff_B_minus_A_volpts'), 3)} vol points; SVI dense-grid pass rate {pct(D.get('method_agreement', {}).get('svi_dense_check_pass_rate', float('nan')))}.
- **Skew variance:** total SD of five-minute SK30 states {f(A['skewvar']['total_sd_volpts'], 2)} vol points; within-day share {pct(A['skewvar']['within_day_share'])}.
- **Half-day expiry sensitivity (D-024):** {A['ES'].get('sessions_checked', 'n/a')} sessions, {A['ES'].get('cutoffs_with_early_close_expiry', 'n/a')} affected cutoffs. Mean |ΔSK30| {f(A['ES'].get('mean_abs_dSK30_volpts'), 4)} vol points, max {f(A['ES'].get('max_abs_dSK30_volpts'), 3)}; max |ΔATM IV| {f(A['ES'].get('max_abs_dATMIV_volpts'), 3)} vol points. States lost under the correction: {A['ES'].get('ok_changed', 'n/a')}, on {', '.join(A['ES'].get('ok_change_sessions', []))}.

## K. Data acquisition, costs and reproducibility
- **Costs:** total ${spend:.2f} (metadata-estimate basis). By schema: {json.dumps(cst.get('by_schema_usd', {}))}. Failed downloads counted conservatively: ${cst.get('failed_download_conservative', float('nan')):.2f}, plus ${cst.get('legacy_conservative_releases', float('nan')):.2f} from earlier releases.
- **Design freeze:** {F['frozen_utc']} UTC; source SHA-256 {F['source_sha256']}; ledger SHA-256 {F['acquisition_ledger_sha256']}.
- **Environment:** Python 3.12 (uv), databento 0.90.0, cvxpy 1.9.3 (CLARABEL), exchange_calendars 4.13.3, Windows 11 (the only tested platform).
- **Commands (in order):** `surface_impact.planning`, `acquire`, `select_lambda`, `run_measure`, `analyze all`, `stress`, `diagnostics`, `expiry_sensitivity`, `figures`, `manuscript`, `writeups` (see README). All billable steps go through the cached, lock-protected ledger; reruns never pay twice.
- **Data rights:** raw OPRA/CME data are licensed and not distributed. Derived aggregates and figures only.
- **Decisions:** all deviations and choices are logged in `docs/decisions.md`. The key ones are D-014 (sampled flow), D-020 (λ), D-021 (estimation sample), D-022 (batch definitions), D-023 (freeze) and D-024 (post-freeze review findings).
"""


def render(md: str):
    import markdown
    from xhtml2pdf import pisa

    body = re.sub(r"^---.*?---\n", "", md, flags=re.S)
    html_body = markdown.markdown(body, extensions=["tables"])
    root = Path.cwd().as_uri()
    html_body = html_body.replace('src="../outputs/', f'src="{root}/outputs/')
    import shutil

    src = next((p for p in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                            "/Library/Fonts/Arial Unicode.ttf",
                            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(p).exists()), None)
    font = None
    if src:  # local copy inside the project (gitignored data/); the renderer may only read here
        font = Path("data/fonts") / Path(src).name
        font.parent.mkdir(parents=True, exist_ok=True)
        if not font.exists():
            shutil.copy(src, font)
        font = font.resolve()
    face = (f"@font-face{{font-family:UniFont;src:url('{Path(font).as_uri()}')}}" if font else "")
    css = (face + "body{font-family:UniFont,Georgia,serif;font-size:10pt;line-height:1.4;max-width:46em;margin:auto}"
           "table{border-collapse:collapse;font-size:7.5pt}td,th{border:1px solid #bbb;padding:2px 4px}"
           "img{width:15cm}h1{font-size:16pt}h2{font-size:13pt}h3{font-size:11pt}")
    html = f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{html_body}</body></html>"
    (OUT / "paper.html").write_text(html, encoding="utf-8")

    def link_callback(uri, rel):  # map file:// URIs to local paths so figures embed
        if uri.startswith("file:///"):
            from urllib.parse import unquote
            return unquote(uri[len("file:///"):])
        return uri

    with open(OUT / "paper.pdf", "wb") as fh:
        # numeric character references avoid any encoding guess inside the renderer
        pisa.CreatePDF(html.encode("ascii", "xmlcharrefreplace").decode("ascii"), dest=fh,
                       link_callback=link_callback)


def latex(md: str):
    body = re.sub(r"^---.*?---\n", "", md, flags=re.S)
    tex = ("\\documentclass[11pt]{article}\n\\usepackage[margin=1in]{geometry}\n\\usepackage{graphicx}\n"
           "\\usepackage[utf8]{inputenc}\n\\title{Volatility Surface Skew and State-Dependent Price Responses in S\\&P 500 Futures}\n"
           "\\author{Chantry Fultz}\n\\begin{document}\n\\maketitle\n"
           "% Generated from manuscript.md + appendix.md; compile where LaTeX is available. Content mirrors the Markdown.\n"
           "\\begin{verbatim}\n" + body.replace("\\end{verbatim}", "") + "\n\\end{verbatim}\n\\bibliographystyle{plain}\n"
           "\\nocite{*}\\bibliography{references}\n\\end{document}\n")
    (PAPER / "main.tex").write_text(tex, encoding="utf-8")


if __name__ == "__main__":
    md, n = build()
    latex(md)
    render(md)
    print("abstract words:", n)
