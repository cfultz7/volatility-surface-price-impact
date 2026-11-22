# Decision log

Each entry: ID, date, decision, reason, and whether it departs from the specification.

- **D-001 (2026-11-21) Python 3.12 via uv.** Python 3.15 is the system default. The spec requests
  3.12, which is installed locally, so `requires-python = ">=3.12,<3.13"`.
- **D-002 (2026-11-21) API key handling.** `data_sources.load_api_key()` reads `DATABENTO_API_KEY`
  from the environment first and falls back to a gitignored `.env` in the project root. The key is
  never written to code, logs, manifests or the ledger. It was rotated after setup.
- **D-003 (2026-11-21) Data location.** The project folder is inside OneDrive. Licensed raw data and
  the 40 GiB cache should not sync to OneDrive. `data/` is gitignored, but OneDrive will still
  sync it. Before main acquisition the data root must move outside OneDrive (a configurable
  `data_root` is planned). Pilot-scale files stay in `data/` for now.
- **D-004 (2026-11-21) Session calendar.** Common sessions come from the `exchange_calendars` XNYS
  calendar (v4.13.3). For 2024–2025 it gives 502 sessions with 6 early closes (13:00 NY) and
  excludes the 2025-01-09 national day of mourning. It still has to be audited against Cboe/CME
  status records once those are acquired. OPRA `status` is only available from 2024-07-17 (vendor
  metadata), so the 2024H1 audit must rely on definitions, quotes and the calendar.
- **D-005 (2026-11-21) ES symbology verified.** `symbology.resolve` (GLBX.MDP3, raw_symbol to
  instrument_id, 2024-02-01) maps ESH4 to 17077, ESU4 to 118 and ESZ4 to 183748. `ES.c.0` maps to
  17077. This confirms the single-digit year code. Every roll-period symbol is still checked
  against definitions before use.
- **D-006 (2026-11-21) Provisional SPXW expiry instant = 16:00 NY on the expiry date**, used only for
  contract pre-selection in planning. It must be checked against definition `expiration` fields
  and Cboe specifications before IV computation.
- **D-007 (2026-11-21) Free SPXW contract listing.** `symbology.resolve(OPRA.PILLAR, ["SPXW.OPT"],
  parent → instrument_id)` returns every listed SPXW OSI symbol for the date at no charge. Contract
  pre-selection (expiry bracket, strike band, C/P) parses the OSI string. Definitions remain
  required for settlement style, exercise style, multiplier and the exact expiry before
  measurement. Definitions are also needed to confirm instrument IDs per date, though explicit
  raw-symbol requests do not require them.
- **D-008 (2026-11-21) Planning-only strike-band reference.** The paid ES daily reference request
  was refused (see B-001), so stage-2 planning uses TradingView `CME_MINI:ES1!` daily regular-
  session closes (`configs/planning_es_reference.json`) to size the 0.85–1.15 band for free cost
  estimates. These values are never used in measurement. The study's reference remains the prior
  eligible session's close of the dated ES contract from Databento.
- **D-009 (2026-11-21) Expiry bracket for acquisition.** For each 5-minute cutoff, the bracket is
  the nearest PM expiry at or below, and at or above, cutoff + 30 days, among expiries 14–90 days
  away. Only the union over the session's cutoffs is requested. Fallback expiries (second-nearest)
  are **not** acquired by default. If the pilot shows the nearest expiry is often unusable, a
  costed fallback rule will be proposed before any main acquisition.

- **D-010 (2026-11-21) Budget context.** The spec's provisional $50 ceiling assumed $125 of account
  credits, but fewer credits were available at project start. The ceiling is revisited in D-011.
  Account balances are never read programmatically or inferred.
- **D-011 (2026-11-21) Ceiling lowered to $31.09 (author decision)**, keeping at least $75 of
  account credits reserved for other work. `configs/budget.yaml` now has
  `project_ceiling_usd: 31.09`, `main` phase limit 31.09, `pilot` limit 5.0 (inside the ceiling),
  and the 20% cushion unchanged. The spec's $25–40 target is capped at $31.09. Every padded
  reservation is checked against this ceiling.

- **D-012 (2026-11-21) ES schema cost comparison (free get_cost, 08:30 NY → 16:00 NY,
  three calendar-chosen days: 2024-02-01, 2025-03-04, 2025-10-15).** mbp-1 $0.62 / $1.70 / $1.41;
  mbp-10 $1.04 / $2.95 / $2.21; MBO $0.76 / $2.18 / $1.50; tbbo (trades only, not OFI)
  $0.69 / $2.20 / $1.24; bbo-1s (1-second *sampled* BBO, not event-level) $0.037 on each day.
  Starting at 09:30 instead of 08:30 saves 2–5%. **mbp-1 is the cheapest event-level schema.**
  bbo-1s would change the OFI statistic (spec §6, §12 forbid keeping the event-level claim on
  sampled data), so it was put to the author as an explicit decision rather than adopted silently.
- **D-013 (2026-11-21) Stage-1 result.** ES mbp-1, 08:30→close, all eligible sessions: 2024–2025
  $380.32 (211.8 GB billable); 2025 only $210.24 (116.8 GB). Both prespecified designs exceed
  the $31.09 ceiling by roughly 8–15× before padding. Per spec §5 the plan, pilot and independent
  code are finished first, and a specific incremental request is presented for a budget
  decision. Nothing is downgraded silently.

- **D-014 (2026-11-21) PROTOCOL DEVIATION, v0.1 → v0.2 (author decision): ES order flow from
  GLBX.MDP3 `bbo-1s` instead of event-level `mbp-1`.** Reason: event-level mbp-1 costs $210 (12 m)
  to $380 (24 m), against a $31.09 ceiling (D-011, D-013). No cheaper event-level schema exists
  (D-012). Consequences, which are binding on all outputs:
  1. The flow variable is renamed **1-second sampled best-quote imbalance (sampled OFI)**. It
     applies the Cont-Kukanov-Stoikov increment between *consecutive bbo-1s records* (BBO state at
     each printed interval end), not between book events. It misses intra-second events and
     reversals, so it is a coarse approximation of event-level OFI. The manuscript, README,
     dashboard and any summaries must not call it event-level OFI or "MBP-1 OFI".
  2. Spec §16's gate "event-level OFI … passed" cannot be met. The status ceiling for any
     result is therefore **"main analysis on sampled order flow (deviation D-014)"**, never an
     unqualified main-analysis-complete.
  3. bbo-1s semantics (Databento BBO docs, read 2026-11-21): `ts_recv` = interval end, clamped to
     the second; `ts_event` = last *trade* time (never used as quote age); a record is printed only
     when a BBO update or trade occurs in the interval, with the other side forward-filled. A
     missing second means "no update", but it cannot be told apart from an outage by absence
     alone. The gap heuristic plus status/flags decide (to be calibrated in the pilot).
  4. Measurement audit (optional, costed, within the ceiling): event-level mbp-1 on the
     development pilot date 2024-02-01 (≈ $0.62) to quantify the correlation between sampled and
     event-level OFI per minute. Audit results are reported but never select the design.
  5. Everything else in protocol v0.1 (horizons, skew definition, surfaces, inference, gates,
     splits) is unchanged. The 12- vs 24-month choice is re-made from costs under this schema
     using the spec's rule (24 m if its padded total fits, else 12 m).

- **D-015 (2026-11-21) Definition audit (pilot purchase, $0.054 consumed).** OPRA SPXW.OPT
  definitions for 2024-02-01 (15,406 instruments) and 2024-08-01 (16,790): `underlying`/`asset`
  = SPXW, `security_type` = OPT, `instrument_class` C/P balanced, `publisher_id` 30, strikes in
  index points, no duplicate raw symbols. **`expiration` is encoded as 00:00 UTC on the expiry
  date** (19:00/20:00 NY the prior evening). It is a date label, not the settlement instant, so
  D-006 stands: PM expiry = 16:00 NY on the expiry date, to be confirmed against Cboe
  specifications. **`contract_multiplier` = 2147483647 (undefined sentinel), `cfi` and
  `settl_currency` empty.** Definitions therefore cannot verify European exercise, PM settlement
  or the $100 multiplier; these rest on the Cboe SPXW specification for the SPXW root. ES:
  ESH4 id 17077 expires 2024-03-15 13:30 UTC; ESU4 id 118 expires 2024-09-20 13:30 UTC;
  `unit_of_measure_qty` 50, tick 0.25, `display_factor` 0.01. Databento ohlcv-1d closes:
  ESH4 2024-01-31 = 4876.75, ESU4 2024-07-31 = 5587.50. These are UTC-day bars (not regular-
  session closes); they serve as the conservative strike-band reference only.

- **D-016 (2026-11-21) Pilot verification on real records** (engineering/measurement checks on
  development dates 2024-02-01 and 2024-08-01; `outputs/pilot/pilot_audit.json`,
  `outputs/pilot/pilot_support.json`):
  - Client 0.90 `DBNStore.to_df(price_type="float")` returns prices already scaled from 1e-9
    fixed point, with UNDEF mapped to NaN. **Never divide by 1e9 again.** `pretty_px` does not exist
    in this version.
  - OPRA cbbo-1m: all `ts_recv` on exact minute boundaries, first 09:31 and last 15:59 NY for a
    09:30→16:00 request (the end bound is exclusive; the interval ending 16:00 is not needed
    because the last observation is close − 15 min). `ts_event` never after `ts_recv`. Premiums in
    index points (bid 1st–99th pct 0.15–695 / 0.15–753). Zero or NaN bids: 0.29% / 0.001%.
  - Noon (cutoff 12:00) filtered quotes: 121/72 and 242/63 call-put pairs on the bracketing
    expiries. Robust parity fits: zero bid/ask-interval violations, implied effective rates
    5.8–6.9% (inside guardrails, somewhat above the policy rate; kept as a diagnostic). 25-delta
    call and put points lie strictly inside observed strike support on all four expiries, so the
    0.85–1.15 band was sufficient on both pilot days and no expansion was triggered.
  - ES bbo-1s: a record every second during the session (longest in-session gap 2 s; no gap > 5 s);
    all but 3/9 records carry F_LAST; no F_BAD_TS_RECV / F_MAYBE_BAD_BOOK; median spread 1 tick.
    DST handled (−05:00 Feb, −04:00 Aug).
  - **D-014 audit (2024-02-01, 366 session minutes):** per-minute sampled (bbo-1s) vs
    event-level (mbp-1, F_LAST-collapsed) OFI correlation 0.916 (Spearman 0.922), sign agreement
    90.2%, SD ratio 0.63 (sampled smaller: within-second netting). One development day only. This
    describes the approximation; it does not validate it in other conditions.
  - Pilot spend: $0.812 (estimate basis), all in the pilot phase.

- **D-017 (2026-11-21) Method A constants, frozen before any outcome data was used.** Spread floor
  2e-5 (normalized price); soft bid/ask band penalty weight 100. The roughness penalty is
  λ·Σ_j (Δs_j)² / Δx_mid,j (integrated squared curvature of the normalized call price;
  dimensionless). An earlier scaling (·x̄²) was found on the first development day (2025-01-10)
  to make the penalty numerically inert: identical held-out losses for every λ. It was
  corrected before λ selection. λ ∈ {0, 1e-4, 1e-2, 1} is chosen by grouped held-out strike-pair
  price reconstruction on Jan–Jun 2025 12:00 snapshots (`select_lambda.py`), argmin of the mean
  fold loss. No return data are used.
- **D-018 (2026-11-21) Conservative accounting of failed downloads.** A request refused by the
  vendor (4xx) is released. Any other failure after the request was sent (e.g. connection abort
  mid-stream) is counted as consumed at its estimate, because it may have been billed. Legacy
  ledger releases with a non-4xx failure note are counted the same way in `Ledger.summary`.
- **D-019 (2026-11-21) Acquisition approval and execution.** The author approved the 12-month
  purchase within the $31.09 ceiling. The main acquisition ran as detached, resumable processes, with
  per-session logging; cached pieces are never re-bought.

- **D-020 (2026-11-21 04:20 PT) λ selected and frozen: λ = 1.0.** `select_lambda.py` on the 118 of 122
  development sessions whose data were acquired at the time (4 sessions were still failing on
  connection aborts). 1,180 grouped held-out strike-pair folds were scored, 0 skipped. Mean
  held-out loss: λ=0 3.7360, 1e-4 3.7357, 1e-2 3.7262, **1 3.7055**. The selection is at the
  upper edge of the prespecified grid; the grid is not extended (it was fixed in the spec). No
  return data were used.
- **D-021 (2026-11-21) Estimation sample.** Final association and forecasts use valid joint rows
  (flow_ok, state present, all controls, outcome present) from *qualifying* sessions (≥ 75% valid
  joint endpoints for the primary specification). The bootstrap grid includes every scheduled final
  session, including non-qualifying ones as empty positions.

## Blockers

- **B-002 (2026-11-21 ~05:10 PT) Databento account monthly limit reached; 40 of 250 sessions not
  acquired.** The final retry pass returned `402 account_insufficient_funds` (the account's monthly
  usage limit had been reached). Missing: dev 1, **val 19**, final 20
  (Jul 10, Aug 9, Sep 10, Nov 3, Dec 7). With 24 val sessions available, the val gate (≥ 25
  qualifying) cannot pass, so the result could only be labelled a pilot. Free estimate to complete:
  **$2.50 (padded $3.00)**, `outputs/planning/completion_estimate.json`, inside the $8.95 project
  headroom. **Decision taken: no design freeze and no final-period outcome analysis until either the
  account limit was raised (then acquire, measure, freeze, analyze) or the 210-session sample was
  accepted with its gate failure.** Adding pre-planned missing data *after* seeing final results would
  weaken the locked design, so the order matters. Also fixed: a retry of a download that failed
  mid-stream (conservatively counted as spent) was wrongly refused as "already cached"; it is now
  allowed as a charged re-purchase when no file exists.
- **B-002 — RESOLVED 2026-11-21 ~17:24 PT.** The account monthly limit was raised. All 250
  sessions are acquired; ledger consumed $25.48 (conservative basis), headroom $5.61. This was done
  before any design freeze or final-period outcome analysis.
- **D-022 (2026-11-21) Batch API for SPXW definitions.** For 9 sessions, full-day `SPXW.OPT`
  definition streams were repeatedly aborted by the server. SPXW definitions are now requested via
  Databento's batch API (server-side job, same billing, same ledger reservation). The fetch code also
  removes its own stale `.part` files before a retry (a leftover partial caused a FileExistsError).
- **D-023 (2026-11-21) Freeze and final analysis.** Design frozen 2026-11-22 01:43:37 UTC
  (`outputs/main/design_freeze.json`) after all 250 sessions were acquired and measured, and before
  final-period coefficients were computed. All gates passed (dev 122, val 43, final 85 qualifying;
  skew-bin groups 26/17 final sessions). Primary δ_sell = +0.114 (95% basic CI [−0.574, 0.612],
  p = 0.67). Rendering fixes after the run (PDF fonts, figure embedding, table formatting, mojibake
  repair in manuscript.py caused by a PowerShell 5.1 read/write) changed presentation only, never
  numbers. Status label: "main analysis on sampled order flow (deviation D-014)".
- **D-024 (2026-11-21) Post-freeze review findings, documented and not used to change reported results.**
  1. *Half-day expiry instant.* Cboe (verbatim, product page): "Trading in SPXW options will ordinarily
     cease on the day of expiration, 4:00 pm ET, and at 1:00 pm ET for any half day holiday." The frozen
     convention (D-006) used 16:00 for all expiries. A switchable correction (`measure.EARLY_CLOSE_EXPIRY`,
     default off) was evaluated in `expiry_sensitivity.py` on 32 sessions. Across 857 cutoffs with a
     half-day expiry in the bracket, Method A SK30 changes by 0.006 vol points on average (max 0.126).
     96 cutoffs on 2025-06-03, 2025-10-29 and 2025-11-24 would lose their state, because the bracketing
     expiry beyond the half-day date was never acquired. The reported analysis is the frozen one.
  2. *SVI shape checks.* 45.8% of SVI fits pass all finite-grid checks. In a 30-fit diagnostic sample,
     every failure was an upside-wing slope violation (normalized call slope > 0 by at most 0.0022),
     with no convexity failures. Method B is reported as a robustness measurement, not an arbitrage-free
     surface.
  3. *h = 15 endpoint mask.* `features.minute_features` masks y_h when t + h + 5 s passes the close,
     so the last observation of each session is dropped for h = 15 only (1 of 366 rows; secondary
     horizon). Left as is, to preserve the frozen results.
  4. *Ledger lock robustness.* The lock also retries on PermissionError, after one unreproduced test
     failure on Windows/OneDrive. 25 repeated runs of the budget tests passed.
  5. *λ smoothing bias (SYNTHETIC, `lambda_bias.py`).* On a smooth noise-free smile, λ = 1 understates
     SK30 by 0.032 vol points (2.2%) and overstates ATM IV by 0.052. λ ≤ 1e-2 errs by < 0.003. A
     near-constant proportional compression is removed by standardizing z; reported in Appendix C.
  6. Hodrick (1992) DOI verified. The Cboe multiplier (100) and European/PM cash settlement are confirmed
     from Cboe product pages at summary level; the trading-cessation sentence is verbatim.
- **B-001 — RESOLVED 2026-11-21.** Usage-based access was re-enabled on the account, with a
  monthly limit; downloads succeeded afterwards. Original note follows.
- **B-001 (2026-11-21) Databento 402 `account_insufficient_funds`.** The first billable request
  (OPRA SPXW.OPT definitions for 2024-02-01, estimate $0.0258) was refused with "You don't have
  sufficient budget to create the request." No data were delivered and the ledger reservation was
  released. Vendor refusal at request creation should mean no charge, but this has not been
  verified against the portal. **Update:** the account had credits available, so the balance was
  not the cause. Remaining candidate causes, none verified: a monthly usage/budget limit
  set to $0 or already reached, credits restricted to certain uses, or a payment-method
  requirement. Do not retry automatically.
