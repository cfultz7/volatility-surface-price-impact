"""Guarded, cached, budget-checked historical downloads.

Flow per request: cache check -> fresh metadata estimate -> padded reservation (ledger,
locked) -> download to a temp file -> sha256 -> atomic rename -> consume (estimated basis)
-> manifest JSON next to the file. On failure the reservation is released (the request
may still have been billed by the vendor; that possibility is noted in the ledger).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .budget import Ledger, cache_key
from .data_sources import check_symbols, client, estimate


@dataclass
class Request:
    dataset: str
    schema: str
    symbols: list[str]
    start: str
    end: str
    stype_in: str = "raw_symbol"
    stype_out: str = "instrument_id"
    purpose: str = ""
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        return cache_key(dataset=self.dataset, schema=self.schema, symbols=self.symbols,
                         start=self.start, end=self.end, stype_in=self.stype_in,
                         stype_out=self.stype_out)

    def path(self, raw_root: Path) -> Path:
        return raw_root / self.dataset / self.schema / f"{self.key()}.dbn.zst"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _batch_download(c, req: Request, tmp: Path, poll_s: float = 10.0, timeout_s: float = 1800.0):
    """Server-side batch job (same billing as streaming), for requests whose streams keep being
    aborted. Writes the single resulting DBN file to tmp."""
    import shutil
    import time as _t

    job = c.batch.submit_job(dataset=req.dataset, symbols=req.symbols, schema=req.schema,
                             start=req.start, end=req.end, stype_in=req.stype_in,
                             stype_out=req.stype_out, encoding="dbn", compression="zstd",
                             split_duration="none")
    jid, t0 = job["id"], _t.time()
    while True:
        st = c.batch.get_job_details(jid)["state"]
        if st == "done":
            break
        if st in ("expired", "failed") or _t.time() - t0 > timeout_s:
            raise RuntimeError(f"batch job {jid} state {st}")
        _t.sleep(poll_s)
    d = tmp.parent / f"_batch_{jid}"
    files = [p for p in c.batch.download(jid, output_dir=d) if p.name.endswith(".dbn.zst")]
    if len(files) != 1:
        raise RuntimeError(f"batch job {jid}: expected 1 data file, got {len(files)}")
    shutil.move(str(files[0]), str(tmp))
    shutil.rmtree(d, ignore_errors=True)
    return jid


def fetch(req: Request, *, ledger: Ledger, phase: str, raw_root: Path, dry_run: bool = False,
          use_batch: bool = False) -> dict:
    check_symbols(req.symbols)
    out = req.path(raw_root)
    man_path = out.with_suffix(".manifest.json")
    if out.exists() and man_path.exists():
        m = json.loads(man_path.read_text())
        if m.get("sha256") == sha256(out):
            return m | {"status": "cached"}
    c = client()
    est = estimate(c, dataset=req.dataset, schema=req.schema, symbols=req.symbols,
                   start=req.start, end=req.end, stype_in=req.stype_in)
    if dry_run:
        return {"status": "dry_run", "estimate": est, "cache_key": req.key()}
    # Reaching here means no valid cached file exists, so a repeat of a previously failed
    # (conservatively consumed) request is a genuine re-purchase and is charged again.
    rid = ledger.reserve(key=req.key(), phase=phase, estimate_usd=est["cost_usd"],
                         dataset=req.dataset, schema=req.schema, n_symbols=len(req.symbols),
                         start=req.start, end=req.end, note=req.purpose,
                         allow_repurchase=not out.exists())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.unlink(missing_ok=True)  # our own stale partial from an interrupted attempt
    try:
        import databento as db

        if use_batch:
            _batch_download(c, req, tmp)
        else:
            c.timeseries.get_range(dataset=req.dataset, schema=req.schema, symbols=req.symbols,
                                   start=req.start, end=req.end, stype_in=req.stype_in,
                                   stype_out=req.stype_out, path=str(tmp))
        digest = sha256(tmp)
        tmp.replace(out)
    except BaseException as e:
        tmp.unlink(missing_ok=True)
        msg = str(e)
        refused = type(e).__name__ == "BentoClientError" and msg[:1] == "4"
        if refused:  # vendor refused the request before creating it: not billable
            ledger.release(rid, note=f"refused by vendor: {msg[:60]}")
        else:  # may have been billed (e.g. stream aborted): count it, conservatively
            ledger.consume(rid, amount_usd=est["cost_usd"],
                           billing_basis="failed_download_conservative",
                           note=f"failed mid-request {type(e).__name__}; counted as spent")
        raise
    ledger.consume(rid, amount_usd=est["cost_usd"], billing_basis="metadata_estimate",
                   note=req.purpose)
    manifest = {
        "request": asdict(req), "cache_key": req.key(), "path": str(out), "sha256": digest,
        "bytes_on_disk": out.stat().st_size, "estimate": est, "ledger_request_id": rid,
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "databento_client_version": db.__version__,
    }
    tmpm = man_path.with_suffix(".tmp")
    tmpm.write_text(json.dumps(manifest, indent=2))
    tmpm.replace(man_path)
    return manifest | {"status": "fetched"}
