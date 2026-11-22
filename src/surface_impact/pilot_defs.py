"""Pilot step 1: purchase definitions (OPRA SPXW.OPT parent, ES quarterly) and the prior-
session ES daily reference for the two pilot dates. All requests go through the ledger.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

from .budget import BudgetConfig, Ledger
from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .fetch import Request, fetch

PILOT_DATES = ["2024-02-01", "2024-08-01"]


def requests_for(day: str) -> list[Request]:
    s = {x.day.isoformat(): x for x in eligible_sessions("2024-01-01", "2024-12-31")}[day]
    sessions = eligible_sessions(s.day - timedelta(days=10), s.day)
    prev = [x for x in sessions if x.day < s.day][-1]
    sched = active_contract_schedule([prev.day, s.day])
    es_today = sched[s.day][0].candidate_raw_symbol()
    es_prev = sched[prev.day][0].candidate_raw_symbol()
    nxt = (s.day + timedelta(days=1)).isoformat()
    return [
        Request("OPRA.PILLAR", "definition", ["SPXW.OPT"], day, nxt, stype_in="parent",
                purpose=f"pilot SPXW definitions {day}"),
        Request("GLBX.MDP3", "definition", sorted({es_today, es_prev}), day, nxt,
                purpose=f"pilot ES definitions {day}"),
        Request("GLBX.MDP3", "ohlcv-1d", [es_prev], prev.day.isoformat(),
                (prev.day + timedelta(days=1)).isoformat(),
                purpose=f"prior-session ES reference for strike band {day}"),
    ]


def main(dry_run: bool):
    cfg = BudgetConfig.load("configs/budget.yaml")
    ledger = Ledger("data/ledger/cost_ledger.csv", cfg)
    results = []
    for d in PILOT_DATES:
        for r in requests_for(d):
            res = fetch(r, ledger=ledger, phase="pilot", raw_root=Path("data/raw"), dry_run=dry_run)
            results.append({"purpose": r.purpose, "status": res["status"],
                            "estimate": res.get("estimate"), "path": res.get("path")})
            print(json.dumps(results[-1]))
    print("ledger:", json.dumps(ledger.summary(), default=str))


if __name__ == "__main__":
    main(dry_run="--execute" not in sys.argv)
