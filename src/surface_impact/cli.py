"""`vsimpact` command-line interface: a thin wrapper over the pipeline modules.

Billable steps (`acquire`) go through the cached, lock-protected ledger in configs/budget.yaml.
No command downloads data implicitly; `plan-data` uses free metadata calls only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Volatility Surface Skew & State-Dependent Price Impact")


@app.command()
def demo(reps: int = typer.Option(0, help="Synthetic size/power replications (0 = skip)")):
    """Offline SYNTHETIC verification: skew recovery, OFI hand example, optional size/power."""
    import numpy as np
    import pandas as pd

    from .order_flow import ofi_increments
    from .pricing import norm_call
    from .skew import TARGET_TAU, curve_a, skew_from_curve
    from .surfaces import SmileObs, fit_method_a

    xs = np.arange(0.85, 1.1501, 0.005)
    iv = 0.15 - 0.25 * np.log(xs) + 0.4 * np.log(xs) ** 2
    c = norm_call(xs, iv**2 * TARGET_TAU)
    st = skew_from_curve(*curve_a(fit_method_a(SmileObs(xs, c, c - 2e-4, c + 2e-4, np.zeros(len(xs), bool)), 1.0),
                                  TARGET_TAU, None, None))
    typer.echo(f"SYNTHETIC skew recovery: SK30 = {st.sk30 * 100:.3f} vol pts, ATM IV = {st.atm_iv * 100:.3f}% (true ATM 15.000%)")
    b = pd.DataFrame([[100, 10, 101, 20], [100, 15, 101, 18]], columns=["bid_px", "bid_sz", "ask_px", "ask_sz"])
    b["ts_recv"], b["valid"], b["reset"] = [0, 1], True, False
    typer.echo(f"SYNTHETIC OFI hand example (expected 7): {ofi_increments(b).iloc[1]:.0f}")
    if reps:
        from .simulations import run

        typer.echo(json.dumps({"SYNTHETIC": run(reps)}, indent=2))


@app.command("plan-data")
def plan_data(start: str, end: str, out: Path = Path("outputs/planning")):
    """Free per-session cost scan (ES bbo-1s; no time-series download)."""
    from .planning import scan_bbo

    rows = scan_bbo(start, end, out / "es_bbo1s_scan.csv")
    typer.echo(f"sessions {len(rows)}; ES bbo-1s estimate ${sum(float(r['es_bbo1s_cost']) for r in rows if not r['error']):.2f}")


@app.command()
def acquire(start: str, end: str, workers: int = 4):
    """Budget-checked, cached acquisition (billable; respects configs/budget.yaml)."""
    from .acquire import main

    main(start, end, workers)


@app.command("select-lambda")
def select_lambda():
    from .select_lambda import run

    typer.echo(json.dumps(run(), indent=2))


@app.command()
def measure(lam: float):
    from .run_measure import main

    main(lam)


@app.command()
def analyze():
    """Panel -> gates -> pretest -> freeze -> final association, forecasts, robustness."""
    from .analyze import run_all

    run_all()


@app.command("render-paper")
def render_paper(lam: float = 1.0, smile_date: str = "2025-01-02"):
    from .diagnostics import main as diag
    from .figures import build_all
    from .manuscript import build, render

    diag()
    build_all(lam, smile_date)
    md, _ = build()
    render(md)
    typer.echo("paper/manuscript.md, paper/appendix.md, paper/paper.pdf written")


@app.command()
def dashboard(run: Path = Path("outputs/main")):
    """Launch the read-only Streamlit dashboard over saved artifacts."""
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app/dashboard.py", "--", "--run", str(run)])


if __name__ == "__main__":
    app()
