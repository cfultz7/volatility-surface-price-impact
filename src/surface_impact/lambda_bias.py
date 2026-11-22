"""SYNTHETIC check of Method A smoothing bias by roughness λ on a smooth asymmetric smile
(single exact 30-day expiry, strikes every 0.005 in x, ±2e-4 normalized bid/ask band)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .pricing import norm_call
from .skew import TARGET_TAU, curve_a, skew_from_curve
from .surfaces import LAMBDA_GRID, SmileObs, fit_method_a


def main():
    xs = np.arange(0.85, 1.1501, 0.005)
    iv = lambda x: 0.15 - 0.25 * np.log(x) + 0.4 * np.log(x) ** 2  # noqa: E731
    c = norm_call(xs, iv(xs) ** 2 * TARGET_TAU)
    g = np.linspace(0.8, 1.15, 40001)
    w = iv(g) ** 2 * TARGET_TAU
    d = 0.5 * (1 + np.vectorize(math.erf)(((-np.log(g) + w / 2) / np.sqrt(w)) / math.sqrt(2)))
    xc, xp = np.interp(0.25, d[::-1], g[::-1]), np.interp(0.75, d[::-1], g[::-1])
    truth = float((iv(xp) - iv(xc)) * 100)
    out = {"label": "SYNTHETIC", "true_sk30_volpts": truth, "true_atm_iv_pct": 15.0, "by_lambda": {}}
    for lam in LAMBDA_GRID:
        st = skew_from_curve(*curve_a(fit_method_a(SmileObs(xs, c, c - 2e-4, c + 2e-4, np.zeros(len(xs), bool)), lam),
                                      TARGET_TAU, None, None))
        out["by_lambda"][str(lam)] = {"sk30_err_volpts": st.sk30 * 100 - truth,
                                      "sk30_rel_err": (st.sk30 * 100 - truth) / truth,
                                      "atm_err_volpts": (st.atm_iv - 0.15) * 100}
    Path("outputs/main/synthetic_lambda_bias.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
