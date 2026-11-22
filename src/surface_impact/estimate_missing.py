"""Free estimate of what completing the acquisition would cost (no downloads)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import databento as db

from .acquire import CHUNK, RAW, _iso, session_requests_stage1
from .calendars import eligible_sessions
from .contracts import active_contract_schedule
from .data_sources import client, estimate
from .fetch import Request
from .selection import select_contracts


def main():
    last = {}
    for line in Path("data/acquisition_log.jsonl").read_text().splitlines():
        r = json.loads(line)
        last[r["date"]] = r["status"]
    allsess = eligible_sessions("2024-06-01", "2025-12-31")
    idx = {x.day: i for i, x in enumerate(allsess)}
    todo = [s for s in allsess if s.day.isoformat() >= "2025-01-01" and last.get(s.day.isoformat()) != "ok"]
    sched = active_contract_schedule([s.day for s in todo])
    c = client()
    total, rows = 0.0, []
    for s in todo:
        prev = allsess[idx[s.day] - 1]
        es = sched[s.day][0].candidate_raw_symbol()
        reqs = session_requests_stage1(s, prev, es)
        cost, missing = 0.0, []
        for r in reqs:
            if not r.path(RAW).exists():
                e = estimate(c, dataset=r.dataset, schema=r.schema, symbols=r.symbols, start=r.start,
                             end=r.end, stype_in=r.stype_in)
                cost += e["cost_usd"]; missing.append(r.schema)
        if reqs[0].path(RAW).exists() and reqs[1].path(RAW).exists():
            defs = db.DBNStore.from_file(reqs[0].path(RAW)).to_df()
            ref = float(db.DBNStore.from_file(reqs[1].path(RAW)).to_df()["close"].iloc[-1])
            sel = select_contracts(sorted(defs["raw_symbol"].unique()), ref, s.surface_cutoffs())
            for i in range(0, len(sel["symbols"]), CHUNK):
                q = Request("OPRA.PILLAR", "cbbo-1m", sel["symbols"][i:i + CHUNK], _iso(s.open_utc), _iso(s.close_utc))
                if not q.path(RAW).exists():
                    e = estimate(c, dataset=q.dataset, schema=q.schema, symbols=q.symbols, start=q.start, end=q.end)
                    cost += e["cost_usd"]; missing.append("cbbo-1m")
        else:
            cost += 0.03; missing.append("cbbo-1m (approx $0.03; needs definitions first)")
        total += cost
        rows.append({"date": s.day.isoformat(), "missing": missing, "estimate_usd": round(cost, 4)})
    out = {"sessions": len(rows), "estimate_usd": round(total, 3), "padded_usd": round(1.2 * total, 3), "detail": rows}
    Path("outputs/planning/completion_estimate.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    o = main()
    print(json.dumps({k: o[k] for k in ("sessions", "estimate_usd", "padded_usd")}))
