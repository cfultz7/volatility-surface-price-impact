"""SYNTHETIC size/power experiment for the null-centered moving-block bootstrap test.

DGP (fixed before evaluation; see tests/test_statistics.py docstring): G sessions x T minutes,
persistent within-day skew state (AR 0.98 around a day effect), AR(0.3) flow, AR(0.2) errors
whose scale rises with |z| (skew changes conditional variance), plus a day shock.
Null: delta = 0. Alternative: delta = -0.25 (known one-step s*z term).
Each replication: block bootstrap L=5, B=199. Reports rejection rates at 5% with binomial
Monte Carlo standard errors. Labelled SYNTHETIC; never mixed with empirical tables.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .inference import bootstrap


def _panel(seed, delta, G=85, T=60):
    import pandas as pd

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
        y = -1.0 * s + delta * s * z + (1 + 0.5 * np.abs(z)) * u + 0.5 * rng.normal()
        rows.append(pd.DataFrame({"date": f"d{d:03d}", "s": s, "z": z, "y": y}))
    return pd.concat(rows, ignore_index=True)


def _stat(df):
    X = np.column_stack([np.ones(len(df)), df.s, df.z, df.s * df.z])
    return np.linalg.lstsq(X, df.y.to_numpy(), rcond=None)[0][[3]]


def _one(args):
    seed, delta = args
    df = _panel(seed, delta)
    r = bootstrap(_stat, df, sorted(df.date.unique()), L=5, B=199, seed=seed + 7)
    return float(r["p_null_centered"][0]), float(r["theta"][0])


def run(R=200, workers=10):
    out = {}
    for label, delta in (("null", 0.0), ("alt_-0.25", -0.25)):
        with ProcessPoolExecutor(workers) as ex:
            res = list(ex.map(_one, [(10_000 + i, delta) for i in range(R)]))
        p = np.array([r[0] for r in res])
        rej = float((p < 0.05).mean())
        out[label] = {"delta": delta, "replications": R, "reject_rate_5pct": rej,
                      "mc_se": float(np.sqrt(rej * (1 - rej) / R)),
                      "mean_estimate": float(np.mean([r[1] for r in res]))}
    d = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "simulation_size_power.json").write_text(json.dumps({"label": "SYNTHETIC", **out}, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(run(int(sys.argv[1]) if len(sys.argv) > 1 else 200), indent=2))
