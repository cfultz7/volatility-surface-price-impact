# Databento cost-and-coverage plan (free metadata only, 2026-11-21)

**Status:** complete. Planning, selection and the 2025 acquisition are finished. The planning scans used free metadata calls only; actual spend is in the ledger summary below.

All figures are account-specific `metadata.get_cost` estimates retrieved 2026-11-21. They are
estimates, not invoices. Padded = estimate × 1.20. The ceiling is **$31.09** (D-011). Unit
prices (`list_unit_prices`, historical, $/GB): GLBX mbp-1 1.80, mbp-10 0.50, MBO 1.80;
OPRA cbbo-1m 2.00, definition 5.00.

## Requests per session (as designed)

| Item | Dataset / schema | Symbols (explicit) | Window | Basis |
|---|---|---|---|---|
| ES event data | GLBX.MDP3 / mbp-1 | the active dated contract under the fixed roll (e.g. ESH4) | 08:30 NY → common close | per-session get_cost, all 502 sessions |
| SPXW definitions | OPRA.PILLAR / definition | parent `SPXW.OPT` (needed to verify family, settlement, multiplier, expiry) | full UTC date | per-session get_cost, all 502 sessions |
| SPXW quotes | OPRA.PILLAR / cbbo-1m | explicit OSI symbols: nearest PM expiries bracketing 30 d over the session's 5-min cutoffs (14–90 d), strikes in 0.85–1.15 × prior ES close, calls + puts | 09:30 NY → close | per-session get_cost on the selected list; per-day symbol lists in `selected_symbols/` |
| Expansion (contingent) | OPRA.PILLAR / cbbo-1m | additional strikes for 0.75–1.25 | same | costed alongside |
| ES reference / status | GLBX ohlcv-1d, status | dated contract | daily | < $0.05 total |

Machine-readable: `session_cost_scan.csv` (stage 1), `opra_selected_scan.csv` (stage 2),
`design_cost_summary.json`.

## Totals

Free `get_cost` estimates by period, in USD (unpadded). The SPXW cbbo-1m column covers only the sessions scanned before the scan was stopped (Q1 2024 to mid-2025); the actual 2025 purchase is in the ledger below.

| period | sessions | ES mbp-1 | ES bbo-1s | SPXW definitions | SPXW cbbo-1m selected (sessions scanned) |
|---|---|---|---|---|---|
| 2024 (24-month design, first year) | 252 | 170.08 | 9.25 | 6.93 | 6.65 (252) |
| 2025 (12-month design) | 250 | 210.24 | 9.16 | 7.01 | 5.56 (213) |
| 2024-2025 (24-month design) | 502 | 380.32 | 18.41 | 13.94 | 12.21 (465) |

**Actual 2025 acquisition plus pilot (ledger, metadata-estimate basis):** $25.48 in total. By schema: bbo-1s $9.38, cbbo-1m $7.06, definition $7.89, mbp-1 $0.62, ohlcv-1d $0.00, status $0.00. Failed downloads counted conservatively: $1.31, plus $0.53 from earlier releases.

## Conclusion

Event-level ES `mbp-1` made both prespecified designs unaffordable under the $31.09 ceiling (about $210 for 2025, $380 for 2024-2025). mbp-10 and MBO cost more per session (decision D-012). The author chose ES `bbo-1s` (1-second sampled BBO; deviation D-014). Under that schema the 24-month design's padded total exceeded the ceiling and the 12-month design fit, so the protocol's cost rule selected the 12-month design (2025) before any outcomes were examined. All 250 sessions were acquired; 40 initial failures (connection aborts and the account monthly limit) were recovered before the design freeze.
