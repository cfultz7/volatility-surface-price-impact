"""Pretest transforms and nested linear models (spec §13-14).

Transforms are fit on pretest data only (dev + val) and frozen:
  q = clip(ofi_norm, p0.5, p99.5) / sd_pretest(clipped)   (no demeaning: zero flow stays 0)
  b = max(q, 0), s = max(-q, 0)
  z = (sk30 - mean_pre) / sd_pre ; other controls standardized with pretest mean/sd
Models (identical rows):
  M_PRICE: 1 + X
  M_FLOW : M_PRICE + b, s, b*RV, s*RV, b*log_depth, s*log_depth
  M_SKEW : M_FLOW + z, b*z, s*z
Primary coefficient: delta_sell = coefficient on s*z at h = 5, Method A.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

CONTROLS = ["atm_iv", "rv30", "spread_bps", "log_depth", "r_flow", "r_prev5", "r_prev60",
            "ofi_lag1", "sin1", "cos1", "sin2", "cos2", "es_dte", "early_close", "roll_day"]
WEEKDAY = ["wd1", "wd2", "wd3", "wd4"]


@dataclass
class Transforms:
    ofi_lo: float
    ofi_hi: float
    ofi_sd: float
    z_mean: float
    z_sd: float
    ctrl_mean: dict
    ctrl_sd: dict

    def to_json(self):
        return json.dumps(asdict(self), default=float)


def fit_transforms(pre: pd.DataFrame, skew_col: str = "sk30") -> Transforms:
    o = pre["ofi_norm"].dropna()
    lo, hi = np.percentile(o, [0.5, 99.5])
    sd = float(np.clip(o, lo, hi).std(ddof=1))
    zm, zs = float(pre[skew_col].mean()), float(pre[skew_col].std(ddof=1))
    cm, cs = {}, {}
    for c in CONTROLS:
        v = pre[c].astype(float)
        cm[c], cs[c] = float(v.mean()), float(v.std(ddof=1))
        if not np.isfinite(cs[c]) or cs[c] == 0:
            cs[c] = 1.0  # genuinely constant column; logged by caller
    return Transforms(float(lo), float(hi), sd, zm, zs, cm, cs)


def design(df: pd.DataFrame, tr: Transforms, model: str, skew_col: str = "sk30",
           ofi_col: str = "ofi_norm") -> tuple[np.ndarray, list[str]]:
    q = np.clip(df[ofi_col].to_numpy(float), tr.ofi_lo, tr.ofi_hi) / tr.ofi_sd
    b, s = np.maximum(q, 0), np.maximum(-q, 0)
    cols, names = [np.ones(len(df))], ["const"]
    for c in CONTROLS:
        cols.append((df[c].to_numpy(float) - tr.ctrl_mean[c]) / tr.ctrl_sd[c]); names.append(c)
    wd = df["weekday"].to_numpy()
    for k in (1, 2, 3, 4):
        cols.append((wd == k).astype(float)); names.append(f"wd{k}")
    if model in ("M_FLOW", "M_SKEW"):
        rv = (df["rv30"].to_numpy(float) - tr.ctrl_mean["rv30"]) / tr.ctrl_sd["rv30"]
        ld = (df["log_depth"].to_numpy(float) - tr.ctrl_mean["log_depth"]) / tr.ctrl_sd["log_depth"]
        for v, n in ((b, "b"), (s, "s"), (b * rv, "b_rv"), (s * rv, "s_rv"), (b * ld, "b_ld"), (s * ld, "s_ld")):
            cols.append(v); names.append(n)
    if model == "M_SKEW":
        z = (df[skew_col].to_numpy(float) - tr.z_mean) / tr.z_sd
        for v, n in ((z, "z"), (b * z, "b_z"), (s * z, "s_z")):
            cols.append(v); names.append(n)
    return np.column_stack(cols), names


@dataclass
class OLS:
    coef: np.ndarray
    names: list
    rank: int
    cond: float
    n: int

    def get(self, name):
        return float(self.coef[self.names.index(name)])


def ols(X: np.ndarray, y: np.ndarray, names: list, rcond: float = 1e-10) -> OLS:
    """SVD least squares. Constant (zero-variance) non-intercept columns are dropped with a
    logged rule (coefficient reported as NaN)."""
    keep = np.ones(X.shape[1], bool)
    for j in range(1, X.shape[1]):
        if np.nanstd(X[:, j]) == 0:
            keep[j] = False
    Xk = X[:, keep]
    beta, _, rank, sv = np.linalg.lstsq(Xk, y, rcond=rcond)
    coef = np.full(X.shape[1], np.nan)
    coef[keep] = beta
    cond = float(sv[0] / sv[-1]) if len(sv) and sv[-1] > 0 else np.inf
    return OLS(coef, names, int(rank), cond, len(y))


def eligible_rows(df: pd.DataFrame, ycol: str, skew_col: str = "sk30") -> pd.Series:
    need = ["ofi_norm", skew_col, ycol] + CONTROLS
    return df[need].notna().all(axis=1) & df["flow_ok"].astype(bool)


def cluster_se(X, y, coef, groups):
    """Day-clustered (CR0) covariance, diagnostic only."""
    m = np.all(np.isfinite(coef))
    keep = np.isfinite(coef)
    Xk = X[:, keep]
    e = y - Xk @ coef[keep]
    XtX_inv = np.linalg.pinv(Xk.T @ Xk)
    meat = np.zeros((Xk.shape[1], Xk.shape[1]))
    for g in np.unique(groups):
        i = groups == g
        sg = Xk[i].T @ e[i]
        meat += np.outer(sg, sg)
    V = XtX_inv @ meat @ XtX_inv
    se = np.full(len(coef), np.nan)
    se[keep] = np.sqrt(np.diag(V))
    del m
    return se
