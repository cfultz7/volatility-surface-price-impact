"""30-day constant-maturity smile and 25-delta downside skew (spec §11).

Delta convention: undiscounted Black forward delta, call Phi(d1), put Phi(d1) - 1.
25-delta put <=> call-delta 0.75. SK30 = IV(put 25d) - IV(call 25d) (decimal).
Roots must be bracketed and unique inside the shared observed support. No extrapolation
in strike or maturity: if only one expiry is usable and it is not exactly 30d, no state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq

from .pricing import implied_w, implied_w_vec, norm_call
from .surfaces import FitA, FitB

TARGET_TAU = 30.0 / 365.0
GRID_N = 801


@dataclass
class SkewState:
    ok: bool
    reason: str = ""
    sk30: float = np.nan
    iv_put25: float = np.nan
    iv_call25: float = np.nan
    atm_iv: float = np.nan
    x_put25: float = np.nan
    x_call25: float = np.nan
    support: tuple = (np.nan, np.nan)


def _weights(tau1, tau2):
    if not (tau1 <= TARGET_TAU <= tau2):
        raise ValueError("expiries do not bracket 30 days")
    if math.isclose(tau1, tau2):
        return 1.0, 0.0
    a = (tau2 - TARGET_TAU) / (tau2 - tau1)
    return a, 1.0 - a


class _WFun:
    """Total-variance curve usable on scalars (root refinement) and arrays (grid)."""

    def __init__(self, price_fn):
        self.price_fn = price_fn

    def __call__(self, x):
        if np.ndim(x) == 0:
            return implied_w(float(self.price_fn(x)), float(x))
        w = implied_w_vec(self.price_fn(np.asarray(x)), np.asarray(x))
        if np.any(~np.isfinite(w)):
            raise ValueError("interpolated price outside no-arbitrage bounds")
        return w


def curve_a(f1: FitA, tau1: float, f2: FitA | None, tau2: float | None):
    """Returns (support, function x -> total variance at 30d) for Method A."""
    if f2 is None:
        if not math.isclose(tau1, TARGET_TAU, abs_tol=1e-9):
            raise ValueError("single expiry is not exactly 30 days")
        return f1.support, _WFun(f1)
    a, b = _weights(tau1, tau2)
    lo, hi = max(f1.support[0], f2.support[0]), min(f1.support[1], f2.support[1])
    if not lo < hi:
        raise ValueError("no common strike support")
    return (lo, hi), _WFun(lambda x: a * f1(x) + b * f2(x))


def curve_b(f1: FitB, tau1: float, f2: FitB | None, tau2: float | None):
    """Interpolate SVI total variance at fixed k = log x, linear in maturity, rescaled to tau=30d
    by total-variance-per-maturity interpolation."""
    if f2 is None:
        if not math.isclose(tau1, TARGET_TAU, abs_tol=1e-9):
            raise ValueError("single expiry is not exactly 30 days")
        return f1.support, lambda x: float(f1.w(math.log(x)))
    a, b = _weights(tau1, tau2)
    lo, hi = max(f1.support[0], f2.support[0]), min(f1.support[1], f2.support[1])
    if not lo < hi:
        raise ValueError("no common strike support")
    # linear interpolation of total variance in tau at fixed k
    return (lo, hi), lambda x: float(a * f1.w(math.log(x)) + b * f2.w(math.log(x)))


def skew_from_curve(support, wfun) -> SkewState:
    lo, hi = support
    xs = np.linspace(lo, hi, GRID_N)
    try:
        ws = np.asarray(wfun(xs), float) if isinstance(wfun, _WFun) else np.array([wfun(x) for x in xs])
    except ValueError as e:
        return SkewState(False, f"IV inversion failed on grid: {e}", support=support)
    if np.any(~np.isfinite(ws)) or np.any(ws <= 0):
        return SkewState(False, "non-positive total variance on grid", support=support)
    sw = np.sqrt(ws)
    dcall = 0.5 * (1 + np.vectorize(math.erf)(((-np.log(xs) + ws / 2) / sw) / math.sqrt(2)))
    st = SkewState(False, support=support)
    roots = {}
    for target, name in ((0.25, "call"), (0.75, "put")):
        g = dcall - target
        sc = np.where(np.diff(np.sign(g)) != 0)[0]
        if len(sc) != 1:
            st.reason = f"{name} 25d root: {len(sc)} crossings in support (need exactly 1)"
            return st

        def h(x, target=target):
            w = wfun(x)
            return 0.5 * (1 + math.erf(((-math.log(x) + w / 2) / math.sqrt(w)) / math.sqrt(2))) - target

        x0 = brentq(h, xs[sc[0]], xs[sc[0] + 1], xtol=1e-12)
        roots[name] = x0
    tau = TARGET_TAU
    st.x_call25, st.x_put25 = roots["call"], roots["put"]
    st.iv_call25 = math.sqrt(wfun(roots["call"]) / tau)
    st.iv_put25 = math.sqrt(wfun(roots["put"]) / tau)
    st.sk30 = st.iv_put25 - st.iv_call25
    if lo <= 1.0 <= hi:
        st.atm_iv = math.sqrt(wfun(1.0) / tau)
    st.ok = True
    return st


def norm_call_from_w(x, w):
    return float(norm_call(x, w))
