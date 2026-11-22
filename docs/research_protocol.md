# Research protocol, version 0.2 (locked retrospective design)

This is a locked retrospective historical study, not a preregistration made in 2024. The design
below was fixed before final-period (Sep–Dec 2025) outcomes were read. The freeze is recorded in
`outputs/main/design_freeze.json` (UTC timestamp, source hash, ledger hash).

## Question and hypotheses
Does the 30-day 25-delta downside skew of the SPXW surface add to the relationship between observed
ES selling pressure and subsequent ES price continuation or reversal, after accounting for volatility,
spread, depth and recent returns?

- **Primary null:** the coefficient on s·z (delta_sell) is zero at h = 5 minutes, Method A, final period.
- **Directional hypothesis (not used to choose the test):** delta_sell < 0, i.e. pricier downside
  protection goes with a more negative subsequent response to selling pressure. The test is two-sided.
- Estimates are **conditional, observational price responses**. They do not identify trade impact,
  informed trading, dealer hedging or permanent value changes.

## Markets and data (Databento, historical)
- SPXW (European, PM cash-settled; root SPXW per Cboe specifications) quotes: OPRA.PILLAR
  `cbbo-1m`, consolidated NBBO 1-minute snapshots. Contracts are explicitly selected per session from
  that day's definitions: the nearest PM expiries bracketing 30 days over the session's 5-minute
  cutoffs (14–90 days), all listed strikes within 0.85–1.15 × the previous session's ES reference
  close, calls and puts.
- ES: GLBX.MDP3 `bbo-1s` for the dated contract active under the fixed roll (Thursday 8 calendar days
  before the third-Friday expiry, at session start), 08:30 NY → close. **Deviation D-014:** the
  specification called for event-level `mbp-1`, which costs $210–$380 against the $31.09 ceiling. Order
  flow is therefore a 1-second sampled best-quote imbalance. A one-day audit (2024-02-01) found a
  per-minute correlation of 0.92 with event-level OFI.
- Calendar: XNYS sessions (holidays, unscheduled closures, early closes), America/New_York clock,
  UTC internally.

## Design and splits (12-month; chosen by cost rule before outcomes)
Development Jan–Jun 2025 · validation Jul–Aug 2025 · final Sep–Dec 2025. The 24-month design did
not fit the ceiling.

## Timeline (locked)
Observation endpoints t from 09:40 to close − 15 min. Flow window (t − 60 s, t]. Outcomes: ES midquote
log returns over (t, t + h], h ∈ {1, 2, 5, 10, 15} min, with 5 min primary; delayed variant
(t + 5 s, t + 5 s + h]. Surface cutoffs s every 5 minutes on the session clock. Only cbbo-1m intervals
ending at or before s are used. The state is available at s + max(5 s, measured fit time). A state
is admissible at t if s ≤ t − 120 s, available ≤ t − 60 s and t − s ≤ 600 s. ES quotes must be at
most 5 s old at each endpoint.

## Measurement
Spread-aware robust parity fit per expiry (at least 4 pairs; implied rate in (−10%, 25%)). Normalized
coordinates x = K/F, c = C/A, with puts converted via parity. **Method A (primary):** convex QP on
normalized calls with bounds, slopes in [−1, 0], convexity, a soft bid/ask band and roughness λ
selected from {0, 1e-4, 1e-2, 1} by held-out strike-pair price reconstruction on development noon
snapshots. **Method B (robustness):** raw SVI per expiry in premium space, with dense-grid checks; no
global no-arbitrage claim. The 30-day curve interpolates in maturity (Method A: normalized calls;
Method B: total variance) with no extrapolation. SK30 = IV(25Δ put) − IV(25Δ call) under the
undiscounted forward-delta convention, with unique bracketed roots inside common support. Quote
filters: two-sided, positive sizes, record age ≤ 120 s, relative spread ≤ 0.50. Sensitivities:
60/180 s and 0.25/1.00, one at a time, on the same universe.

## Models and inference
M_PRICE ⊂ M_FLOW ⊂ M_SKEW (see `regressions.py`), with pretest-only transforms. Final association:
OLS on final-period rows of qualifying sessions. Uncertainty: moving-block bootstrap over 5 consecutive
scheduled sessions of the final calendar grid, 1,999 replications, seed 1729. The two-sided test is
null-centered and the 95% CI is the basic bootstrap interval. Diagnostics: block lengths 1 and 10, and
day-clustered SEs. Secondary family: b·z and s·z at all horizons except the primary, Holm-adjusted.
Forecasts: M_FLOW and M_SKEW fitted once on dev + val and evaluated on the final period (MAE, MSE,
OOS R² vs the pretest mean, 1 − SSE_skew/SSE_flow, date-balanced variants, paired block bootstrap).

## Gates for "main analysis" status
At least 150 qualifying sessions overall, ≥ 70 dev, ≥ 25 val, ≥ 50 final. A session qualifies with
≥ 75% valid joint endpoints. Each pretest-defined low/high skew group needs ≥ 10 final sessions. Units,
timestamps, mappings, OFI construction and price/expiry support must be verified. Because of D-014,
the highest attainable label is "main analysis on sampled order flow (deviation D-014)".

## Robustness (prespecified)
- Method B, plus the four quote-filter sensitivities (common-observation and separate-coverage estimates).
- Delayed endpoints and the 10 s flow-gap threshold.
- Roll-day exclusion.
- Pretest-defined skew bins (descriptive) and final-month stability (small-sample caution).
- A five-session state-lag placebo.
- Quote-perturbation measurement stress (25 draws at calendar-chosen 12:00 snapshots).
