"""The fast SVI path (analytic Jacobians) matches numerical derivatives and the frozen path."""

import numpy as np
import pytest

from surface_impact.pricing import norm_call
from surface_impact.surfaces import SmileObs, fit_method_b, svi_jac, svi_w


def test_svi_jacobian_matches_finite_differences():
    k = np.linspace(-0.15, 0.12, 50)
    p = np.array([0.002, 0.03, -0.6, 0.01, 0.08])
    J = svi_jac(k, p)
    eps = 1e-7
    for j in range(5):
        dp = np.zeros(5)
        dp[j] = eps
        num = (svi_w(k, p + dp) - svi_w(k, p - dp)) / (2 * eps)
        assert np.allclose(J[:, j], num, rtol=1e-5, atol=1e-9)


def test_fast_fit_matches_frozen_fit():
    tau = 30 / 365
    xs = np.arange(0.86, 1.14, 0.004)
    iv = 0.15 - 0.25 * np.log(xs) + 0.4 * np.log(xs) ** 2
    c = norm_call(xs, iv**2 * tau)
    rng = np.random.default_rng(3)
    mid = c + rng.uniform(-1e-4, 1e-4, len(xs))
    o = SmileObs(xs, mid, mid - 2e-4, mid + 2e-4, np.zeros(len(xs), bool))
    slow, fast = fit_method_b(o, tau), fit_method_b(o, tau, fast=True)
    assert slow.ok and fast.ok
    g = np.log(np.linspace(xs.min(), xs.max(), 200))
    assert np.max(np.abs(np.sqrt(fast.w(g) / tau) - np.sqrt(slow.w(g) / tau))) == pytest.approx(0, abs=5e-4)
