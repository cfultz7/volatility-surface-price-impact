"""Research dashboard + interactive method demo.

Reads saved artifacts only (never downloads or recomputes results). The "Method demo" page runs
the project's own measurement code on SYNTHETIC quotes, so it works without any licensed data.

Local:  uv run --extra dashboard streamlit run app/dashboard.py
Cloud:  Streamlit Community Cloud, main file `app/dashboard.py` (dependencies: app/requirements.txt)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # use the package without installing it (e.g. on Streamlit Cloud)

args = sys.argv[1:]
RUN = Path(args[args.index("--run") + 1]) if "--run" in args else ROOT / "outputs" / "main"
if not RUN.is_absolute():
    RUN = ROOT / RUN


def load_json(name):
    p = RUN / name
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else None


def load_csv(name, **kw):
    p = RUN / name
    return pd.read_csv(p, **kw) if p.exists() else None


def missing(name):
    st.info(f"`{name}` is not available in this deployment. Nothing is shown in its place.")


def image(name):
    p = RUN / "figures" / name
    if p.exists():
        st.image(str(p), use_container_width=True)
    else:
        missing(name)


st.set_page_config(page_title="Skew & Price Response", page_icon="📈", layout="wide")
res, frz, diag = load_json("results.json"), load_json("design_freeze.json"), load_json("diagnostics.json")

st.title("Volatility Surface Skew & State-Dependent Price Responses")
st.caption("SPXW option skew (OPRA cbbo-1m) × E-mini S&P 500 futures (CME bbo-1s) · 2025 regular sessions · "
           "primary: 5-minute horizon, Method A · ES order flow is a 1-second SAMPLED best-quote imbalance "
           "(deviation D-014), not event-level OFI · working paper by Chantry Fultz")

PAGES = ["Overview", "Method demo (synthetic)", "Data & Measurement", "Surface Explorer",
         "Conditional Responses", "Robustness & Prediction"]
page = st.sidebar.radio("Page", PAGES)
st.sidebar.markdown("---")
st.sidebar.caption(f"Status: {res['status'] if res else 'pending'}")
if frz:
    st.sidebar.caption(f"Design frozen {frz['frozen_utc'][:16].replace('T', ' ')} UTC")

# ---------------------------------------------------------------------------- overview ----
if page == "Overview":
    st.subheader("Question")
    st.write("When S&P 500 options price downside protection expensively, do E-mini futures prices behave "
             "differently in the minutes after a burst of selling pressure? The option-surface state is "
             "measured strictly **before** the pressure; the price response strictly **after** it.")
    if res:
        p, fc, g = res["final"]["primary"], res["forecast"], res["gates"]
        a, b = st.columns(2)
        a.metric("δ_sell (bps per SD²)", f"{p['delta_sell']:+.3f}")
        b.metric("95% CI (block bootstrap)", f"[{p['ci_basic'][0]:.2f}, {p['ci_basic'][1]:.2f}]")
        c, d = st.columns(2)
        c.metric("Two-sided p", f"{p['p_null_centered']:.2f}")
        d.metric("Final sessions", f"{p['sessions']}")
        lo, hi = p["ci_basic"]
        verdict = ("The interval includes zero: the data do not show that downside skew changes the "
                   "response to selling pressure." if lo <= 0 <= hi else
                   "The interval excludes zero.")
        st.success(f"**Result.** {verdict} Adding skew to a flow-only model changes frozen out-of-sample "
                   f"squared error by {fc['incremental_1_minus_SSE_ratio']:+.4f} (1 − SSE ratio; negative = worse).")
        st.write(f"Sample: {g['total']} qualifying 2025 sessions (dev {g['dev']}, validation {g['val']}, "
                 f"final {g['final']}); all data-quality gates passed.")
    image("fig1_timeline.png")
    st.markdown("**Pages:** *Method demo* lets you run the skew measurement on synthetic quotes. The other "
                "pages show the saved empirical results (no data are downloaded by this app).")

# -------------------------------------------------------------------------- method demo ----
elif page == "Method demo (synthetic)":
    from surface_impact.parity import fit_parity
    from surface_impact.pricing import implied_w_vec, norm_call
    from surface_impact.skew import TARGET_TAU, curve_a, curve_b, skew_from_curve
    from surface_impact.surfaces import LAMBDA_GRID, SmileObs, fit_method_a, fit_method_b

    st.subheader("Method demo: from noisy option quotes to a 30-day 25-delta skew")
    st.caption("SYNTHETIC data generated in your browser session. This illustrates the measurement "
               "pipeline used in the paper; it is not market data and says nothing about the empirical result.")
    c1, c2, c3 = st.columns(3)
    atm = c1.slider("ATM implied vol (%)", 8.0, 40.0, 15.0, 0.5) / 100
    slope = c1.slider("Skew slope (vol per unit log-moneyness)", -0.8, 0.3, -0.25, 0.05)
    curv = c1.slider("Smile curvature", 0.0, 2.0, 0.4, 0.1)
    d1 = c2.slider("Near expiry (days)", 14, 29, 27)
    d2 = c2.slider("Far expiry (days)", 31, 60, 34)
    spacing = c2.select_slider("Strike spacing (index points)", [5, 10, 25, 50], 10)
    half = c3.slider("Quoted half-spread (index points)", 0.05, 3.0, 0.5, 0.05)
    noise = c3.slider("Mid-price noise (share of half-spread)", 0.0, 1.0, 0.5, 0.05)
    lam = c3.select_slider("Method A roughness λ", list(LAMBDA_GRID), 1.0)
    seed = c3.number_input("Random seed", 0, 10_000, 1729)
    F, r = 6000.0, 0.045

    def iv_true(k):
        return np.maximum(atm + slope * k + curv * k**2, 0.03)

    @st.cache_data(show_spinner="Fitting parity, Method A and SVI…")
    def run_demo(atm, slope, curv, d1, d2, spacing, half, noise, lam, seed):
        rng = np.random.default_rng(int(seed))
        out = {}
        for name, days in (("near", d1), ("far", d2)):
            tau = days / 365
            D = math.exp(-r * tau)
            K = np.arange(math.ceil(0.85 * F / spacing) * spacing, 1.15 * F + 1e-9, spacing)
            x = K / F
            w = iv_true(np.log(x)) ** 2 * tau
            C = D * F * norm_call(x, w)
            P = C - D * (F - K)
            rows = []
            for right, prem in (("C", C), ("P", P)):
                mid = prem + noise * half * rng.uniform(-1, 1, len(K))
                bid, ask = mid - half, mid + half
                keep = bid > 0.05
                rows.append(pd.DataFrame({"right": right, "K": K[keep], "bid": bid[keep], "ask": ask[keep]}))
            q = pd.concat(rows)
            c, p = q[q.right == "C"].set_index("K"), q[q.right == "P"].set_index("K")
            Ks = np.array(sorted(set(c.index) & set(p.index)))
            fit = fit_parity(Ks, c.loc[Ks, "bid"], c.loc[Ks, "ask"], p.loc[Ks, "bid"], p.loc[Ks, "ask"], tau)
            if not fit.ok:
                return {"error": f"{name} expiry: parity failed ({fit.reason})"}
            xs = np.r_[c.index.to_numpy() / fit.F, p.index.to_numpy() / fit.F]
            mids = np.r_[((c.bid + c.ask) / 2).to_numpy() / fit.A,
                         ((p.bid + p.ask) / 2).to_numpy() / fit.A + 1 - p.index.to_numpy() / fit.F]
            los = np.r_[c.bid.to_numpy() / fit.A, p.bid.to_numpy() / fit.A + 1 - p.index.to_numpy() / fit.F]
            his = np.r_[c.ask.to_numpy() / fit.A, p.ask.to_numpy() / fit.A + 1 - p.index.to_numpy() / fit.F]
            obs = SmileObs(xs, mids, los, his, np.r_[np.zeros(len(c), bool), np.ones(len(p), bool)])
            out[name] = {"tau": tau, "obs": obs, "F": fit.F, "D": fit.D, "D_true": D, "pairs": len(Ks),
                         "A": fit_method_a(obs, lam), "B": fit_method_b(obs, tau)}
        n, f_ = out["near"], out["far"]
        sa = skew_from_curve(*curve_a(n["A"], n["tau"], f_["A"], f_["tau"])) if n["A"].ok and f_["A"].ok else None
        sb = skew_from_curve(*curve_b(n["B"], n["tau"], f_["B"], f_["tau"])) if n["B"].ok and f_["B"].ok else None
        # truth: the same IV smile at exactly 30 days
        g = np.linspace(0.8, 1.2, 40001)
        wt = iv_true(np.log(g)) ** 2 * TARGET_TAU
        dl = 0.5 * (1 + np.vectorize(math.erf)(((-np.log(g) + wt / 2) / np.sqrt(wt)) / math.sqrt(2)))
        xc, xp = np.interp(0.25, dl[::-1], g[::-1]), np.interp(0.75, dl[::-1], g[::-1])
        truth = {"sk30": float(iv_true(math.log(xp)) - iv_true(math.log(xc))), "atm": float(iv_true(0.0)),
                 "xc": float(xc), "xp": float(xp)}
        return {"exp": out, "A": sa, "B": sb, "truth": truth}

    R = run_demo(atm, slope, curv, d1, d2, spacing, half, noise, lam, seed)
    if "error" in R:
        st.error(R["error"])
        st.stop()
    t, sa, sb = R["truth"], R["A"], R["B"]
    st.caption("SK30 and ATM IV in vol points; the small number under each estimate is its error vs the truth.")
    m1, m2 = st.columns(2)
    m1.metric("True SK30", f"{100 * t['sk30']:.3f}")
    m2.metric("Method A SK30", f"{100 * sa.sk30:.3f}" if sa and sa.ok else "n/a",
              f"{100 * (sa.sk30 - t['sk30']):+.3f}" if sa and sa.ok else None, delta_color="off")
    m3, m4 = st.columns(2)
    m3.metric("SVI SK30", f"{100 * sb.sk30:.3f}" if sb and sb.ok else "n/a",
              f"{100 * (sb.sk30 - t['sk30']):+.3f}" if sb and sb.ok else None, delta_color="off")
    m4.metric("Method A ATM IV", f"{100 * sa.atm_iv:.2f}" if sa and sa.ok else "n/a",
              f"{100 * (sa.atm_iv - t['atm']):+.2f} (true {100 * t['atm']:.2f})" if sa and sa.ok else None,
              delta_color="off")

    import plotly.graph_objects as go

    fig = go.Figure()
    colors = {"near": "#2a6f97", "far": "#c8553d"}
    for name, e in R["exp"].items():
        o = e["obs"]
        iv_mid = np.sqrt(implied_w_vec(o.c_mid, o.x) / e["tau"]) * 100
        fig.add_trace(go.Scatter(x=np.log(o.x), y=iv_mid, mode="markers", marker=dict(size=4, color=colors[name], opacity=0.45),
                                 name=f"quotes, {name} ({e['tau'] * 365:.0f}d)"))
        xs = np.linspace(o.x.min(), o.x.max(), 300)
        if e["A"].ok:
            fig.add_trace(go.Scatter(x=np.log(xs), y=np.sqrt(implied_w_vec(e["A"](xs), xs) / e["tau"]) * 100, mode="lines",
                                     line=dict(color=colors[name]), name=f"Method A, {name}"))
        if e["B"].ok:
            fig.add_trace(go.Scatter(x=np.log(xs), y=np.sqrt(e["B"].w(np.log(xs)) / e["tau"]) * 100, mode="lines",
                                     line=dict(color=colors[name], dash="dash"), name=f"SVI, {name}"))
    kk = np.linspace(-0.16, 0.14, 300)
    fig.add_trace(go.Scatter(x=kk, y=iv_true(kk) * 100, mode="lines", line=dict(color="#9a9a9a", dash="dot", width=2),
                             name="true smile"))
    if sa and sa.ok:
        fig.add_trace(go.Scatter(x=[math.log(sa.x_put25), math.log(sa.x_call25)], y=[100 * sa.iv_put25, 100 * sa.iv_call25],
                                 mode="markers", marker=dict(size=11, symbol="diamond", color="#111"),
                                 name="30d 25Δ put / call (Method A)"))
    fig.update_layout(height=470, xaxis_title="log(K / F)", yaxis_title="Black implied vol (%)",
                      legend=dict(orientation="h", y=-0.2), margin=dict(t=20))
    st.plotly_chart(fig, use_container_width=True)

    rows = []
    for name, e in R["exp"].items():
        rows.append({"expiry": name, "days": round(e["tau"] * 365, 1), "matched pairs": e["pairs"],
                     "forward: true / parity": f"{F:.2f} / {e['F']:.2f}",
                     "discount: true / parity": f"{e['D_true']:.5f} / {e['D']:.5f}",
                     "Method A ok": e["A"].ok, "SVI ok": e["B"].ok,
                     "SVI dense-grid checks": ", ".join(k for k, v in e["B"].dense_checks.items() if not v) or "all pass"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("What is happening here?"):
        st.markdown(
            "1. **Quotes.** Calls and puts are priced from the true smile, then given bid/ask spreads and mid noise.\n"
            "2. **Parity.** A robust, spread-weighted fit of C − P = D(F − K) recovers the forward and discount "
            "factor from the quotes alone.\n"
            "3. **Method A.** A convex QP fits normalized call prices with no-arbitrage shape constraints "
            "(bounds, slopes in [−1, 0], convexity) and roughness λ.\n"
            "4. **Method B.** Raw SVI fitted in premium space, with finite-grid shape checks reported, not assumed.\n"
            "5. **30-day skew.** Interpolate to exactly 30 days, solve for the ±25 forward-delta strikes and take "
            "SK30 = IV(25Δ put) − IV(25Δ call).\n\nTry a wide spread with high noise, or λ = 0 vs 1, and see how "
            "the estimate moves. Large λ trades a little bias for stability. In the far wings, premiums are "
            "close to the quote noise, so fitted implied vols there are unstable (the same edge effect appears "
            "in the paper's real smile). The 25-delta points sit well inside that region.")

# ----------------------------------------------------------------- data & measurement ----
elif page == "Data & Measurement":
    cov = load_csv("coverage.csv")
    if cov is None:
        missing("coverage.csv")
    else:
        st.subheader("Consecutive-session coverage (every planned 2025 session)")
        image("fig2_coverage.png")
        st.dataframe(cov, use_container_width=True, height=260)
        if res:
            st.json(res["gates"])
    if diag:
        st.subheader("Measurement diagnostics")
        a, b, c = st.columns(3)
        a.metric("Parity fits OK", f"{100 * diag['parity']['parity_ok_rate']:.2f}%")
        b.metric("Skew states formed", f"{100 * diag['quote_quality']['state_ok_rate']:.2f}%")
        c.metric("Method A vs B SK30 corr.", f"{diag['method_agreement']['corr_sk30_A_B']:.4f}")
        st.subheader("Data cost (metadata-estimate basis, USD)")
        st.json(diag["costs"])
    att = load_csv("attrition.csv")
    st.subheader("Surface-state attrition (primary variant, all 5-minute cutoffs)")
    if att is not None:
        st.dataframe(att)
    else:
        missing("attrition.csv")
    roll = load_csv("roll_calendar.csv")
    if roll is not None:
        st.subheader("ES roll calendar")
        st.dataframe(roll[roll.roll_day.astype(bool)], hide_index=True)

# ------------------------------------------------------------------- surface explorer ----
elif page == "Surface Explorer":
    image("fig6_skew_timeseries.png")
    image("fig3_smile.png")
    stp = RUN / "skew_states.parquet"
    if stp.exists():
        s = pd.read_parquet(stp, columns=["date", "cutoff", "variant", "ok", "sk30", "atm_iv"])
        v = st.selectbox("Variant", sorted(s.variant.unique()))
        d = st.selectbox("Session", sorted(s.date.unique()))
        sub = s[(s.variant == v) & (s.date == d)]
        st.line_chart(sub.set_index("cutoff")[["sk30", "atm_iv"]])
    else:
        st.caption("Five-minute skew states are derived from licensed quotes and are not distributed with this "
                   "deployment; the figures above summarize them.")

# --------------------------------------------------------------- conditional responses ----
elif page == "Conditional Responses":
    if res is None:
        missing("results.json")
    else:
        p = res["final"]["primary"]
        st.subheader("Primary: δ_sell (sell pressure × skew), h = 5 min, Method A, final period")
        a, b, c = st.columns(3)
        a.metric("δ_sell (bps / SD / SD)", f"{p['delta_sell']:.3f}")
        b.metric("95% block-bootstrap CI", f"[{p['ci_basic'][0]:.3f}, {p['ci_basic'][1]:.3f}]")
        c.metric("Two-sided p (null-centered)", f"{p['p_null_centered']:.3f}")
        st.write(f"Sessions: {p['sessions']} · rows: {p['rows']} · bootstrap replications OK: {p['n_success']}")
        sl = p["sell_slope_by_z"]
        st.dataframe(pd.DataFrame([{"z (skew SD)": z, "selling-pressure slope (bps)": round(v["estimate"], 3),
                                    "95% CI": f"[{v['ci_basic'][0]:.3f}, {v['ci_basic'][1]:.3f}]"} for z, v in sl.items()]),
                     hide_index=True)
    image("fig4_horizon.png")
    coef = load_csv("coefficients.csv")
    if coef is not None:
        st.dataframe(coef, hide_index=True)
    else:
        missing("coefficients.csv")

# --------------------------------------------------------------- robustness & prediction ----
else:
    rob = load_csv("robustness.csv")
    st.subheader("Robustness of δ_sell")
    image("fig5_robustness.png")
    if rob is not None:
        st.dataframe(rob, hide_index=True)
    else:
        missing("robustness.csv")
    met = load_csv("forecast_metrics.csv")
    st.subheader("Frozen pretest forecasts evaluated on the final period")
    if met is not None:
        st.dataframe(met)
    else:
        missing("forecast_metrics.csv")
    if res:
        st.json(res["forecast"])
    ms = load_csv("measurement_stress.csv")
    st.subheader("Quote-perturbation measurement stress (not confidence intervals)")
    if ms is not None:
        st.dataframe(ms, hide_index=True)
    else:
        missing("measurement_stress.csv")
