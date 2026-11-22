"""SYNTHETIC verification of Method A/B fits and 30d 25-delta skew (spec §19)."""

import math

import numpy as np
import pytest

from surface_impact.pricing import norm_call
from surface_impact.skew import TARGET_TAU, curve_a, curve_b, skew_from_curve
from surface_impact.surfaces import SmileObs, check_shape, fit_method_a, fit_method_b


def smile_iv(x, atm=0.15, slope=-0.25, curv=0.4):
    k = np.log(x)
    return atm + slope * k + curv * k**2  # asymmetric: higher IV for low strikes


def make_obs(tau, xs, hs=2e-4, rng=None):
    iv = smile_iv(xs)
    c = norm_call(xs, iv**2 * tau)
    rng = rng or np.random.default_rng(1729)
    noise = rng.uniform(-0.5, 0.5, len(xs)) * hs
    mid = c + noise
    return SmileObs(xs, mid, mid - hs, mid + hs, np.zeros(len(xs), bool)), iv


def truth_skew(tau=TARGET_TAU):
    xs = np.linspace(0.8, 1.15, 4001)
    w = smile_iv(xs) ** 2 * tau
    d = 0.5 * (1 + np.vectorize(math.erf)(((-np.log(xs) + w / 2) / np.sqrt(w)) / math.sqrt(2)))
    xc, xp = np.interp(0.25, d[::-1], xs[::-1]), np.interp(0.75, d[::-1], xs[::-1])
    return smile_iv(xp) - smile_iv(xc)


@pytest.fixture(scope="module")
def fits():
    xs = np.arange(0.85, 1.1501, 0.005)
    t1, t2 = 28 / 365, 33 / 365
    o1, _ = make_obs(t1, xs)
    o2, _ = make_obs(t2, xs, rng=np.random.default_rng(7))
    return t1, t2, o1, o2


def test_method_a_shape_and_fit(fits):
    t1, t2, o1, o2 = fits
    for lam in (0.0, 1e-2):
        f = fit_method_a(o1, lam)
        assert f.ok
        assert all(check_shape(f.knots, f.c).values())
        assert f.band_violation < 1e-6
    with pytest.raises(ValueError):
        f(np.array([0.5]))  # no extrapolation


def test_skew_sign_and_accuracy_method_a(fits):
    t1, t2, o1, o2 = fits
    fa1, fa2 = fit_method_a(o1, 1e-4), fit_method_a(o2, 1e-4)
    st = skew_from_curve(*curve_a(fa1, t1, fa2, t2))
    assert st.ok and st.sk30 > 0  # downside puts richer
    assert st.sk30 == pytest.approx(truth_skew(), abs=0.004)
    assert st.atm_iv == pytest.approx(0.15, abs=0.003)


def test_skew_method_b(fits):
    t1, t2, o1, o2 = fits
    fb1, fb2 = fit_method_b(o1, t1), fit_method_b(o2, t2)
    assert fb1.ok and fb2.ok and fb1.dense_checks["positive_w"]
    st = skew_from_curve(*curve_b(fb1, t1, fb2, t2))
    assert st.ok and st.sk30 == pytest.approx(truth_skew(), abs=0.004)


def test_mirror_smile_flips_sign():
    xs = np.arange(0.85, 1.1501, 0.005)
    tau = TARGET_TAU
    iv = 0.15 + 0.25 * np.log(xs) + 0.4 * np.log(xs) ** 2  # upside richer
    c = norm_call(xs, iv**2 * tau)
    o = SmileObs(xs, c, c - 2e-4, c + 2e-4, np.zeros(len(xs), bool))
    f = fit_method_a(o, 1e-4)
    st = skew_from_curve(*curve_a(f, tau, None, None))
    assert st.ok and st.sk30 < 0


def test_no_maturity_extrapolation(fits):
    t1, t2, o1, o2 = fits
    fa1 = fit_method_a(o1, 1e-4)
    with pytest.raises(ValueError):
        curve_a(fa1, t1, None, None)  # single non-30d expiry
    with pytest.raises(ValueError):
        curve_a(fa1, 31 / 365, fit_method_a(o2, 1e-4), 33 / 365)  # does not bracket


def test_unsupported_root_rejected():
    tau = TARGET_TAU
    xs = np.arange(0.99, 1.0101, 0.0025)  # too narrow: 25d roots outside support
    c = norm_call(xs, smile_iv(xs) ** 2 * tau)
    o = SmileObs(xs, c, c - 2e-4, c + 2e-4, np.zeros(len(xs), bool))
    st = skew_from_curve(*curve_a(fit_method_a(o, 1e-4), tau, None, None))
    assert not st.ok and "crossings" in st.reason


def test_svi_bounds_not_claimed_as_no_arbitrage(fits):
    t1, _, o1, _ = fits
    fb = fit_method_b(o1, t1)
    # dense checks are finite-domain diagnostics only; reported, not a global guarantee
    assert set(fb.dense_checks) == {"positive_w", "bounds", "slope_range", "convex"}
