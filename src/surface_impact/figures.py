"""Paper/dashboard figures generated only from saved artifacts (no recomputation of results)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path(os.environ.get("VSIMPACT_OUT", "outputs/main"))
FIG = OUT / "figures"
NY = "America/New_York"
plt.rcParams.update({"figure.dpi": 150, "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False})
C1, C2, CG = "#2a6f97", "#c8553d", "#8d99ae"


def fig_timeline():
    fig, ax = plt.subplots(figsize=(7.2, 1.9))
    ax.set_xlim(-11, 16); ax.set_ylim(0, 3); ax.axis("off")
    ax.annotate("", xy=(15.5, 1), xytext=(-10.5, 1), arrowprops=dict(arrowstyle="->", color="k"))
    for x, lab in ((-7, "surface cutoff s\n(09:55 <= t-2m)"), (-3, "t-1m\n(09:59)"), (0, "t\n(10:00)"),
                   (6, "t+5m\n(10:05, primary)"), (15, "t+15m\n(10:15)")):
        ax.plot([x, x], [0.85, 1.15], color="k", lw=1)
        ax.text(x, 0.45, lab, ha="center", va="top", fontsize=7)
    ax.axvspan(-3, 0, ymin=0.38, ymax=0.62, color=C1, alpha=0.35)
    ax.text(-1.5, 1.75, "ES flow window\n(t-60s, t]", ha="center", fontsize=7, color=C1)
    ax.annotate("", xy=(-6.92, 2.4), xytext=(-10.5, 2.4), arrowprops=dict(arrowstyle="-", color=C2, lw=3))
    ax.text(-8.7, 2.6, "SPXW cbbo-1m intervals ending <= s", ha="center", fontsize=7, color=C2)
    ax.text(-7, 1.3, "state available s+5s\n(<= t-60s required)", ha="center", fontsize=6.5, color=C2)
    ax.annotate("", xy=(15, 1.55), xytext=(0.2, 1.55), arrowprops=dict(arrowstyle="->", color=CG))
    ax.text(7.5, 1.7, "future ES midquote returns (1,2,5,10,15 min)", ha="center", fontsize=7, color=CG)
    ax.set_title("SCHEMATIC (not to scale): locked information timeline, example t = 10:00 New York", fontsize=8)
    return fig


def fig_coverage(cov: pd.DataFrame):
    cov = cov.copy()
    cov["d"] = pd.to_datetime(cov.date)
    monday0 = cov.d.min() - pd.Timedelta(days=int(cov.d.min().weekday()))
    cov["week"] = ((cov.d - monday0).dt.days // 7).astype(int)  # year-safe (Dec 29-31 stay in 2025)
    cov["dow"] = cov.d.dt.weekday
    piv = cov.pivot_table(index="dow", columns="week", values="coverage")
    fig, ax = plt.subplots(figsize=(7.2, 2.1))
    im = ax.imshow(piv.values, aspect="auto", cmap="viridis", vmin=0, vmax=1)
    ax.set_yticks(range(5), ["Mon", "Tue", "Wed", "Thu", "Fri"])
    ticks = [i for i, w in enumerate(piv.columns) if w % 4 == 0]
    ax.set_xticks(ticks, [(monday0 + pd.Timedelta(weeks=int(piv.columns[i]))).strftime("%b %d") for i in ticks],
                  fontsize=6.5)
    for r in cov[cov.roll_day.astype(bool)].itertuples():
        ax.plot(list(piv.columns).index(r.week), r.dow, marker="x", color="w", ms=6)
    fig.colorbar(im, ax=ax, label="valid joint share")
    ax.set_title("HISTORICAL 2025 sessions: share of valid joint endpoints (primary spec, h=5)\n"
                 "dark = no valid data; white = no session (holiday/weekend); x = ES roll day", fontsize=7.5)
    return fig


def fig_horizon(coef: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(4.6, 2.8))
    for term, col, lab in (("s_z", C2, "sell x skew (delta_sell)"), ("b_z", C1, "buy x skew (delta_buy)")):
        d = coef[coef.term.str.startswith(term + "@")].copy()
        d["h"] = d.term.str.split("@").str[1].astype(int)
        d = d.sort_values("h")
        ax.errorbar(d.h + (0.12 if term == "b_z" else -0.12), d.estimate,
                    yerr=[d.estimate - d.ci_lo, d.ci_hi - d.estimate], fmt="o-", color=col, capsize=2, label=lab, ms=4)
    ax.axhline(0, color="k", lw=0.6)
    ax.axvline(5, color=CG, lw=0.6, ls="--")
    ax.text(5.1, ax.get_ylim()[1] * 0.9, "primary", fontsize=7, color=CG)
    ax.set_xlabel("horizon h (minutes)"); ax.set_ylabel("bps per 1 SD flow per 1 SD skew")
    ax.set_title("HISTORICAL final period: interaction by horizon\n95% moving-block bootstrap (5 sessions)", fontsize=8)
    ax.legend(fontsize=7, frameon=False)
    return fig


def fig_robust(rob: pd.DataFrame):
    r = rob.dropna(subset=["delta_sell"]).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 0.30 * len(r) + 1.1))
    y = np.arange(len(r))
    ax.errorbar(r.delta_sell, y, xerr=[r.delta_sell - r.ci_lo, r.ci_hi - r.delta_sell], fmt="o",
                color=C2, capsize=2, ms=3)
    ax.set_yticks(y, r.check, fontsize=6.5)
    ax.axvline(0, color="k", lw=0.6)
    ax.set_xlabel("delta_sell at h = 5 (bps per SD per SD)")
    ax.set_title("HISTORICAL final period: robustness of delta_sell\n(95% block bootstrap; "
                 "calendar months are point estimates only)", fontsize=8)
    return fig


def fig_smile(day: str, lam: float):
    from .measure import FILTERS, expiry_instant, expiry_obs, load_quotes, snapshot, usable_expiry
    from .pricing import implied_w_vec
    from .surfaces import fit_method_a, fit_method_b

    q = load_quotes(day)
    cut = pd.Timestamp(f"{day} 12:00", tz=NY).tz_convert("UTC")
    snap, _ = snapshot(q, cut, FILTERS["primary"])
    fig, ax = plt.subplots(figsize=(5.0, 3.0))
    shown = 0
    for exp, g in sorted(snap.groupby("exp"), key=lambda kv: kv[0]):
        tau = (expiry_instant(exp) - cut).total_seconds() / (365 * 86400)
        if not 20 / 365 <= tau <= 45 / 365:
            continue
        fit, o, diag = expiry_obs(g, tau)
        if not usable_expiry(diag) or shown >= 2:
            continue
        iv = np.sqrt(implied_w_vec(o.c_mid, o.x) / tau)
        col = [C1, C2][shown]
        ax.scatter(np.log(o.x), iv * 100, s=5, color=col, alpha=0.5, label=f"quotes exp {exp} ({tau*365:.1f}d)")
        fa, fb = fit_method_a(o, lam), fit_method_b(o, tau)
        xs = np.linspace(o.x.min(), o.x.max(), 300)
        if fa.ok:
            ax.plot(np.log(xs), 100 * np.sqrt(implied_w_vec(fa(xs), xs) / tau), color=col, lw=1.2)
        if fb.ok:
            ax.plot(np.log(xs), 100 * np.sqrt(fb.w(np.log(xs)) / tau), color=col, lw=1, ls="--")
        shown += 1
    ax.set_xlabel("log(K / F)"); ax.set_ylabel("Black implied vol (%)")
    ax.set_title(f"HISTORICAL SPXW smile, {day} 12:00 NY\nsolid: Method A, dashed: SVI; observed strike "
                 "support only;\nedge IV is very sensitive to tiny premiums", fontsize=7.5)
    ax.legend(fontsize=6.5, frameon=False)
    return fig


def build_all(lam: float, smile_day: str):
    FIG.mkdir(parents=True, exist_ok=True)
    made = []

    def save(fig, name):
        fig.tight_layout(); fig.savefig(FIG / f"{name}.png"); plt.close(fig); made.append(name)

    save(fig_timeline(), "fig1_timeline")
    if (OUT / "coverage.csv").exists():
        save(fig_coverage(pd.read_csv(OUT / "coverage.csv")), "fig2_coverage")
    save(fig_smile(smile_day, lam), "fig3_smile")
    if (OUT / "coefficients.csv").exists():
        save(fig_horizon(pd.read_csv(OUT / "coefficients.csv")), "fig4_horizon")
    if (OUT / "robustness.csv").exists():
        save(fig_robust(pd.read_csv(OUT / "robustness.csv")), "fig5_robustness")
    return made


if __name__ == "__main__":
    import sys

    print(json.dumps(build_all(float(sys.argv[1]), sys.argv[2])))
