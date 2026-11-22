"""Moving-block bootstrap over trading-session positions (spec §15).

The final calendar grid lists every scheduled eligible session in order, including
sessions that later fail quality gates (they stay as empty positions). A replicate draws
ceil(G / L) start positions uniformly from the G - L + 1 possible starts and concatenates
blocks of L consecutive session positions, truncating to G. Each drawn session keeps all of
its own observations (labels were built inside the original session; block boundaries create
no new returns). Repeated copies are relabelled for clustering.

Two-sided null-centered test for a coefficient theta:
  p = (1 + #{ |theta*_b - theta_hat| >= |theta_hat| }) / (B + 1)
95% CI: basic percentile interval 2*theta_hat - q(0.975), 2*theta_hat - q(0.025) and the
plain percentile interval (both reported; the basic interval is the declared primary).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


def block_indices(G: int, L: int, rng: np.random.Generator) -> np.ndarray:
    if G < L:
        raise ValueError("fewer sessions than block length")
    nb = int(np.ceil(G / L))
    starts = rng.integers(0, G - L + 1, size=nb)
    idx = (starts[:, None] + np.arange(L)[None, :]).ravel()[:G]
    return idx


def bootstrap(stat: Callable[[pd.DataFrame], np.ndarray], df: pd.DataFrame, grid: list[str],
              L: int = 5, B: int = 1999, seed: int = 1729) -> dict:
    """stat(df) -> vector of statistics. df must have column 'date'. grid = ordered list of
    all scheduled sessions in the evaluation period (including sessions with no rows)."""
    rng = np.random.default_rng(seed)
    by_day = {d: g for d, g in df.groupby("date")}
    theta = np.asarray(stat(df), float)
    draws, failed = [], 0
    for b in range(B):
        idx = block_indices(len(grid), L, rng)
        parts = []
        for k, i in enumerate(idx):
            d = grid[i]
            if d in by_day:
                p = by_day[d].copy()
                p["date"] = f"{d}#{k}"  # relabel repeated copies
                parts.append(p)
        if not parts:
            failed += 1
            continue
        try:
            draws.append(np.asarray(stat(pd.concat(parts, ignore_index=True)), float))
        except Exception:  # noqa: BLE001
            failed += 1
    D = np.array(draws)
    ok = np.all(np.isfinite(D), axis=1)
    D = D[ok]
    failed += int((~ok).sum())
    lo_q, hi_q = np.percentile(D, [2.5, 97.5], axis=0)
    p = (1 + (np.abs(D - theta) >= np.abs(theta)).sum(axis=0)) / (len(D) + 1)
    return {"theta": theta, "draws": D, "n_success": len(D), "n_failed": failed,
            "ci_basic": (2 * theta - hi_q, 2 * theta - lo_q), "ci_percentile": (lo_q, hi_q),
            "p_null_centered": p, "se_boot": D.std(axis=0, ddof=1),
            "n_blocks_possible": len(grid) - L + 1}
