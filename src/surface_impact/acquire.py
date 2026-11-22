"""Main acquisition (protocol v0.2, 12-month design, user-approved 2026-11-21).

Per eligible session d (in order; each step cached, costed, ledger-reserved):
  1. OPRA SPXW.OPT definitions (full UTC date d)
  2. GLBX ES ohlcv-1d of the contract active on d, for the previous eligible session
     (strike-band reference; D-015 notes it is a UTC-day bar)
  3. GLBX ES definition + status (full UTC date d)
  4. OPRA cbbo-1m for SPXW contracts selected from (1) and (2), 09:30 -> close
  5. GLBX ES bbo-1s, active contract, 08:30 -> close
Sessions are processed in parallel; every request goes through fetch() (ledger lock).
"""

from __future__ import annotations

import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import databento as db

from .budget import BudgetConfig, BudgetExceeded, Ledger
from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .fetch import Request, fetch
from .selection import select_contracts

RAW = Path("data/raw")
CHUNK = 2000


def _iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def session_requests_stage1(s, prev, es: str) -> list[Request]:
    d = s.day.isoformat()
    nxt = (s.day + timedelta(days=1)).isoformat()
    return [
        Request("OPRA.PILLAR", "definition", ["SPXW.OPT"], d, nxt, stype_in="parent",
                purpose=f"SPXW definitions {d}"),
        Request("GLBX.MDP3", "ohlcv-1d", [es], prev.day.isoformat(),
                (prev.day + timedelta(days=1)).isoformat(), purpose=f"ES ref close for {d}"),
        Request("GLBX.MDP3", "definition", [es], d, nxt, purpose=f"ES definition {d}"),
        Request("GLBX.MDP3", "status", [es], d, nxt, purpose=f"ES status {d}"),
        Request("GLBX.MDP3", "bbo-1s", [es], _iso(s.es_download_start_utc), _iso(s.close_utc),
                purpose=f"ES bbo-1s {d}"),
    ]


def run_session(s, prev, es: str, ledger: Ledger, phase: str) -> dict:
    d = s.day.isoformat()
    rec = {"date": d, "es_contract": es, "status": "ok", "notes": []}
    st1 = session_requests_stage1(s, prev, es)
    for r in st1:
        # Full-day SPXW definition streams were repeatedly aborted for some sessions (D-022):
        # SPXW definitions use the server-side batch API (same billing).
        fetch(r, ledger=ledger, phase=phase, raw_root=RAW,
              use_batch=(r.dataset == "OPRA.PILLAR" and r.schema == "definition"))
    defs = db.DBNStore.from_file(st1[0].path(RAW)).to_df()
    ref_df = db.DBNStore.from_file(st1[1].path(RAW)).to_df()
    if ref_df.empty:
        rec["status"] = "no_reference_close"
        return rec
    ref = float(ref_df["close"].iloc[-1])
    sel = select_contracts(sorted(defs["raw_symbol"].unique()), ref, s.surface_cutoffs())
    rec |= {"ref_close": ref, "n_selected": len(sel["symbols"]), "expiries": sel["expiries"],
            "band": sel["band"]}
    for i in range(0, len(sel["symbols"]), CHUNK):
        fetch(Request("OPRA.PILLAR", "cbbo-1m", sel["symbols"][i:i + CHUNK], _iso(s.open_utc),
                      _iso(s.close_utc), purpose=f"SPXW cbbo-1m {d} chunk {i // CHUNK}"),
              ledger=ledger, phase=phase, raw_root=RAW)
    sel_dir = Path("data/selection")
    sel_dir.mkdir(parents=True, exist_ok=True)
    (sel_dir / f"{d}.json").write_text(json.dumps(rec | {"symbols": sel["symbols"]}))
    return rec


def main(start: str, end: str, workers: int = 6, phase: str = "main"):
    cfg = BudgetConfig.load("configs/budget.yaml")
    ledger = Ledger("data/ledger/cost_ledger.csv", cfg)
    allsess = eligible_sessions("2024-06-01", end)
    idx = {x.day: i for i, x in enumerate(allsess)}
    sessions = [x for x in allsess if start <= x.day.isoformat() <= end]
    sched = active_contract_schedule([x.day for x in sessions])
    log = Path("data/acquisition_log.jsonl")
    done = set()
    if log.exists():
        for line in log.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") == "ok":
                done.add(r["date"])
    todo = [x for x in sessions if x.day.isoformat() not in done]
    print(f"sessions {len(sessions)} todo {len(todo)}", flush=True)
    stop = False
    with ThreadPoolExecutor(workers) as ex:
        futs = {}
        for s in todo:
            prev = allsess[idx[s.day] - 1]
            es = sched[s.day][0].candidate_raw_symbol()
            futs[ex.submit(run_session, s, prev, es, ledger, phase)] = s
        for f in as_completed(futs):
            s = futs[f]
            try:
                rec = f.result()
            except BudgetExceeded as e:
                rec = {"date": s.day.isoformat(), "status": "budget_exceeded", "error": str(e)}
                stop = True
            except Exception as e:  # noqa: BLE001
                rec = {"date": s.day.isoformat(), "status": "failed",
                       "error": f"{type(e).__name__}: {str(e)[:300]}",
                       "tb": traceback.format_exc()[-600:]}
            with log.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(rec["date"], rec["status"], rec.get("n_selected", ""), flush=True)
            if stop:
                for g in futs:
                    g.cancel()
    print("ledger:", json.dumps(ledger.summary(), default=str), flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], workers=int(a[2]) if len(a) > 2 else 6)
