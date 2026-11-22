"""Per-expiry smile fits (spec §11).

Inputs per expiry: normalized strikes x = K/F and normalized-call quote intervals
[c_lo, c_hi] with midpoint c_mid (puts converted via parity: c = p + 1 - x). Calls and
puts at the same strike are both retained as observations.

Method A (primary): convex QP on fitted normalized call values c_j at the distinct
observed strike knots x_1 < ... < x_m:
  bounds      max(1 - x_j, 0) <= c_j <= 1
  slopes      s_j = (c_{j+1} - c_j)/(x_{j+1} - x_j) in [-1, 0]
  convexity   s_j nondecreasing
  objective   mean_i ((c(x_i) - c_mid_i) / h_i)^2              (dimensionless, averaged)
              + PEN * mean_i (pos(c - c_hi_i) + pos(c_lo_i - c))^2 / h_i^2   (soft band)
              + lambda * sum_j (s_{j+1} - s_j)^2 / dxmid_j     (roughness, D-017)
  h_i = max(half-spread, SPREAD_FLOOR) in normalized units, weights capped via the floor.
Between knots the fitted call function is linear (piecewise-linear interpolation of the
knot values), so the shape constraints hold on the full observed support [x_1, x_m].

Method B (robustness): raw SVI total variance per expiry, fitted in premium space with the
same spread-normalized loss; bounded parameters; no global no-arbitrage claim.

Frozen constants (set after development pilot, before final data; see decisions D-017).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np
from scipy.optimize import least_squares

from .pricing import norm_call

SPREAD_FLOOR = 2e-5      # normalized-price floor on half-spread (~0.1 index pt at F=5000)
BAND_PENALTY = 100.0     # soft penalty weight for leaving the bid/ask interval
LAMBDA_GRID = (0.0, 1e-4, 1e-2, 1.0)


@dataclass
class SmileObs:
    x: np.ndarray        # normalized strike per observation
    c_mid: np.ndarray    # normalized call mid
    c_lo: np.ndarray
    c_hi: np.ndarray
    is_put: np.ndarray   # bool, observation originated from a put

    def subset(self, m):
        return SmileObs(self.x[m], self.c_mid[m], self.c_lo[m], self.c_hi[m], self.is_put[m])


@dataclass
class FitA:
    ok: bool
    knots: np.ndarray = field(default_factory=lambda: np.empty(0))
    c: np.ndarray = field(default_factory=lambda: np.empty(0))
    reason: str = ""
    band_violation: float = 0.0
    n_obs: int = 0

    def __call__(self, x):
        x = np.asarray(x, float)
        if np.any((x < self.knots[0] - 1e-12) | (x > self.knots[-1] + 1e-12)):
            raise ValueError("evaluation outside observed strike support (no extrapolation)")
        return np.interp(x, self.knots, self.c)

    @property
    def support(self):
        return float(self.knots[0]), float(self.knots[-1])


def fit_method_a(obs: SmileObs, lam: float) -> FitA:
    knots = np.unique(obs.x)
    m = len(knots)
    if m < 3:
        return FitA(False, reason=f"only {m} distinct strikes")
    idx = np.searchsorted(knots, obs.x)
    h = np.maximum((obs.c_hi - obs.c_lo) / 2.0, SPREAD_FLOOR)
    c = cp.Variable(m)
    dx = np.diff(knots)
    s = cp.multiply(cp.diff(c), 1.0 / dx)
    fitted = c[idx]
    resid = cp.multiply(fitted - obs.c_mid, 1.0 / h)
    over = cp.multiply(cp.pos(fitted - obs.c_hi) + cp.pos(obs.c_lo - fitted), 1.0 / h)
    obj = cp.sum_squares(resid) / len(h) + BAND_PENALTY * cp.sum_squares(over) / len(h)
    if m >= 3 and lam > 0:
        # integrated squared curvature of the normalized call: sum (Δs_j)^2 / Δx_mid,j
        # (dimensionless; quadratic in adjacent-slope changes; decision D-017)
        dxm = (knots[2:] - knots[:-2]) / 2.0
        obj = obj + lam * cp.sum(cp.multiply(cp.square(cp.diff(s)), 1.0 / dxm))
    cons = [c >= np.maximum(1.0 - knots, 0.0), c <= 1.0, s >= -1.0, s <= 0.0]
    if m >= 3:
        cons.append(cp.diff(s) >= 0)
    prob = cp.Problem(cp.Minimize(obj), cons)
    try:
        prob.solve(solver=cp.CLARABEL)
    except Exception as e:  # noqa: BLE001
        return FitA(False, reason=f"solver error {type(e).__name__}")
    if prob.status not in ("optimal", "optimal_inaccurate") or c.value is None:
        return FitA(False, reason=f"status {prob.status}")
    cv = np.asarray(c.value, float)
    fv = cv[idx]
    viol = float(np.mean(np.maximum(fv - obs.c_hi, 0) + np.maximum(obs.c_lo - fv, 0)))
    return FitA(True, knots, cv, "", viol, len(h))


def check_shape(knots, c, tol=1e-9) -> dict:
    s = np.diff(c) / np.diff(knots)
    return {"bounds": bool(np.all(c >= np.maximum(1 - knots, 0) - tol) and np.all(c <= 1 + tol)),
            "slope_range": bool(np.all(s >= -1 - tol) and np.all(s <= tol)),
            "convex": bool(np.all(np.diff(s) >= -tol))}


# ---------------------------------------------------------------- Method B: raw SVI ----
SVI_BOUNDS = (np.array([-0.5, 1e-4, -0.999, -1.0, 1e-4]), np.array([1.0, 5.0, 0.999, 1.0, 2.0]))


def svi_w(k, p):
    a, b, rho, m, sig = p
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sig**2))


@dataclass
class FitB:
    ok: bool
    params: np.ndarray = field(default_factory=lambda: np.full(5, np.nan))
    support: tuple = (np.nan, np.nan)
    reason: str = ""
    dense_checks: dict = field(default_factory=dict)

    def w(self, k):
        k = np.asarray(k, float)
        lo, hi = np.log(self.support[0]), np.log(self.support[1])
        if np.any((k < lo - 1e-12) | (k > hi + 1e-12)):
            raise ValueError("evaluation outside observed strike support (no extrapolation)")
        return svi_w(k, self.params)

    def __call__(self, x):
        return norm_call(np.asarray(x, float), self.w(np.log(x)))


def svi_init(obs: SmileObs, tau: float, atm_iv_guess: float = 0.18):
    w0 = atm_iv_guess**2 * tau
    return np.array([0.5 * w0, 0.1 * max(np.sqrt(tau), 0.05), -0.5, 0.0, 0.1])


FAST_REFINE_NFEV = 300  # fast mode: ~9x faster; SK30 within ~0.001 vol pts of the frozen fit


def svi_jac(k, p):
    """Analytic Jacobian of raw SVI total variance w.r.t. (a, b, rho, m, sigma)."""
    a, b, rho, m, sig = p
    d = k - m
    r = np.sqrt(d**2 + sig**2)
    return np.column_stack([np.ones_like(k), rho * d + r, b * d, b * (-rho - d / r), b * sig / r])


def fit_method_b(obs: SmileObs, tau: float, warm: np.ndarray | None = None,
                 fast: bool = False) -> FitB:
    """fast=True: same objective, bounds, starts and checks, but analytic Jacobians and
    vectorized initial implied variances (used by the interactive demo). The default
    (fast=False) is the frozen numerics used for every reported result."""
    if len(np.unique(obs.x)) < 5:
        return FitB(False, reason="fewer than 5 distinct strikes")
    k = np.log(obs.x)
    h = np.maximum((obs.c_hi - obs.c_lo) / 2.0, SPREAD_FLOOR)
    scale = h * np.sqrt(len(h))

    def resid(p):
        w = np.maximum(svi_w(k, p), 1e-12)
        return (norm_call(obs.x, w) - obs.c_mid) / h / np.sqrt(len(h))

    def resid_jac(p):
        w_raw = svi_w(k, p)
        w = np.maximum(w_raw, 1e-12)
        sw = np.sqrt(w)
        d1 = (-k + 0.5 * w) / sw
        dcdw = np.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi) / (2 * sw)
        dcdw = np.where(w_raw > 1e-12, dcdw, 0.0)
        return (dcdw / scale)[:, None] * svi_jac(k, p)

    # Deterministic initialization: least-squares SVI on implied total variance of mids
    # (stage 1), then refinement in premium space with the spread-normalized loss (stage 2).
    from .pricing import implied_w, implied_w_vec

    if fast:
        wv = implied_w_vec(obs.c_mid, obs.x)
        ok = np.isfinite(wv)
        wk, kk = list(wv[ok]), list(k[ok])
    else:
        wk, kk = [], []
        for xi, ci in zip(obs.x, obs.c_mid):
            try:
                wk.append(implied_w(float(ci), float(xi)))
                kk.append(math.log(xi))
            except ValueError:
                pass
    starts = [warm] if warm is not None else []
    if len(wk) >= 5 and warm is None:
        wk, kk = np.array(wk), np.array(kk)
        wmin = float(wk.min())
        grid = [np.array([0.5 * wmin, b0, r0, 0.0, s0])
                for b0 in (0.05 * math.sqrt(tau), 0.2 * math.sqrt(tau))
                for r0 in (-0.7, -0.3, 0.0) for s0 in (0.05, 0.2)]
        best_w = None
        for p0 in grid:
            p0 = np.clip(p0, SVI_BOUNDS[0] + 1e-6, SVI_BOUNDS[1] - 1e-6)
            if fast:
                r = least_squares(lambda p: (svi_w(kk, p) - wk) / wmin, p0, bounds=SVI_BOUNDS,
                                  method="trf", max_nfev=300, x_scale="jac",
                                  jac=lambda p: svi_jac(kk, p) / wmin)
            else:
                r = least_squares(lambda p: (svi_w(kk, p) - wk) / wmin, p0, bounds=SVI_BOUNDS,
                                  method="trf", max_nfev=3000)
            if best_w is None or r.cost < best_w.cost:
                best_w = r
        starts.append(best_w.x)
    starts.append(svi_init(obs, tau))
    best = None
    for p0 in starts:
        p0 = np.clip(p0, SVI_BOUNDS[0] + 1e-6, SVI_BOUNDS[1] - 1e-6)
        try:
            r = least_squares(resid, p0, bounds=SVI_BOUNDS, method="trf",
                              max_nfev=300 if warm is not None else (FAST_REFINE_NFEV if fast else 2000),
                              x_scale="jac",
                              ftol=1e-10, xtol=1e-10, jac=resid_jac if fast else "2-point")
        except Exception:  # noqa: BLE001
            continue
        if not np.all(np.isfinite(r.x)):
            continue
        if best is None or r.cost < best.cost:
            best = r
    if best is None or best.status < 0:
        return FitB(False, reason="optimizer failed")
    fit = FitB(True, best.x, (float(obs.x.min()), float(obs.x.max())))
    # finite-domain checks on a dense grid inside support (not a global no-arb proof)
    xs = np.linspace(fit.support[0], fit.support[1], 401)
    w = svi_w(np.log(xs), best.x)
    cv = norm_call(xs, np.maximum(w, 1e-12))
    fit.dense_checks = {"positive_w": bool(np.all(w > 0)), **check_shape(xs, cv, tol=1e-8)}
    if not fit.dense_checks["positive_w"]:
        fit.ok, fit.reason = False, "non-positive total variance in support"
    return fit
