"""Databento access layer.

Only this module talks to Databento. Metadata calls (cost, billable size, record
count, symbology, dataset condition, unit prices) are free per Databento docs and
are *never* treated as downloads. Time-series requests go through ``fetch_range``,
which requires a prior budget reservation (see budget.py).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

FORBIDDEN_SYMBOLS = {"ALL_SYMBOLS"}


def load_api_key(project_root: Path | None = None) -> str | None:
    """Return DATABENTO_API_KEY from the environment, falling back to a local,
    gitignored ``.env`` file in the project root. The key is never logged."""
    key = os.environ.get("DATABENTO_API_KEY")
    if key:
        return key
    root = project_root or Path.cwd()
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABENTO_API_KEY="):
                key = line.split("=", 1)[1].strip()
                os.environ["DATABENTO_API_KEY"] = key
                return key
    return None


def client():
    import databento as db

    if not load_api_key():
        raise RuntimeError("DATABENTO_API_KEY is not configured locally; cannot query metadata.")
    return db.Historical()


def check_symbols(symbols: Any) -> list[str]:
    if symbols is None or isinstance(symbols, str):
        raise ValueError("Symbols must be an explicit list; implicit/all-symbol requests are forbidden.")
    syms = [str(s) for s in symbols]
    if not syms or any(s.upper() in FORBIDDEN_SYMBOLS for s in syms):
        raise ValueError("Explicit non-empty symbol list required; ALL_SYMBOLS is forbidden.")
    return syms


def _retry(fn, *a, tries: int = 4, **kw):
    for i in range(tries):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            transient = any(x in msg for x in ("429", "502", "503", "504", "timed out", "Connection"))
            if not transient or i == tries - 1:
                raise
            time.sleep(2.0 * (2**i))


def estimate(c, *, dataset: str, schema: str, symbols, start, end, stype_in: str = "raw_symbol") -> dict:
    """Free metadata estimate for one request: cost (USD), billable bytes, records."""
    syms = check_symbols(symbols)
    kw = dict(dataset=dataset, schema=schema, symbols=syms, start=start, end=end, stype_in=stype_in)
    cost = _retry(c.metadata.get_cost, **kw)
    size = _retry(c.metadata.get_billable_size, **kw)
    n = _retry(c.metadata.get_record_count, **kw)
    return {"cost_usd": float(cost), "billable_bytes": int(size), "records": int(n)}
