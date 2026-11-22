"""Batch surface/skew measurement for all acquired sessions (resumable, per-day files).

Variants: A_primary, B_primary, and Method A under the prespecified one-at-a-time quote
filter sensitivities (age60, age180, spr025, spr100) on the SAME acquired universe.
Output: data/derived/states/<date>.pkl ; combined outputs/main/skew_states.parquet
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .calendars import eligible_sessions
from .measure import FILTERS, measure_day

OUT = Path(os.environ.get("VSIMPACT_STATES", "data/derived/states"))


def one_day(day: str, lam: float) -> str:
    f = OUT / f"{day}.pkl"
    if f.exists():
        return f"{day} cached"
    t0 = time.time()
    rows = []
    for r in measure_day(day, lam, FILTERS["primary"], methods=("A", "B")):
        r["variant"] = f"{r['method']}_primary"
        rows.append(r)
    for key in ("age60", "age180", "spr025", "spr100"):
        for r in measure_day(day, lam, FILTERS[key], methods=("A",)):
            r["variant"] = f"A_{key}"
            rows.append(r)
    df = pd.DataFrame(rows)
    tmp = f.with_suffix(".tmp")
    df.to_pickle(tmp)
    tmp.replace(f)
    return f"{day} done {time.time() - t0:.0f}s ok={df.groupby('variant').ok.mean().round(2).to_dict()}"


def main(lam: float, start="2025-01-01", end="2025-12-31", workers=10):
    OUT.mkdir(parents=True, exist_ok=True)
    days = [s.day.isoformat() for s in eligible_sessions(start, end)]
    ok_days = set()
    log = Path("data/acquisition_log.jsonl")
    for line in log.read_text().splitlines():
        r = json.loads(line)
        if r.get("status") == "ok":
            ok_days.add(r["date"])
    days = [d for d in days if d in ok_days]
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(one_day, d, lam) for d in days]
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as e:  # noqa: BLE001
                print("ERROR", type(e).__name__, str(e)[:200], flush=True)
    parts = [pd.read_pickle(p) for p in sorted(OUT.glob("*.pkl"))]
    allst = pd.concat(parts, ignore_index=True)
    MAIN = Path(os.environ.get("VSIMPACT_OUT", "outputs/main")); MAIN.mkdir(parents=True, exist_ok=True)
    allst.drop(columns=["expiry_diags"]).to_parquet(MAIN / "skew_states.parquet")
    allst[["date", "cutoff", "variant", "expiry_diags"]].to_parquet(MAIN / "expiry_diagnostics.parquet")
    print("states:", len(allst), allst.groupby("variant").ok.mean().round(3).to_dict())


if __name__ == "__main__":
    main(float(sys.argv[1]), *sys.argv[2:4])

