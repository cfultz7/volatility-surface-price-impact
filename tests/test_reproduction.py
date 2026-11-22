"""Paper, dashboard inputs and exported tables agree on values, samples, counts and status (spec test 21).
Skipped when no main run exists (e.g. CI without licensed data)."""

import json
from pathlib import Path

import pandas as pd
import pytest

OUT = Path("outputs/main")
pytestmark = pytest.mark.skipif(not (OUT / "results.json").exists(), reason="no main run available")


def _load():
    R = json.loads((OUT / "results.json").read_text(encoding="utf-8-sig"))
    md = Path("paper/manuscript.md").read_text(encoding="utf-8")
    return R, md


def test_primary_numbers_in_paper():
    R, md = _load()
    p = R["final"]["primary"]
    assert f"{p['delta_sell']:.3f}" in md
    assert f"[{p['ci_basic'][0]:.3f}, {p['ci_basic'][1]:.3f}]" in md
    assert f"{p['p_null_centered']:.3f}" in md
    assert R["status"] in md


def test_table_exports_match_results():
    R, _ = _load()
    coef = pd.read_csv(OUT / "coefficients.csv").set_index("term")
    assert abs(coef.loc["s_z@5", "estimate"] - R["final"]["primary"]["delta_sell"]) < 1e-12
    rob = pd.read_csv(OUT / "robustness.csv")
    prim = rob[rob.check.str.startswith("primary")].iloc[0]
    assert abs(prim.delta_sell - R["final"]["primary"]["delta_sell"]) < 1e-12
    full = pd.read_csv(OUT / "tables/final_M_SKEW_all_coefficients.csv").set_index("term")
    assert abs(full.loc["s_z", "estimate"] - R["final"]["primary"]["delta_sell"]) < 1e-9


def test_counts_consistent():
    R, md = _load()
    cov = pd.read_csv(OUT / "coverage.csv")
    assert R["gates"]["total"] == int(cov.qualifying.sum())
    assert R["final"]["primary"]["sessions"] == R["gates"]["final"]
    freeze = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))
    assert len(freeze["final_grid"]) == int((cov.split == "final").sum())


def test_freeze_precedes_final_outputs():
    freeze = json.loads((OUT / "design_freeze.json").read_text(encoding="utf-8-sig"))
    frozen = pd.Timestamp(freeze["frozen_utc"])
    produced = pd.Timestamp((OUT / "coefficients.csv").stat().st_mtime, unit="s", tz="UTC")
    assert produced > frozen
