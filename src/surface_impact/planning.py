"""Cost-and-coverage planning. Makes **no time-series downloads**: only free
metadata estimates. Per-session results are cached so reruns do not re-query.

Stage-1 scan (this module, before any definitions are purchased):
  * ES mbp-1 for the active dated contract, 08:30 NY -> common close (exact request
    that the main design would make).
  * OPRA cbbo-1m for the whole SPXW.OPT parent, 09:30 -> close. This is an UPPER
    BOUND: the main design requests only explicitly selected contracts, whose cost
    is derived after pilot definitions are available (stage 2).
  * OPRA definition for SPXW.OPT parent on full UTC dates, and ES definitions.
"""

from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .data_sources import client, estimate

SCAN_FIELDS = [
    "date", "early_close", "es_contract", "roll_day",
    "es_mbp1_cost", "es_mbp1_bytes", "es_mbp1_records",
    "opra_parent_cbbo1m_cost", "opra_parent_cbbo1m_bytes", "opra_parent_cbbo1m_records",
    "opra_def_cost", "opra_def_bytes", "opra_def_records",
    "es_def_cost", "error",
]


def _iso(ts) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


UNIT_PRICE_PER_GB = {"GLBX.MDP3/mbp-1": 1.8, "OPRA.PILLAR/definition": 5.0}  # list_unit_prices 2026-11-21


def scan_session_fast(c, s, contract, roll) -> dict:
    """Cost-only scan (one get_cost per item). Billable bytes = cost / unit price per GB
    (decimal GB), which is how the unit prices are defined; records are not counted."""
    from datetime import date as _d

    from .data_sources import _retry

    row = {"date": s.day.isoformat(), "early_close": s.early_close,
           "es_contract": contract.candidate_raw_symbol(), "roll_day": roll, "error": ""}
    day_end = _d.fromordinal(s.day.toordinal() + 1).isoformat()
    try:
        es = float(_retry(c.metadata.get_cost, dataset="GLBX.MDP3", schema="mbp-1",
                          symbols=[row["es_contract"]], start=_iso(s.es_download_start_utc),
                          end=_iso(s.close_utc)))
        od = float(_retry(c.metadata.get_cost, dataset="OPRA.PILLAR", schema="definition",
                          symbols=["SPXW.OPT"], stype_in="parent", start=s.day.isoformat(),
                          end=day_end))
        row |= {"es_mbp1_cost": es, "es_mbp1_bytes": int(es / 1.8 * 1e9),
                "opra_def_cost": od, "opra_def_bytes": int(od / 5.0 * 1e9)}
    except Exception as ex:  # noqa: BLE001
        row["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    return row


BBO_FIELDS = ["date", "early_close", "es_contract", "roll_day", "es_bbo1s_cost", "error"]


def scan_session_bbo(c, s, contract, roll) -> dict:
    """Protocol v0.2 (D-014): ES bbo-1s cost, 08:30 NY -> close, active dated contract."""
    from .data_sources import _retry

    row = {"date": s.day.isoformat(), "early_close": s.early_close,
           "es_contract": contract.candidate_raw_symbol(), "roll_day": roll, "error": ""}
    try:
        row["es_bbo1s_cost"] = float(_retry(
            c.metadata.get_cost, dataset="GLBX.MDP3", schema="bbo-1s",
            symbols=[row["es_contract"]], start=_iso(s.es_download_start_utc), end=_iso(s.close_utc)))
    except Exception as ex:  # noqa: BLE001
        row["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    return row


def scan_bbo(start: str, end: str, out_csv: Path, workers: int = 8) -> list[dict]:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if out_csv.exists():
        with out_csv.open(newline="") as f:
            done = {r["date"]: r for r in csv.DictReader(f) if not r["error"]}
    sessions = eligible_sessions(start, end)
    sched = active_contract_schedule([s.day for s in sessions])
    todo = [s for s in sessions if s.day.isoformat() not in done]
    c = client()
    rows = list(done.values())
    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(todo), 40):
            rows += list(ex.map(lambda s: scan_session_bbo(c, s, *sched[s.day]), todo[i:i + 40]))
            write_rows(out_csv, rows, BBO_FIELDS)
            print(f"bbo {len(rows)}", flush=True)
    return sorted(rows, key=lambda r: r["date"])


def scan_session(c, s, contract, roll) -> dict:
    row = {"date": s.day.isoformat(), "early_close": s.early_close,
           "es_contract": contract.candidate_raw_symbol(), "roll_day": roll, "error": ""}
    nxt = (s.day.toordinal() + 1)
    from datetime import date as _d

    day_end = _d.fromordinal(nxt).isoformat()
    try:
        e = estimate(c, dataset="GLBX.MDP3", schema="mbp-1", symbols=[row["es_contract"]],
                     start=_iso(s.es_download_start_utc), end=_iso(s.close_utc))
        row |= {"es_mbp1_cost": e["cost_usd"], "es_mbp1_bytes": e["billable_bytes"],
                "es_mbp1_records": e["records"]}
        o = estimate(c, dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=["SPXW.OPT"],
                     stype_in="parent", start=_iso(s.open_utc), end=_iso(s.close_utc))
        row |= {"opra_parent_cbbo1m_cost": o["cost_usd"], "opra_parent_cbbo1m_bytes": o["billable_bytes"],
                "opra_parent_cbbo1m_records": o["records"]}
        d = estimate(c, dataset="OPRA.PILLAR", schema="definition", symbols=["SPXW.OPT"],
                     stype_in="parent", start=s.day.isoformat(), end=day_end)
        row |= {"opra_def_cost": d["cost_usd"], "opra_def_bytes": d["billable_bytes"],
                "opra_def_records": d["records"]}
        ed = estimate(c, dataset="GLBX.MDP3", schema="definition", symbols=[row["es_contract"]],
                      start=s.day.isoformat(), end=day_end)
        row["es_def_cost"] = ed["cost_usd"]
    except Exception as ex:  # noqa: BLE001
        row["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    return row


def scan(start: str, end: str, out_csv: Path, workers: int = 4, fast: bool = False) -> list[dict]:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if out_csv.exists():
        with out_csv.open(newline="") as f:
            for r in csv.DictReader(f):
                if not r["error"]:
                    done[r["date"]] = r
    sessions = eligible_sessions(start, end)
    sched = active_contract_schedule([s.day for s in sessions])
    todo = [s for s in sessions if s.day.isoformat() not in done]
    c = client()
    rows = list(done.values())
    fn = scan_session_fast if fast else scan_session
    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(todo), 20):  # checkpoint every 20 sessions
            rows += list(ex.map(lambda s: fn(c, s, *sched[s.day]), todo[i:i + 20]))
            write_rows(out_csv, rows, SCAN_FIELDS)
            print(f"stage1 {len(rows)}/{len(sessions)}", flush=True)
    write_rows(out_csv, rows, SCAN_FIELDS)
    return sorted(rows, key=lambda r: r["date"])


def write_rows(out_csv: Path, rows: list[dict], fields: list[str]) -> None:
    rows = sorted(rows, key=lambda r: r["date"])
    tmp = out_csv.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out_csv)


def summarize(rows: list[dict], label: str) -> dict:
    def tot(k):
        return sum(float(r[k]) for r in rows if r.get(k) not in (None, ""))

    return {
        "label": label,
        "sessions": len(rows),
        "errors": sum(1 for r in rows if r["error"]),
        "early_closes": sum(1 for r in rows if str(r["early_close"]) == "True"),
        "es_mbp1_cost": round(tot("es_mbp1_cost"), 4),
        "es_mbp1_gb": round(tot("es_mbp1_bytes") / 1e9, 3),
        "es_mbp1_records": int(tot("es_mbp1_records")),
        "opra_parent_cbbo1m_cost_upper_bound": round(tot("opra_parent_cbbo1m_cost"), 4),
        "opra_parent_cbbo1m_gb": round(tot("opra_parent_cbbo1m_bytes") / 1e9, 3),
        "opra_def_cost": round(tot("opra_def_cost"), 4),
        "es_def_cost": round(tot("es_def_cost"), 4),
    }


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("outputs/planning")
    if len(sys.argv) > 3:  # partial range: scan and checkpoint only, e.g. one month
        scan(sys.argv[2], sys.argv[3], root / "session_cost_scan.csv",
             workers=8 if "--fast" in sys.argv else 4, fast="--fast" in sys.argv)
        raise SystemExit(0)
    rows = scan("2024-01-01", "2025-12-31", root / "session_cost_scan.csv")
    y24 = [r for r in rows if r["date"] < "2025"]
    y25 = [r for r in rows if r["date"] >= "2025"]
    summ = {"y2024": summarize(y24, "2024"), "y2025": summarize(y25, "2025"),
            "y2024_2025": summarize(rows, "2024-2025")}
    (root / "stage1_summary.json").write_text(json.dumps(summ, indent=2))
    print(json.dumps(summ, indent=2))
