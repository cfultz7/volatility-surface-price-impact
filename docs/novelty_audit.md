# Novelty audit (protocol v0.1, 2026-11-21, pre-data)

## Status

Preliminary. Based on bibliographic and abstract-level checks (see `literature_review.md`);
full-text reading of the highest-overlap items is outstanding. This audit will be revised
before the manuscript draft and **must not** be revised in response to final-period results.

## What is already established (do not claim)

- Best-quote order-flow imbalance explains contemporaneous short-interval price changes, with
  slope inversely related to depth (Cont, Kukanov & Stoikov 2014). Multi-level and cross-asset
  extensions exist (Cont, Cucuringu & Zhang 2023).
- The S&P 500 E-mini is the dominant venue for index price discovery (Hasbrouck 2003).
- ES price–flow dynamics are state-dependent around macroeconomic announcements in one-second
  BBO data (Takahashi 2025, preprint).
- Option-implied crash risk co-moves with market illiquidity at daily frequency (Christoffersen,
  Feunou, Jeon & Ornthanalai 2021).
- Option-implied quantities, including skewness, are sensitive to how the surface is built
  (Ulrich & Walther 2020).
- Option markets contribute to price discovery and option-induced order flow carries information
  (Chakravarty, Gulen & Mayhew 2004; Hu 2014); option prices also respond to underlying order flow
  (Collin-Dufresne, Fos & Muravyev 2021).

## Gap found so far

The searches did not turn up a study that conditions the **forward** (post-flow-interval)
response of ES midquotes to best-quote OFI on a **strictly lagged, constant-maturity 30-day
25-delta SPXW skew**, with volatility, depth and spread interactions as competing explanations,
and with the skew measured under two different surface constructions. This absence comes from a
limited search (two extended web searches, one 15-result peer-reviewed index search, plus
reference-lead checks). It is **not** evidence that nobody has done this work.

## Defensible contribution statement (modest)

> We test whether a strictly lagged, consistently defined 30-day downside-skew state from SPXW
> option quotes is associated with differences in the short-horizon ES midquote response to
> observed best-quote selling pressure, beyond volatility, depth, spread and recent returns. The
> skew is measured under two surface constructions, the evidence is limited to a stated
> contiguous historical sample, and inference accounts for dependence at the trading-day level.

The candidate contributions are measurement and design contributions: the combination of a
lagged constant-maturity skew state, separation of the flow window from future returns,
volatility and depth interaction controls, and robustness to the surface method. They are not a
claim of a new mechanism.

## Overlap risks to resolve with full-text reading

1. **Takahashi (2025)**: check whether any specification conditions on VIX or option-implied
   measures. If it does, we frame our study as an extension to a skew (rather than level) state
   with a surface-measurement robustness check.
2. **JFE (2007) state-dependence lead** ("Order flow, dealer profitability, and price formation"):
   identify the authors and check whether option-implied states are used.
3. **Christoffersen et al. (2021)**: confirm the frequency and whether any intraday order-flow
   analysis appears.
4. Look for practitioner-adjacent academic work on "skew and futures liquidity" or "gamma and
   intraday reversal". Any such dealer-gamma papers are **contrasts** only: this study does not
   build gamma exposure from open interest.

## Framing rule if close prior work turns up

If a paper already estimates this interaction, the project becomes a specified
replication/extension: different sample period, event-level MBP-1 OFI, two-method surface
robustness, and day-block inference. The main question and primary coefficient stay the same.
