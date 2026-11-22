"""Pilot measurement check: parity fit + IV + 25-delta root support at the noon cutoff,
per selected expiry, on real pilot quotes. Uses mid prices of filtered quotes (no surface
smoothing yet; Methods A/B are built later). Engineering check only.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .parity import fit_parity
from .pilot_audit import df_of
from .pilot_quotes import PILOT, build
from .pricing import forward_delta_call, implied_w

NY = "America/New_York"


def check(day: str) -> dict:
    reqs, info = build(day)
    cb = pd.concat([df_of(r)[1] for r in reqs if r.schema == "cbbo-1m"]).reset_index()
    noon = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
    snap = cb[(cb.ts_recv <= noon) & (cb.ts_recv > noon - pd.Timedelta(seconds=120))]
    snap = snap.sort_values("ts_recv").groupby("symbol").tail(1).copy()
    mid = (snap.bid_px_00 + snap.ask_px_00) / 2
    ok = ((snap.bid_px_00 > 0) & (snap.ask_px_00 >= snap.bid_px_00) & (snap.bid_sz_00 > 0)
          & (snap.ask_sz_00 > 0) & (((snap.ask_px_00 - snap.bid_px_00) / mid) <= 0.5))
    snap = snap[ok]
    snap["exp"] = pd.to_datetime(snap.symbol.str[6:12], format="%y%m%d")
    snap["right"] = snap.symbol.str[12]
    snap["K"] = snap.symbol.str[13:].astype(int) / 1000
    out = {}
    for exp, g in snap.groupby("exp"):
        expiry = pd.Timestamp(exp.date().isoformat() + " 16:00", tz=NY).tz_convert("UTC")  # D-006
        tau = (expiry - noon).total_seconds() / (365 * 86400)
        c = g[g.right == "C"].set_index("K")
        p = g[g.right == "P"].set_index("K")
        Ks = sorted(set(c.index) & set(p.index))
        fit = fit_parity(np.array(Ks), c.loc[Ks, "bid_px_00"], c.loc[Ks, "ask_px_00"],
                         p.loc[Ks, "bid_px_00"], p.loc[Ks, "ask_px_00"], tau)
        rec = {"tau_days": round(tau * 365, 3), "pairs": len(Ks), "parity_ok": fit.ok,
               "reason": fit.reason, "F": round(fit.F, 3), "D": round(fit.D, 6),
               "implied_rate": round(fit.rate(tau), 5), "interval_violations": fit.n_interval_violations}
        if fit.ok:
            # OTM convention: puts below F, calls above F, converted to normalized calls via parity
            pts = []
            for K in sorted(set(c.index) | set(p.index)):
                x = K / fit.F
                if K < fit.F and K in p.index:
                    cn = ((p.loc[K, "bid_px_00"] + p.loc[K, "ask_px_00"]) / 2) / fit.A + (1 - x)
                elif K >= fit.F and K in c.index:
                    cn = ((c.loc[K, "bid_px_00"] + c.loc[K, "ask_px_00"]) / 2) / fit.A
                else:
                    continue
                try:
                    w = implied_w(cn, x)
                    pts.append((K, x, np.sqrt(w / tau), forward_delta_call(x, w)))
                except ValueError:
                    pass
            d = pd.DataFrame(pts, columns=["K", "x", "iv", "dcall"])
            rec["iv_points"] = len(d)
            rec["iv_atm_nearest"] = round(float(d.iloc[(d.x - 1).abs().argmin()].iv), 4)
            rec["strike_support"] = [float(d.K.min()), float(d.K.max())]
            rec["min_call_delta"] = round(float(d.dcall.min()), 4)
            rec["max_call_delta"] = round(float(d.dcall.max()), 4)
            rec["call25_inside_support"] = bool(d.dcall.min() < 0.25 < d.dcall.max())
            rec["put25_inside_support"] = bool(d.dcall.min() < 0.75 < d.dcall.max())
            # raw (unsmoothed) 25-delta IVs by linear interpolation in delta, diagnostic only
            dd = d.sort_values("dcall")
            rec["raw_iv_call25"] = round(float(np.interp(0.25, dd.dcall, dd.iv)), 4)
            rec["raw_iv_put25"] = round(float(np.interp(0.75, dd.dcall, dd.iv)), 4)
        out[exp.date().isoformat()] = rec
    return out


if __name__ == "__main__":
    res = {d: check(d) for d in PILOT}
    Path("outputs/pilot/pilot_support.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=1))
