import threading

import pytest

from surface_impact.budget import BudgetConfig, BudgetExceeded, Ledger, cache_key
from surface_impact.data_sources import check_symbols


def cfg(ceiling=50.0, pilot=5.0):
    return BudgetConfig(125.0, ceiling, 25.0, 40.0, {"pilot": pilot, "main": ceiling}, 0.20)


def kw(**o):
    d = dict(phase="pilot", dataset="OPRA.PILLAR", schema="definition", n_symbols=1,
             start="2024-02-01", end="2024-02-02")
    d.update(o)
    return d


def test_padding_and_phase_limit(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg())
    rid = L.reserve(key="a", estimate_usd=4.0, **kw())  # padded 4.8 <= 5
    L.consume(rid, amount_usd=4.0)
    with pytest.raises(BudgetExceeded):
        L.reserve(key="b", estimate_usd=0.9, **kw())  # 4.0 + 1.08 > 5
    assert L.reserve(key="b", estimate_usd=0.8, **kw())  # 4.0 + 0.96 <= 5


def test_ceiling_counts_open_reservations(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg(ceiling=10.0, pilot=10.0))
    L.reserve(key="a", estimate_usd=5.0, **kw())  # padded 6 open
    with pytest.raises(BudgetExceeded):
        L.reserve(key="b", estimate_usd=3.5, **kw(phase="main"))  # 6 + 4.2 > 10


def test_cached_request_not_repaid(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg())
    k = cache_key(dataset="X", schema="y", symbols=["B", "A"], start="s", end="e")
    assert k == cache_key(dataset="X", schema="y", symbols=["A", "B"], start="s", end="e")
    rid = L.reserve(key=k, estimate_usd=0.1, **kw())
    L.consume(rid, amount_usd=0.1)
    assert L.is_cached(k)
    with pytest.raises(ValueError):
        L.reserve(key=k, estimate_usd=0.1, **kw())


def test_interrupted_request_release(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg())
    rid = L.reserve(key="a", estimate_usd=3.0, **kw())
    L.release(rid, note="interrupted")
    assert L.summary()["reserved"] == 0
    assert not L.is_cached("a")
    L.reserve(key="a", estimate_usd=3.0, **kw())  # can retry after release


def test_midstream_failure_counted_conservatively(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg())
    r1 = L.reserve(key="a", estimate_usd=1.0, **kw())
    L.release(r1, note="failed/interrupted: ConnectionError; vendor may still bill")  # legacy note
    r2 = L.reserve(key="b", estimate_usd=1.0, **kw())
    L.release(r2, note="failed/interrupted: BentoClientError; vendor may still bill")  # 402 refusal
    assert L.summary()["consumed"] == pytest.approx(1.0)


def test_concurrent_reservations_cannot_overspend(tmp_path):
    L = Ledger(tmp_path / "l.csv", cfg(ceiling=12.0, pilot=12.0))
    ok, bad = [], []

    def go(i):
        try:
            ok.append(L.reserve(key=f"k{i}", estimate_usd=2.0, **kw()))  # padded 2.4 each
        except BudgetExceeded:
            bad.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 5 and len(bad) == 5
    assert L.summary()["reserved"] <= 12.0 + 1e-9


def test_no_implicit_all_symbols():
    for bad in (None, "ALL_SYMBOLS", ["ALL_SYMBOLS"], [], "SPXW.OPT"):
        with pytest.raises(ValueError):
            check_symbols(bad)
    assert check_symbols(["SPXW.OPT"]) == ["SPXW.OPT"]
