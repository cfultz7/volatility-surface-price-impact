"""Durable project cost ledger with padded reservations.

Every billable request must:
  1. obtain a fresh account-specific metadata estimate,
  2. reserve ``estimate * (1 + cushion)`` under an exclusive file lock, checked against
     both the project ceiling and the phase limit (e.g. pilot <= $5),
  3. after the request, record the consumed amount (the *estimate*, labelled as such,
     because actual charges are not readable programmatically through the client).

The ledger is a CSV (append-only rows) so it is easy to audit. A lock file prevents two
processes from reserving concurrently. Cached requests (same cache key) are never
re-reserved.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

FIELDS = ["ts_utc", "event", "request_id", "cache_key", "phase", "dataset", "schema",
          "n_symbols", "start", "end", "estimate_usd", "padded_usd", "amount_usd",
          "billing_basis", "note"]


@dataclass(frozen=True)
class BudgetConfig:
    initial_credits_usd: float
    project_ceiling_usd: float
    target_low_usd: float
    target_high_usd: float
    phase_limits_usd: dict[str, float]
    cushion: float

    @classmethod
    def load(cls, path: str | Path) -> "BudgetConfig":
        d = yaml.safe_load(Path(path).read_text())
        return cls(
            initial_credits_usd=float(d["initial_credits_usd"]),
            project_ceiling_usd=float(d["project_ceiling_usd"]),
            target_low_usd=float(d["target_usd"][0]),
            target_high_usd=float(d["target_usd"][1]),
            phase_limits_usd={k: float(v) for k, v in d["phase_limits_usd"].items()},
            cushion=float(d["estimate_cushion"]),
        )


class BudgetExceeded(RuntimeError):
    pass


def cache_key(**params) -> str:
    """Deterministic key over *all* query parameters."""
    norm = json.dumps({k: (sorted(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v))
                       for k, v in sorted(params.items())}, sort_keys=True)
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


class Ledger:
    def __init__(self, path: str | Path, config: BudgetConfig):
        self.path = Path(path)
        self.cfg = config
        self.lock = self.path.with_suffix(".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="") as f:
                csv.DictWriter(f, FIELDS).writeheader()

    @contextmanager
    def _locked(self, timeout: float = 30.0):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except (FileExistsError, PermissionError):  # Windows may raise PermissionError while
                # another holder is deleting the lock file (seen under OneDrive)
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"Ledger lock held: {self.lock}")
                time.sleep(0.1)
        try:
            yield
        finally:
            for _ in range(50):
                try:
                    self.lock.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.02)

    def rows(self) -> list[dict]:
        with self.path.open(newline="") as f:
            return list(csv.DictReader(f))

    # --- accounting -------------------------------------------------------------
    def _state(self, rows: list[dict]) -> dict:
        """Open reservations count at their padded amount; consumed at amount."""
        open_res: dict[str, dict] = {}
        consumed_total = 0.0
        consumed_phase: dict[str, float] = {}
        cached: set[str] = set()
        for r in rows:
            if r["event"] == "reserve":
                open_res[r["request_id"]] = r
            elif r["event"] in ("consume", "release"):
                res = open_res.pop(r["request_id"], None)
                # Releases recorded by older code after a mid-stream failure (ConnectionError etc.)
                # may have been billed: count them conservatively at the reserved estimate.
                if (r["event"] == "release" and res is not None
                        and "failed/interrupted" in r.get("note", "")
                        and "BentoClientError" not in r.get("note", "")):
                    amt = float(res["estimate_usd"])
                    consumed_total += amt
                    consumed_phase[res["phase"]] = consumed_phase.get(res["phase"], 0.0) + amt
                    continue
                if r["event"] == "consume":
                    amt = float(r["amount_usd"])
                    consumed_total += amt
                    ph = r["phase"] or (res or {}).get("phase", "")
                    consumed_phase[ph] = consumed_phase.get(ph, 0.0) + amt
                    cached.add(r["cache_key"])
        reserved_total = sum(float(r["padded_usd"]) for r in open_res.values())
        reserved_phase: dict[str, float] = {}
        for r in open_res.values():
            reserved_phase[r["phase"]] = reserved_phase.get(r["phase"], 0.0) + float(r["padded_usd"])
        return {"consumed": consumed_total, "reserved": reserved_total,
                "consumed_phase": consumed_phase, "reserved_phase": reserved_phase,
                "cached": cached, "open": open_res}

    def summary(self) -> dict:
        s = self._state(self.rows())
        return {k: v for k, v in s.items() if k not in ("cached", "open")} | {
            "ceiling": self.cfg.project_ceiling_usd,
            "headroom": self.cfg.project_ceiling_usd - s["consumed"] - s["reserved"],
            "preserved_credits_floor": self.cfg.initial_credits_usd - self.cfg.project_ceiling_usd,
        }

    def is_cached(self, key: str) -> bool:
        return key in self._state(self.rows())["cached"]

    def _append(self, **row):
        row.setdefault("ts_utc", datetime.now(timezone.utc).isoformat())
        with self.path.open("a", newline="") as f:
            csv.DictWriter(f, FIELDS).writerow({k: row.get(k, "") for k in FIELDS})

    def reserve(self, *, key: str, phase: str, estimate_usd: float, dataset: str, schema: str,
                n_symbols: int, start: str, end: str, note: str = "",
                allow_repurchase: bool = False) -> str:
        """allow_repurchase=True is passed by fetch() only after it verified that no cached file
        exists (e.g. an earlier attempt failed mid-stream and was conservatively counted). The
        repurchase is charged again against the ceiling."""
        if phase not in self.cfg.phase_limits_usd:
            raise ValueError(f"Unknown budget phase {phase!r}")
        padded = estimate_usd * (1.0 + self.cfg.cushion)
        with self._locked():
            s = self._state(self.rows())
            if key in s["cached"] and not allow_repurchase:
                raise ValueError("Request already consumed and cached; do not pay again.")
            if any(r["cache_key"] == key for r in s["open"].values()):
                raise ValueError("An open reservation already exists for this request.")
            total_after = s["consumed"] + s["reserved"] + padded
            if total_after > self.cfg.project_ceiling_usd + 1e-12:
                raise BudgetExceeded(
                    f"Padded ${padded:.4f} would bring project to ${total_after:.4f} "
                    f"> ceiling ${self.cfg.project_ceiling_usd:.2f}")
            ph_after = (s["consumed_phase"].get(phase, 0.0) + s["reserved_phase"].get(phase, 0.0)
                        + padded)
            if ph_after > self.cfg.phase_limits_usd[phase] + 1e-12:
                raise BudgetExceeded(
                    f"Padded ${padded:.4f} would bring phase {phase!r} to ${ph_after:.4f} "
                    f"> phase limit ${self.cfg.phase_limits_usd[phase]:.2f}")
            rid = hashlib.sha256(f"{key}{time.time_ns()}".encode()).hexdigest()[:16]
            self._append(event="reserve", request_id=rid, cache_key=key, phase=phase,
                         dataset=dataset, schema=schema, n_symbols=n_symbols, start=start,
                         end=end, estimate_usd=f"{estimate_usd:.6f}", padded_usd=f"{padded:.6f}",
                         note=note)
            return rid

    def consume(self, rid: str, *, amount_usd: float, billing_basis: str = "metadata_estimate",
                note: str = ""):
        with self._locked():
            s = self._state(self.rows())
            res = s["open"].get(rid)
            if res is None:
                raise ValueError(f"No open reservation {rid}")
            self._append(event="consume", request_id=rid, cache_key=res["cache_key"],
                         phase=res["phase"], dataset=res["dataset"], schema=res["schema"],
                         n_symbols=res["n_symbols"], start=res["start"], end=res["end"],
                         amount_usd=f"{amount_usd:.6f}", billing_basis=billing_basis, note=note)

    def expire_stale(self, older_than_minutes: float = 20.0) -> int:
        """Open reservations older than the threshold belong to dead/killed processes. They may
        have been billed, so they are consumed at their estimate (conservative)."""
        now = datetime.now(timezone.utc)
        n = 0
        with self._locked():
            s = self._state(self.rows())
            for rid, r in s["open"].items():
                age = (now - datetime.fromisoformat(r["ts_utc"])).total_seconds() / 60
                if age > older_than_minutes:
                    self._append(event="consume", request_id=rid, cache_key=r["cache_key"],
                                 phase=r["phase"], dataset=r["dataset"], schema=r["schema"],
                                 n_symbols=r["n_symbols"], start=r["start"], end=r["end"],
                                 amount_usd=r["estimate_usd"], billing_basis="stale_reservation_conservative",
                                 note=f"orphaned reservation ({age:.0f} min); counted as spent")
                    n += 1
        return n

    def release(self, rid: str, note: str = ""):
        with self._locked():
            s = self._state(self.rows())
            res = s["open"].get(rid)
            if res is None:
                raise ValueError(f"No open reservation {rid}")
            self._append(event="release", request_id=rid, cache_key=res["cache_key"],
                         phase=res["phase"], note=note)
