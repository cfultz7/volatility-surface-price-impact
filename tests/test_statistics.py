"""SYNTHETIC checks of block resampling, interaction signs and the null-centered test.

DGP (recorded before evaluation; seeds fixed): G sessions x T minutes. Skew state z_d,t is a
persistent AR(1) within day (phi=0.98) plus a day effect; flow q is AR(1) (phi=0.3). Outcome
  y = beta*s + delta*s*z + sigma_t * eps,  sigma_t = 1 + 0.5*|z|  (skew changes variance)
with a common within-day AR(1) error (phi=0.2) to create dependence. Null: delta = 0.
"""

import numpy as np
import pandas as pd
import pytest

from surface_impact.inference import block_indices, bootstrap


def make_panel(G=60, T=120, delta=0.0, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(G):
        zd = rng.normal()
        z = np.empty(T); q = np.empty(T); u = np.empty(T)
        z[0], q[0], u[0] = zd, rng.normal(), rng.normal()
        for t in range(1, T):
            z[t] = 0.98 * z[t - 1] + 0.02 * zd + 0.1 * rng.normal()
            q[t] = 0.3 * q[t - 1] + rng.normal()
            u[t] = 0.2 * u[t - 1] + rng.normal()
        s = np.maximum(-q, 0)
        y = -1.0 * s + delta * s * z + (1 + 0.5 * np.abs(z)) * u + 0.5 * rng.normal()  # + day shock
        rows.append(pd.DataFrame({"date": f"d{d:03d}", "s": s, "z": z, "y": y}))
    return pd.concat(rows, ignore_index=True)


def stat(df):
    X = np.column_stack([np.ones(len(df)), df.s, df.z, df.s * df.z])
    return np.linalg.lstsq(X, df.y.to_numpy(), rcond=None)[0][[3]]


def test_block_indices_keep_sessions_whole_and_contiguous():
    rng = np.random.default_rng(0)
    idx = block_indices(23, 5, rng)
    assert len(idx) == 23
    blocks = idx.reshape(-1)[: (23 // 5) * 5].reshape(-1, 5)
    assert np.all(np.diff(blocks, axis=1) == 1)


def test_bootstrap_preserves_day_rows_and_relabels():
    df = make_panel(G=12, T=10)
    seen = []

    def s2(d):
        seen.append(d.groupby("date").size().unique().tolist())
        return np.array([len(d)])

    bootstrap(s2, df, sorted(df.date.unique()), L=5, B=5, seed=3)
    assert all(v == [10] for v in seen)  # every resampled session kept whole


def test_interaction_sign_recovered():
    df = make_panel(delta=-0.5, seed=5)
    assert stat(df)[0] == pytest.approx(-0.5, abs=0.15)


@pytest.mark.parametrize("delta,expect_reject", [(0.0, False), (-0.6, True)])
def test_null_centered_test(delta, expect_reject):
    df = make_panel(delta=delta, seed=11)
    res = bootstrap(stat, df, sorted(df.date.unique()), L=5, B=199, seed=1729)
    assert res["n_success"] == 199
    assert (res["p_null_centered"][0] < 0.05) == expect_reject


def test_missing_scheduled_sessions_stay_in_grid():
    df = make_panel(G=10, T=5)
    grid = sorted(df.date.unique()) + ["d998", "d999"]  # scheduled but no data
    res = bootstrap(lambda d: np.array([d.date.nunique()]), df, grid, L=5, B=50, seed=2)
    assert res["draws"].max() <= 12 and res["draws"].min() >= 1
