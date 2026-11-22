"""Aggregate free-metadata scans into per-design cost summaries (machine-readable JSON).

Designs (spec §5, §13):
  24m: 2024-01-01..2025-12-31  dev Jan-Sep 2024, val Oct-Dec 2024, final 2025
  12m: 2025-01-01..2025-12-31  dev Jan-Jun 2025, val Jul-Aug 2025, final Sep-Dec 2025
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

DESIGNS = {
    "24m": {"start": "2024-01-01", "end": "2025-12-31",
            "splits": {"dev": ("2024-01-01", "2024-09-30"), "val": ("2024-10-01", "2024-12-31"),
                       "final": ("2025-01-01", "2025-12-31")}},
    "12m": {"start": "2025-01-01", "end": "2025-12-31",
            "splits": {"dev": ("2025-01-01", "2025-06-30"), "val": ("2025-07-01", "2025-08-31"),
                       "final": ("2025-09-01", "2025-12-31")}},
}


def load(root: Path) -> pd.DataFrame:
    s1 = pd.read_csv(root / "session_cost_scan.csv")
    df = s1[["date", "early_close", "es_contract", "roll_day", "es_mbp1_cost", "es_mbp1_bytes",
             "opra_def_cost"]].copy()
    p2 = root / "opra_selected_scan.csv"
    if p2.exists():
        s2 = pd.read_csv(p2)[["date", "n_sel_primary", "cost_primary", "n_sel_expanded",
                              "cost_expanded", "expiries"]]
        df = df.merge(s2, on="date", how="left")
    return df


def summarize(df: pd.DataFrame, cushion: float, ceiling: float) -> dict:
    out = {}
    for name, d in DESIGNS.items():
        sub = df[(df.date >= d["start"]) & (df.date <= d["end"])]
        have_opra = sub["cost_primary"].notna().sum() if "cost_primary" in sub else 0
        es = sub.es_mbp1_cost.sum()
        odef = sub.opra_def_cost.sum()
        opra = sub["cost_primary"].sum() if have_opra else float("nan")
        opra_exp = sub["cost_expanded"].sum() if have_opra else float("nan")
        total = es + odef + (opra if have_opra else 0.0)
        out[name] = {
            "sessions": int(len(sub)),
            "split_sessions": {k: int(((sub.date >= a) & (sub.date <= b)).sum())
                               for k, (a, b) in d["splits"].items()},
            "es_mbp1_usd": round(es, 2),
            "es_mbp1_gb": round(sub.es_mbp1_bytes.sum() / 1e9, 1),
            "es_mbp1_usd_per_session": {"median": round(sub.es_mbp1_cost.median(), 3),
                                         "max": round(sub.es_mbp1_cost.max(), 3)},
            "opra_definitions_usd": round(odef, 2),
            "opra_cbbo1m_selected_usd": round(opra, 2),
            "opra_cbbo1m_expanded_usd": round(opra_exp, 2),
            "opra_sessions_costed": int(have_opra),
            "total_usd": round(total, 2),
            "total_padded_usd": round(total * (1 + cushion), 2),
            "fits_ceiling": bool(total * (1 + cushion) <= ceiling),
            "ceiling_usd": ceiling,
        }
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("outputs/planning")
    import yaml

    b = yaml.safe_load(Path("configs/budget.yaml").read_text())
    res = summarize(load(root), b["estimate_cushion"], b["project_ceiling_usd"])
    (root / "design_cost_summary.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
