"""Spread-aware put-call parity fit per expiry snapshot (spec §9).

C(K) - P(K) = A - B K, with A = D F, B = D. Fit by robust (Huber-IRLS) weighted least
squares on matched-strike midpoints, weights from the parity bid/ask interval width.
Records violations of [C_bid - P_ask, C_ask - P_bid].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ParityFit:
    ok: bool
    reason: str
    A: float = np.nan
    B: float = np.nan
    n_pairs: int = 0
    n_interval_violations: int = 0
    cond: float = np.nan

    @property
    def D(self) -> float:
        return self.B

    @property
    def F(self) -> float:
        return self.A / self.B

    def rate(self, tau: float) -> float:
        return -np.log(self.B) / tau


def fit_parity(K, c_bid, c_ask, p_bid, p_ask, tau: float, *, min_pairs: int = 4,
               rate_bounds=(-0.10, 0.25), spread_floor: float = 1e-4, huber: float = 1.5,
               iters: int = 20) -> ParityFit:
    K = np.asarray(K, float)
    lo = np.asarray(c_bid, float) - np.asarray(p_ask, float)
    hi = np.asarray(c_ask, float) - np.asarray(p_bid, float)
    mid = 0.5 * (lo + hi)
    width = np.maximum(hi - lo, spread_floor * np.maximum(np.abs(mid), 1.0))
    m = np.isfinite(K) & np.isfinite(mid) & np.isfinite(width)
    K, lo, hi, mid, width = K[m], lo[m], hi[m], mid[m], width[m]
    n = len(np.unique(K))
    if n < min_pairs:
        return ParityFit(False, f"only {n} paired strikes (< {min_pairs})", n_pairs=n)
    X = np.column_stack([np.ones_like(K), -K / np.median(K)])  # scaled for conditioning
    w = 1.0 / width**2
    beta = np.zeros(2)
    for _ in range(iters):
        sw = np.sqrt(w)
        beta, *_ = np.linalg.lstsq(X * sw[:, None], mid * sw, rcond=None)
        r = (mid - X @ beta) / width
        rw = np.where(np.abs(r) <= huber, 1.0, huber / np.maximum(np.abs(r), 1e-300))
        w = rw / width**2
    A, B = beta[0], beta[1] / np.median(K)
    cond = float(np.linalg.cond(X * np.sqrt(w)[:, None]))
    fitted = A - B * K
    viol = int(np.sum((fitted < lo - 1e-12) | (fitted > hi + 1e-12)))
    fit = ParityFit(True, "", A, B, n, viol, cond)
    if not (B > 0 and A > 0):
        return ParityFit(False, "non-positive discount or forward", A, B, n, viol, cond)
    r = fit.rate(tau)
    if not (rate_bounds[0] < r < rate_bounds[1]):
        fit.ok, fit.reason = False, f"implied rate {r:.4f} at/outside bounds {rate_bounds}"
    return fit
