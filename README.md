# Volatility Surface Skew & State-Dependent Price Impact

[![ci](https://github.com/cfultz7/volatility-surface-price-impact/actions/workflows/ci.yml/badge.svg)](https://github.com/cfultz7/volatility-surface-price-impact/actions/workflows/ci.yml)
[![Streamlit demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://volatility-skew-price-impact.streamlit.app)

**Question.** Does the 30-day downside skew of the S&P 500 options surface (SPXW), measured strictly
before an interval of order-flow pressure in E-mini S&P 500 futures (ES), go with a different
subsequent ES price response, after accounting for volatility, spread, depth and recent returns?

**Status.** Working paper v0.2 by Chantry Fultz (not peer reviewed). Status: *main analysis on sampled
order flow (deviation D-014)*. All prespecified data-quality gates passed (250 qualifying sessions).

**Result (null).** In the locked final period (Sep–Dec 2025, 85 sessions, 30,730 minutes), the
sell-pressure × skew coefficient at the 5-minute horizon is **+0.114 bps per SD of flow per SD of skew**,
with a 95% moving-block bootstrap interval of [−0.574, 0.612] and null-centered p = 0.67. The interval
includes zero, so the data do not show that downside skew changes the ES response to selling pressure.
- **Horizons:** no secondary horizon interaction survives Holm adjustment.
- **Robustness:** the null holds under SVI, all quote filters, delayed endpoints and roll exclusion.
- **Forecasting:** adding skew makes frozen out-of-sample forecasts slightly worse (1 − SSE ratio =
  −0.0034, CI [−0.0060, −0.0009]).

Estimates are conditional, observational associations. They are not causal price impact or a trading
strategy. Read the full paper: [`paper/paper.pdf`](paper/paper.pdf) (17 pages, with appendix).

**In plain English.** When S&P 500 options make downside protection relatively expensive, do S&P
futures prices behave differently in the minutes after a burst of selling pressure? I rebuilt a 30-day
volatility smile from option quotes every five minutes, measured how much more downside puts cost than
comparable calls, measured buying and selling pressure in E-mini futures, and tested whether the next
five minutes of futures returns depend on the skew measured *before* the pressure. The answer, in a
final period examined only after the model was frozen, is no detectable difference. This is not a
trading strategy (midquote returns ignore costs), and it does not identify who traded or why.

**Markets and design.**
- Data: SPXW consolidated quotes (OPRA `cbbo-1m`) and the dated ES contract (CME `bbo-1s`).
- Sample: every regular session of 2025, split into development Jan–Jun, validation Jul–Aug and final
  Sep–Dec.
- Locked design: frozen before final-period outcomes were read.
- Primary horizon: 5 minutes.
- Inference: moving-block bootstrap over 5-session blocks.

> **Deviation D-014.** ES order flow is a *1-second sampled* best-quote imbalance, not event-level OFI.
> Event-level data cost 7–12× the project budget. A one-day audit found 0.92 per-minute correlation
> with event-level OFI. See `docs/decisions.md`.

## Interactive demo
**Live:** https://volatility-skew-price-impact.streamlit.app

`app/dashboard.py` is a Streamlit app with six pages:

- **Overview:** the headline result.
- **Method demo (synthetic):** set a smile (ATM vol, skew, curvature) and the quote quality (spread,
  noise, strike spacing, λ), and watch the project's parity fit, Method A QP and SVI recover the
  30-day 25-delta skew against the known truth.
- **Data & Measurement, Surface Explorer, Conditional Responses, Robustness & Prediction:** the saved
  empirical results.

The app downloads nothing, and the demo uses no licensed data.

## Repository map
| Path | Contents |
|---|---|
| `src/surface_impact/` | data access & budget ledger, calendars/rolls, contract selection, pricing, parity, surfaces (Method A QP, Method B SVI), skew, OFI, alignment, features, regressions, bootstrap inference, analysis driver, figures, manuscript |
| `tests/` | offline synthetic tests (no billable calls) |
| `configs/` | protocol, budget, planning reference |
| `docs/` | protocol, literature review, novelty audit, data dictionary, decisions |
| `paper/` | the paper (PDF), its Markdown text and appendix, references |
| `app/dashboard.py` | Streamlit dashboard over saved artifacts |
| `outputs/main/` | derived tables, figures, freeze manifest, results |
| `data/` | licensed raw data and ledger (**not distributed**; gitignored) |

## Reproducibility
- **Tests:** 52 offline tests (synthetic data, plus checks that the paper's numbers match the committed
  results) pass on Ubuntu, Windows and macOS in GitHub Actions. They make no billable calls.
- **Data costs:** every billable Databento request was estimated with the free metadata API, padded by
  20%, and checked against a fixed budget under a file lock. Requests are cached, so reruns never pay
  twice. The study used about $25 of data.
- **Design freeze:** the model, filters and λ were frozen and hashed before final-period outcomes were
  read (`outputs/main/design_freeze.json`). All deviations are logged in `docs/decisions.md`.

## Data rights
Raw Databento/OPRA/CME data are licensed and are **not** included. Only code, synthetic fixtures and
derived aggregate tables/figures are intended for publication. Check your own data agreement before
redistributing anything derived from licensed feeds.

## Limitations
- Order flow is sampled at 1 second (D-014).
- One year and one market pair.
- Skew is partly a volatility regime.
- Cross-market timestamps come from different locations.
- Quote filters may select on stress.
- SVI has no global no-arbitrage guarantee.
- Midquote returns are not tradable P&L.
