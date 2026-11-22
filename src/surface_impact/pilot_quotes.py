"""Pilot step 2 (protocol v0.2): quotes for the two pilot dates.

Contract selection uses the *purchased* SPXW definitions (raw symbols) and the Databento
dated-ES ohlcv-1d reference close (D-015), never TradingView. Requests:
  * OPRA cbbo-1m, selected SPXW symbols, 09:30 NY -> close (chunked <= 2000 symbols)
  * GLBX bbo-1s, active ES contract, 08:30 NY -> close
  * GLBX status, active ES contract, full UTC day
  * GLBX mbp-1 audit, 2024-02-01 only (D-014 item 4)
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import databento as db

from .budget import BudgetConfig, Ledger
from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .fetch import Request, fetch
from .pilot_defs import requests_for as def_requests
from .selection import select_contracts

PILOT = {"2024-02-01": {"audit_mbp1": True}, "2024-08-01": {"audit_mbp1": False}}
CHUNK = 2000


def _iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def load_store(req: Request):
    return db.DBNStore.from_file(req.path(Path("data/raw"))).to_df()


def build(day: str) -> tuple[list[Request], dict]:
    s = {x.day.isoformat(): x for x in eligible_sessions("2024-01-01", "2024-12-31")}[day]
    dreqs = def_requests(day)
    defs = load_store(dreqs[0])
    ref = float(load_store(dreqs[2])["close"].iloc[0])
    sel = select_contracts(sorted(defs["raw_symbol"].unique()), ref, s.surface_cutoffs())
    es = active_contract_schedule([s.day])[s.day][0].candidate_raw_symbol()
    nxt = (s.day + timedelta(days=1)).isoformat()
    reqs = [Request("OPRA.PILLAR", "cbbo-1m", sel["symbols"][i:i + CHUNK], _iso(s.open_utc),
                    _iso(s.close_utc), purpose=f"pilot SPXW cbbo-1m {day} chunk {i // CHUNK}")
            for i in range(0, len(sel["symbols"]), CHUNK)]
    reqs.append(Request("GLBX.MDP3", "bbo-1s", [es], _iso(s.es_download_start_utc),
                        _iso(s.close_utc), purpose=f"pilot ES bbo-1s {day}"))
    reqs.append(Request("GLBX.MDP3", "status", [es], day, nxt, purpose=f"pilot ES status {day}"))
    if PILOT[day]["audit_mbp1"]:
        reqs.append(Request("GLBX.MDP3", "mbp-1", [es], _iso(s.es_download_start_utc),
                            _iso(s.close_utc), purpose=f"pilot ES mbp-1 AUDIT {day} (D-014)"))
    info = {"day": day, "es_contract": es, "ref_close": ref, "n_selected": len(sel["symbols"]),
            "expiries": sel["expiries"], "band": sel["band"]}
    return reqs, info


def main(execute: bool):
    cfg = BudgetConfig.load("configs/budget.yaml")
    ledger = Ledger("data/ledger/cost_ledger.csv", cfg)
    total = 0.0
    for day in PILOT:
        reqs, info = build(day)
        print(json.dumps(info))
        sel_dir = Path("data/pilot")
        sel_dir.mkdir(parents=True, exist_ok=True)
        (sel_dir / f"selection_{day}.json").write_text(json.dumps(info | {"symbols": reqs[0].symbols}))
        for r in reqs:
            res = fetch(r, ledger=ledger, phase="pilot", raw_root=Path("data/raw"), dry_run=not execute)
            est = res.get("estimate", {})
            total += est.get("cost_usd", 0.0) if res["status"] != "cached" else 0.0
            print(f"  {res['status']:8s} {r.purpose:45s} ${est.get('cost_usd', 0):.4f} "
                  f"{est.get('billable_bytes', 0) / 1e6:8.1f}MB {est.get('records', '')}")
    print(f"total new estimate ${total:.4f}  padded ${total * (1 + cfg.cushion):.4f}")
    print("ledger:", json.dumps(ledger.summary(), default=str))


if __name__ == "__main__":
    main(execute="--execute" in sys.argv)
