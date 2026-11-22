"""SYNTHETIC verification of pricing and parity (no market data)."""

import numpy as np
import pytest

from surface_impact.parity import fit_parity
from surface_impact.pricing import forward_delta_call, implied_vol, implied_w, norm_call, norm_put


def black_cash(F, K, D, sig, tau, right):
    w = sig**2 * tau
    c = D * F * norm_call(K / F, w)
    return c if right == "C" else c - D * (F - K)


def test_iv_roundtrip_and_bounds():
    for x in (0.7, 0.9, 1.0, 1.1, 1.3):
        for w in (1e-4, 0.01, 0.2):
            c = float(norm_call(x, w))
            if c - max(1 - x, 0) < 1e-13:
                continue  # deep wing with no time value: not invertible at double precision
            assert implied_w(c, x) == pytest.approx(w, rel=1e-7)
    with pytest.raises(ValueError):
        implied_w(0.05, 0.9)  # below intrinsic 0.1
    with pytest.raises(ValueError):
        implied_w(1.0, 1.0)  # at upper bound
    with pytest.raises(ValueError):
        implied_w(np.nan, 1.0)


def test_put_call_identity():
    x, w = np.array([0.8, 1.0, 1.2]), 0.04
    assert np.allclose(norm_call(x, w) - norm_put(x, w), 1 - x)


def test_parity_recovers_forward_discount_and_unit_invariance():
    rng = np.random.default_rng(1729)
    F, D, tau, sig = 5000.0, np.exp(-0.05 * 30 / 365), 30 / 365, 0.16
    K = np.arange(4500, 5501, 25.0)
    C = black_cash(F, K, D, sig, tau, "C")
    P = black_cash(F, K, D, sig, tau, "P")
    hs = 0.05 + 0.01 * rng.random(len(K))  # half-spreads in index points
    fit = fit_parity(K, C - hs, C + hs, P - hs, P + hs, tau)
    assert fit.ok and fit.F == pytest.approx(F, rel=1e-6) and fit.D == pytest.approx(D, rel=1e-6)
    assert fit.n_interval_violations == 0
    # Multiply all premiums by the $100 multiplier (full-contract dollars): F, D unchanged,
    # A scales by 100 so normalized prices C/A are unchanged.
    fit100 = fit_parity(K, 100 * (C - hs), 100 * (C + hs), 100 * (P - hs), 100 * (P + hs), tau)
    assert fit100.F == pytest.approx(fit.F, rel=1e-9)
    assert fit100.A == pytest.approx(100 * fit.A, rel=1e-9)
    # but B also scales -> D would be wrong if premiums were dollars and strikes index points.
    # This documents why units must match: dollar premiums with index-point strikes are rejected.
    assert not fit100.ok or fit100.D != pytest.approx(D, rel=1e-3)
    x = K / fit.F
    c_norm = C / fit.A
    iv = [implied_vol(ci, xi, tau) for ci, xi in zip(c_norm, x)]
    assert np.allclose(iv, sig, atol=1e-6)


def test_parity_rejects_too_few_pairs_and_bad_rates():
    K = np.array([4900.0, 5000, 5100])
    f = fit_parity(K, K * 0 + 1, K * 0 + 2, K * 0 + 1, K * 0 + 2, 0.1)
    assert not f.ok and "paired strikes" in f.reason
    F, tau = 5000.0, 30 / 365
    K = np.arange(4800, 5201, 50.0)
    D = np.exp(-0.40 * tau)  # 40% rate: outside [-10%, 25%]
    C, P = black_cash(F, K, D, 0.2, tau, "C"), black_cash(F, K, D, 0.2, tau, "P")
    f = fit_parity(K, C - 0.05, C + 0.05, P - 0.05, P + 0.05, tau)
    assert not f.ok and "rate" in f.reason


def test_forward_delta():
    assert forward_delta_call(1.0, 0.04) == pytest.approx(0.5398, abs=1e-4)
