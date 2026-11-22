"""Stage-2 plan: exact SPXW contract selection per session from free parent symbology,
and free cost estimates for cbbo-1m on only those contracts (primary 0.85-1.15 band and
the predeclared 0.75-1.25 expansion). Strike-band reference is a PLANNING proxy (see
configs/planning_es_reference.json); the study reference comes from Databento ES.
"""

from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .calendars import eligible_sessions
from .data_sources import _retry, client, estimate
from .selection import select_contracts

CHUNK = 2000  # Databento per-request symbol limit (verify; requests chunked below it)
FIELDS = ["date", "ref_prev_close", "n_listed", "expiries", "n_sel_primary", "cost_primary",
          "bytes_primary", "records_primary", "n_sel_expanded", "cost_expanded", "error"]


def load_reference(path="configs/planning_es_reference.json") -> dict[date, float]:
    d = json.loads(Path(path).read_text())
    return {datetime.fromtimestamp(t + 3600, tz=timezone.utc).date(): c
            for t, c in zip(d["t"], d["c"])}


def prev_close(ref: dict[date, float], d: date) -> float:
    prior = [k for k in ref if k < d]
    return ref[max(prior)]


def _iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def est_chunks(c, syms, s):
    """Cost-only (fast). Bytes = cost / $2.0 per GB (OPRA cbbo-1m unit price, 2026-11-21);
    records not counted in fast mode (-1)."""
    tot = {"cost_usd": 0.0, "billable_bytes": 0, "records": -1}
    for i in range(0, len(syms), CHUNK):
        cost = float(_retry(c.metadata.get_cost, dataset="OPRA.PILLAR", schema="cbbo-1m",
                            symbols=syms[i:i + CHUNK], start=_iso(s.open_utc),
                            end=_iso(s.close_utc)))
        tot["cost_usd"] += cost
    tot["billable_bytes"] = int(tot["cost_usd"] / 2.0 * 1e9)
    return tot


def plan_session(c, s, ref) -> tuple[dict, dict]:
    row = {"date": s.day.isoformat(), "error": ""}
    detail = {}
    try:
        r = _retry(c.symbology.resolve, "OPRA.PILLAR", ["SPXW.OPT"], "parent", "instrument_id",
                   s.day.isoformat(), (s.day + timedelta(days=1)).isoformat())
        listed = list(r.get("result", {}).keys())
        px = prev_close(ref, s.day)
        cut = s.surface_cutoffs()
        prim = select_contracts(listed, px, cut, (0.85, 1.15))
        expd = select_contracts(listed, px, cut, (0.75, 1.25))
        ep = est_chunks(c, prim["symbols"], s)
        ee = est_chunks(c, expd["symbols"], s)
        row |= {"ref_prev_close": px, "n_listed": prim["n_listed_root"],
                "expiries": "|".join(prim["expiries"]), "n_sel_primary": len(prim["symbols"]),
                "cost_primary": ep["cost_usd"], "bytes_primary": ep["billable_bytes"],
                "records_primary": ep["records"], "n_sel_expanded": len(expd["symbols"]),
                "cost_expanded": ee["cost_usd"]}
        detail = {"date": s.day.isoformat(), "primary_symbols": prim["symbols"],
                  "expanded_extra": sorted(set(expd["symbols"]) - set(prim["symbols"])),
                  "expiries": prim["expiries"], "band_primary": prim["band"]}
    except Exception as ex:  # noqa: BLE001
        row["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    return row, detail


def run(start, end, root: Path, workers=8):
    root.mkdir(parents=True, exist_ok=True)
    out_csv = root / "opra_selected_scan.csv"
    sym_dir = root / "selected_symbols"
    sym_dir.mkdir(exist_ok=True)
    done = {}
    if out_csv.exists():
        with out_csv.open(newline="") as f:
            done = {r["date"]: r for r in csv.DictReader(f) if not r["error"]}
    ref = load_reference()
    sess = [s for s in eligible_sessions(start, end) if s.day.isoformat() not in done]
    from .planning import write_rows

    c = client()
    rows = list(done.values())
    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(sess), 20):  # checkpoint every 20 sessions
            res = list(ex.map(lambda s: plan_session(c, s, ref), sess[i:i + 20]))
            for row, det in res:
                if det:
                    (sym_dir / f"{row['date']}.json").write_text(json.dumps(det))
            rows += [r for r, _ in res]
            write_rows(out_csv, rows, FIELDS)
            print(f"stage2 {len(rows)}/{len(rows) - len(res) + len(sess) - i}", flush=True)
    return sorted(rows, key=lambda r: r["date"])


if __name__ == "__main__":
    a = sys.argv[1:]
    rows = run(a[0], a[1], Path(a[2]) if len(a) > 2 else Path("outputs/planning"))
    ok = [r for r in rows if not r["error"]]
    print(f"sessions={len(rows)} ok={len(ok)} cost_primary={sum(float(r['cost_primary']) for r in ok):.4f} "
          f"cost_expanded={sum(float(r['cost_expanded']) for r in ok):.4f}")
