"""Normalized Black pricing in forward-normalized coordinates (spec §9).

x = K / F, c = C / A with A = D * F. Then c = Phi(d1) - x Phi(d2), with
d1 = (-log x + w/2)/sqrt(w), d2 = d1 - sqrt(w), and w the total variance.
Puts: p = c - (1 - x). Black is a quoting coordinate only.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

W_MAX = 25.0  # total-variance search ceiling (vol 500% over 1y); prices above -> invalid


def norm_call(x, w):
    x, w = np.asarray(x, float), np.asarray(w, float)
    sw = np.sqrt(w)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (-np.log(x) + 0.5 * w) / sw
    d2 = d1 - sw
    out = norm.cdf(d1) - x * norm.cdf(d2)
    return np.where(w <= 0, np.maximum(1 - x, 0.0), out)


def norm_put(x, w):
    return norm_call(x, w) - (1.0 - np.asarray(x, float))


def norm_vega(x, w):
    """d c / d sqrt(w)."""
    sw = math.sqrt(w)
    d1 = (-math.log(x) + 0.5 * w) / sw
    return float(norm.pdf(d1))


def call_bounds(x: float) -> tuple[float, float]:
    return max(1.0 - x, 0.0), 1.0


def implied_w(c: float, x: float, *, tol: float = 1e-12) -> float:
    """Bracketed total-variance root for a normalized call. Raises ValueError on
    prices outside the no-arbitrage bounds (never clamps)."""
    if not (np.isfinite(c) and np.isfinite(x) and x > 0):
        raise ValueError("non-finite input")
    lo, hi = call_bounds(x)
    if not (lo < c < hi):
        raise ValueError(f"price {c} outside open bounds ({lo}, {hi}) at x={x}")
    f = lambda w: float(norm_call(x, w)) - c  # noqa: E731
    if f(W_MAX) < 0:
        raise ValueError("price requires total variance above search ceiling")
    w = brentq(f, 1e-16, W_MAX, xtol=tol, rtol=1e-14, maxiter=500)
    return w


def implied_w_vec(c, x, iters: int = 80):
    """Vectorized bisection on log total variance. Returns NaN where the price is outside
    the open no-arbitrage bounds (never clamps). Agrees with implied_w to ~1e-12 relative."""
    c, x = np.broadcast_arrays(np.asarray(c, float), np.asarray(x, float))
    lo_b = np.maximum(1.0 - x, 0.0)
    valid = np.isfinite(c) & np.isfinite(x) & (x > 0) & (c > lo_b) & (c < 1.0)
    lo = np.full(c.shape, np.log(1e-16))
    hi = np.full(c.shape, np.log(W_MAX))
    valid &= norm_call(x, np.exp(hi)) >= c
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        too_low = norm_call(x, np.exp(mid)) < c
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    w = np.exp(0.5 * (lo + hi))
    return np.where(valid, w, np.nan)


def implied_vol(c: float, x: float, tau: float) -> float:
    if tau <= 0:
        raise ValueError("tau must be positive")
    return math.sqrt(implied_w(c, x) / tau)


def forward_delta_call(x: float, w: float) -> float:
    """Undiscounted Black forward delta Phi(d1)."""
    return float(norm.cdf((-math.log(x) + 0.5 * w) / math.sqrt(w)))


def year_fraction_act365(start_utc, end_utc) -> float:
    return (end_utc - start_utc).total_seconds() / (365.0 * 86400.0)
