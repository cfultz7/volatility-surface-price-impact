import numpy as np

from surface_impact.pricing import implied_w, implied_w_vec, norm_call


def test_vectorized_matches_scalar_and_rejects_invalid():
    x = np.linspace(0.8, 1.2, 41)
    w = 0.002 + 0.01 * (x - 1) ** 2
    c = norm_call(x, w)
    wv = implied_w_vec(c, x)
    ws = np.array([implied_w(ci, xi) for ci, xi in zip(c, x)])
    assert np.allclose(wv, ws, rtol=1e-9) and np.allclose(wv, w, rtol=1e-7)
    bad = implied_w_vec(np.array([0.0, 1.0, np.nan]), np.array([1.1, 1.0, 1.0]))
    assert np.all(np.isnan(bad))
