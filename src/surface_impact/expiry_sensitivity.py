"""Post-freeze measurement sensitivity (D-024): half-day expiry instant.

Recomputes Method A primary states with EARLY_CLOSE_EXPIRY=True for every 2025 session whose
bracketing expiries can include an early-close date (2025-07-03, 2025-11-28, 2025-12-24), and
compares SK30 / ATM IV with the frozen states. Reported as a measurement check; the frozen
primary analysis is not re-run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import measure
from .calendars import eligible_sessions


def affected_days() -> list[str]:
    early = [s.day for s in eligible_sessions("2025-01-01", "2026-03-31") if s.early_close]
    out = set()
    for s in eligible_sessions("2025-01-01", "2025-12-31"):
        for e in early:
            dd = (e - s.day).days
            if 14 <= dd <= 90 and abs(dd - 30) <= 7:  # could sit in a 30-day bracket
                out.add(s.day.isoformat())
    return sorted(out)


def main():
    days = affected_days()
    frozen = pd.read_parquet("outputs/main/skew_states.parquet",
                             columns=["date", "cutoff", "variant", "ok", "sk30", "atm_iv", "exp_lo", "exp_hi"])
    frozen = frozen[(frozen.variant == "A_primary") & frozen.date.isin(days)]
    measure.EARLY_CLOSE_EXPIRY = True
    try:
        rows = []
        for d in days:
            rows += [r for r in measure.measure_day(d, 1.0, methods=("A",))]
    finally:
        measure.EARLY_CLOSE_EXPIRY = False
    new = pd.DataFrame(rows)[["date", "cutoff", "ok", "sk30", "atm_iv", "exp_lo", "exp_hi"]]
    m = frozen.merge(new, on=["date", "cutoff"], suffixes=("_frozen", "_corr"))
    early_codes = {e.strftime("%y%m%d") for e in [s.day for s in eligible_sessions("2025-01-01", "2026-03-31") if s.early_close]}
    touched = m[m.exp_lo_frozen.isin(early_codes) | m.exp_hi_frozen.isin(early_codes)]
    d_sk = (touched.sk30_corr - touched.sk30_frozen) * 100
    d_iv = (touched.atm_iv_corr - touched.atm_iv_frozen) * 100
    res = {"sessions_checked": len(days), "cutoffs_compared": int(len(m)),
           "cutoffs_with_early_close_expiry": int(len(touched)),
           "ok_changed": int((m.ok_frozen != m.ok_corr).sum()),
           "max_abs_dSK30_volpts": float(np.nanmax(np.abs(d_sk))) if len(touched) else 0.0,
           "mean_abs_dSK30_volpts": float(np.nanmean(np.abs(d_sk))) if len(touched) else 0.0,
           "max_abs_dATMIV_volpts": float(np.nanmax(np.abs(d_iv))) if len(touched) else 0.0,
           "early_close_expiry_codes": sorted(early_codes)}
    Path("outputs/main/expiry_sensitivity.json").write_text(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
