# Technical appendix

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
s_j = (c_{j+1} − c_j)/(x_{j+1} − x_j):

minimize  (1/n)Σ_i ((c(x_i) − c_mid,i)/h_i)² + 100·(1/n)Σ_i ((c(x_i) − c_hi,i)⁺ + (c_lo,i − c(x_i))⁺)²/h_i² + λ·Σ_j (s_{j+1} − s_j)²/Δx̄_j

subject to max(1 − x_j, 0) ≤ c_j ≤ 1, −1 ≤ s_j ≤ 0, s_{j+1} ≥ s_j,

where i runs over all call and put observations (puts converted by parity), h_i = max(half-spread_i,
2×10⁻⁵) and Δx̄_j = (x_{j+2} − x_j)/2. Between knots the fitted call is linear, so the constraints hold on the
whole observed support. The problem is solved with CLARABEL via CVXPY. The 30-day curve is
c₃₀(x) = a·c₁(x) + (1 − a)·c₂(x) with a = (τ₂ − τ₃₀)/(τ₂ − τ₁). This is a convex combination, which
preserves the strike constraints on common support. λ selection uses five strike folds per expiry
(strike rank mod 5); held-out quotes inside the training support are scored. Results:

| lambda | mean held-out loss |
|---|---|
| 0.0 | 3.7360 |
| 0.0001 | 3.7357 |
| 0.01 | 3.7262 |
| 1.0 | 3.7055 |

**Smoothing bias (SYNTHETIC).** On a smooth, noise-free asymmetric smile (true SK30 1.452 vol points), the selected λ = 1.0 understates SK30 by 0.032 vol points (2.2%) and overstates ATM IV by 0.052 vol points. λ = 0 errs by -0.0023. The cross-validation preferred λ = 1.0 on real, noisy quotes. A near-constant proportional compression of skew is removed by standardizing z, so it should not affect the sign or test of δ_sell. It does mean SK30 levels are slightly conservative.

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
| Column | Window / timing | Units | Definition |
|---|---|---|---|
| `date`, `split` | session | – | NY session date; `dev` Jan–Jun, `val` Jul–Aug, `final` Sep–Dec 2025 |
| `t` | endpoint | ns UTC | observation endpoint (minute boundary) |
| `variant` | – | – | skew state source: `A_primary`, `B_primary`, `A_age60`, `A_age180`, `A_spr025`, `A_spr100` |
| `es_contract`, `roll_day`, `es_dte` | session | –, bool, days | active dated ES contract under the fixed roll; roll-day flag; calendar days to the contract's expiry date |
| `ofi_raw` | (t−60s, t] | contracts | Σ Cont-Kukanov-Stoikov increments between consecutive valid bbo-1s records with `ts_recv` in the window (**sampled OFI**) |
| `depth` | (t−60s, t] | contracts | time-weighted mean of (Q_bid + Q_ask)/2, using the state prevailing each second |
| `ofi_norm` | (t−60s, t] | – | `ofi_raw / depth`, only if `flow_ok` |
| `flow_ok` | (t−60s, t] | bool | no record gap > 5 s and no invalid state in the window (`flow_ok_10s`: 10 s sensitivity) |
| `spread_bps` | (t−60s, t] | bps | time-weighted mean (ask − bid)/mid × 1e4 |
| `log_depth` | (t−60s, t] | log contracts | log of `depth` |
| `mid` | at t | index pts | ES midquote as of t if the latest record is ≤ 5 s old, else NaN |
| `r_flow` | (t−60s, t] | bps | flow-minute return 1e4·log(mid_t / mid_{t−1m}) (control and contemporaneous diagnostic; **not** an outcome) |
| `r_prev5` | (t−6m, t−1m] | bps | prior 5-minute return, excluding the flow minute |
| `r_prev60` | (t−61m, t−1m] | bps | prior 60-minute return, excluding the flow minute |
| `rv30` | (t−31m, t−1m] | bps per minute | RMS of the 30 one-minute returns before the flow minute (no annualization) |
| `ofi_lag1` | (t−120s, t−60s] | – | previous minute's `ofi_norm` |
| `y_h`, h in {1,2,5,10,15} | (t, t+h] | bps | **outcome**: 1e4·log(mid_{t+h} / mid_t); NaN if t+h+5 s passes the session close (so the last row of each session has no y_15; D-024) |
| `yd_h` | (t+5s, t+5s+h] | bps | delayed-endpoint robustness outcome |
| `sk30` | state | decimal IV | 30-day 25-delta skew IV(put 25Δ) − IV(call 25Δ), forward-delta convention, from the latest admissible state |
| `atm_iv` | state | decimal IV | 30-day ATM-forward (x = 1) implied vol from the same state |
| `state_cutoff`, `state_age_s` | state | ns UTC, s | cutoff s of the state used: s ≤ t−120 s, available ≤ t−60 s, t − s ≤ 600 s |
| `n_expiries_usable`, `usable`, `band_violation` | state | count, count, normalized | quote-quality variables of the selected state |
| `sess_min`, `sin1..cos2` | at t | minutes, – | minutes since 09:30 and first/second Fourier harmonics over the actual session length |
| `weekday`, `early_close` | session | 0–4, bool | weekday controls (Monday base) and the early-close flag |

## H. Bootstrap algorithm and tests
1. List the G = 85 scheduled final sessions in calendar order, including any without
   valid rows.
2. For b = 1, …, 1999 (seed 1729): draw ceil(G/5) block starts uniformly from the G − 4 possible starts, and
   concatenate the five consecutive session positions of each block, truncated to G.
3. Collect every original observation of each drawn session, relabelling repeated copies. Returns are
   never recomputed across block boundaries.
4. Refit the frozen M_SKEW regression to get θ*_b.
5. Report θ_hat, the basic interval [2θ_hat − q(0.975), 2θ_hat − q(0.025)] of the draws, the percentile
   interval, and the null-centered p = (1 + #{|θ*_b − θ_hat| ≥ |θ_hat|})/(B + 1).

The secondary family (b·z and s·z at 1, 2, 5, 10, 15 minutes, excluding the primary) is Holm-adjusted.
Forecast comparisons resample paired daily blocks of losses. Synthetic checks (labelled SYNTHETIC):
null rejection 6.0% (Monte Carlo SE
1.7%, 200 replications);
power 100.0% at δ = −0.25.

## I. Full final-period M_SKEW coefficients (h = 5, Method A)
All regressors are standardized with pretest moments. Intervals are basic 95% moving-block bootstrap
intervals (5-session blocks, 1,999 replications). Rank and conditioning: rank
29, condition number 19.6.

| term | estimate | se_boot | ci_lo | ci_hi | p_null_centered |
|---|---|---|---|---|---|
| const | 1.0806 | 0.6263 | -0.3445 | 2.1249 | 0.0885 |
| atm_iv | 0.7343 | 0.8502 | -1.4576 | 1.9308 | 0.2975 |
| rv30 | 0.2830 | 0.4253 | -0.7154 | 0.9668 | 0.5005 |
| spread_bps | 0.8967 | 0.5713 | -0.3197 | 1.9705 | 0.1150 |
| log_depth | 0.3125 | 0.2121 | -0.0906 | 0.7350 | 0.1360 |
| r_flow | -0.0836 | 0.2218 | -0.5156 | 0.3575 | 0.7280 |
| r_prev5 | 0.0471 | 0.2563 | -0.3923 | 0.6153 | 0.8510 |
| r_prev60 | 0.1898 | 0.1773 | -0.0485 | 0.6352 | 0.3025 |
| ofi_lag1 | -0.0650 | 0.0523 | -0.1798 | 0.0279 | 0.2180 |
| sin1 | -0.0960 | 0.0910 | -0.2668 | 0.0894 | 0.2915 |
| cos1 | 0.0092 | 0.0837 | -0.1414 | 0.1880 | 0.9115 |
| sin2 | -0.0518 | 0.0774 | -0.1876 | 0.1105 | 0.5100 |
| cos2 | 0.1305 | 0.0671 | 0.0219 | 0.2827 | 0.0635 |
| es_dte | 0.0165 | 0.1014 | -0.2215 | 0.1404 | 0.7985 |
| early_close | 0.0482 | 0.0435 | 0.0082 | 0.1908 | 0.1360 |
| roll_day | 0.0772 | 0.1080 | -0.0833 | 0.3389 | 0.4265 |
| wd1 | 0.0808 | 0.1769 | -0.2419 | 0.4492 | 0.6570 |
| wd2 | -0.1328 | 0.2623 | -0.6147 | 0.4224 | 0.6020 |
| wd3 | -0.6406 | 0.3736 | -1.1914 | 0.2491 | 0.0915 |
| wd4 | -0.1776 | 0.3408 | -0.7981 | 0.5486 | 0.5930 |
| b | -0.1317 | 0.1802 | -0.4939 | 0.1926 | 0.4585 |
| s | -0.0004 | 0.1752 | -0.3458 | 0.3312 | 0.9985 |
| b_rv | -0.3420 | 0.5065 | -1.2546 | 0.7566 | 0.4945 |
| s_rv | -0.2620 | 0.4145 | -1.0878 | 0.5570 | 0.5480 |
| b_ld | -0.0513 | 0.1815 | -0.2634 | 0.4717 | 0.7180 |
| s_ld | -0.0605 | 0.2241 | -0.3221 | 0.5427 | 0.7420 |
| z | -0.0907 | 0.5580 | -0.9005 | 1.3117 | 0.8555 |
| b_z | -0.2630 | 0.2452 | -0.8490 | 0.1162 | 0.2590 |
| s_z | 0.1144 | 0.2931 | -0.5739 | 0.6119 | 0.6705 |

## J. Additional diagnostics
- **Parity:** 39636 expiry fits, success rate 99.99%, median 79 pairs, mean interval violations 0.0013 per fit. Failures were all too few matched pairs (5 fits).
- **Quote quality:** 466.3 of 483.4 contracts usable per cutoff. Of 19392 primary-state cutoffs, 10 failed: no usable 30d bracket (6); call 25d root: 0 crossings in support (need exactly 1) (3); IV inversion failed on grid: interpolated price outside no-a (1).
- **Method agreement:** SK30 correlation 0.9984; mean B − A difference -0.017 vol points; SVI dense-grid pass rate 45.8%.
- **Skew variance:** total SD of five-minute SK30 states 2.01 vol points; within-day share 2.9%.
- **Half-day expiry sensitivity (D-024):** 32 sessions, 857 affected cutoffs. Mean |ΔSK30| 0.0059 vol points, max 0.126; max |ΔATM IV| 0.054 vol points. States lost under the correction: 96, on 2025-06-03, 2025-10-29, 2025-11-24.

## K. Data acquisition, costs and reproducibility
- **Costs:** total $25.48 (metadata-estimate basis). By schema: {"bbo-1s": 9.38, "cbbo-1m": 7.056, "definition": 7.893, "mbp-1": 0.623, "ohlcv-1d": 0.003, "status": 0.0}. Failed downloads counted conservatively: $1.31, plus $0.53 from earlier releases.
- **Design freeze:** 2026-11-22T01:43:37.426790+00:00 UTC; source SHA-256 a348e12a46c3241518c46d09c17f5076c4c083b80676aac5b7cad6d4fcd65cd3; ledger SHA-256 8ad5f99fa1d1500ad0e9d12aec5c9eaa2d6e2c24bb98ac9e3eb63defa59aa3ad.
- **Environment:** Python 3.12 (uv), databento 0.90.0, cvxpy 1.9.3 (CLARABEL), exchange_calendars 4.13.3, Windows 11 for the full pipeline; offline tests also pass on Ubuntu and macOS in GitHub Actions.
- **Commands (in order):** `surface_impact.planning`, `acquire`, `select_lambda`, `run_measure`, `analyze all`, `stress`, `diagnostics`, `expiry_sensitivity`, `figures`, `manuscript`, `writeups` (see README). All billable steps go through the cached, lock-protected ledger; reruns never pay twice.
- **Data rights:** raw OPRA/CME data are licensed and not distributed. Derived aggregates and figures only.
- **Decisions:** all deviations and choices are logged in `docs/decisions.md`. The key ones are D-014 (sampled flow), D-020 (λ), D-021 (estimation sample), D-022 (batch definitions), D-023 (freeze) and D-024 (post-freeze review findings).
