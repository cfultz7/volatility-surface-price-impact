# Data dictionary: `outputs/main/minute_panel.parquet`

One row per (session, minute endpoint t, state variant). t runs 09:40 → close − 15 min (New York),
stored as integer nanoseconds UTC. All windows are relative to t. Protocol v0.2: ES order flow comes
from GLBX `bbo-1s` (deviation D-014). "Sampled" means computed from consecutive 1-second BBO records,
not from every book event.

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

Transforms (fit on pretest dev+val rows only, stored in `outputs/main/transforms.json`):
q = clip(`ofi_norm`, p0.5, p99.5) / SD_pre (no demeaning), b = max(q, 0), s = max(−q, 0),
z = (`sk30` − mean_pre)/SD_pre, other controls standardized with pretest mean/SD.
