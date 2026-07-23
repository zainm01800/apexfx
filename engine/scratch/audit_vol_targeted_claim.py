"""Audit the '0.85% sweet spot vol-targeted dual momentum' claim (Sharpe 1.331, £807/mo).

The claimed numbers reproduce exactly from test_dual_momentum_085_risk.py. The question is
whether that script measures a tradable strategy. It is a vectorised weights x returns
calculation with NO transaction costs, NO risk-free rate, and NO engine involvement.

This script quantifies each gap separately so the size of every correction is visible.
Measurement only - no ledger charge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

STORE = ENGINE_DIR / "data_store"
HOLDOUT = pd.Timestamp("2025-01-01", tz="UTC")

from scratch.run_runner_ev_test import ALL_INSTRUMENTS  # noqa: E402


def build():
    bars = {}
    for inst in ALL_INSTRUMENTS:
        p = STORE / f"{inst.replace('/', '_')}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    close = pd.DataFrame({i: d["close"] for i, d in bars.items()}).ffill().dropna(how="all")

    daily = close.pct_change().fillna(0)
    vol63 = daily.rolling(63).std() * np.sqrt(252)
    sma200 = close.rolling(200).mean()
    trend = (close > sma200) & (sma200 > sma200.shift(20))
    score = (close.pct_change(126) / vol63).where(trend, np.nan)
    w = (score.rank(axis=1, ascending=False) <= 3).astype(float)
    w = w.mul(1.0 / vol63.clip(lower=0.05), axis=0)
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    return close, daily, w


def stats(r, label, rf_ann=0.0):
    r = pd.Series(r).dropna()
    ann = float(r.mean() * 252)
    vol = float(r.std(ddof=1) * np.sqrt(252))
    sh = (ann - rf_ann) / vol if vol > 0 else 0.0
    eq = (1 + r).cumprod()
    dd = float(abs(((eq - eq.cummax()) / eq.cummax()).min())) * 100
    print(f"  {label:<44} Sharpe {sh:6.3f}   ann {ann*100:6.2f}%   "
          f"maxDD {dd:5.2f}%   £{ann*100000/12:7.2f}/mo")
    return sh, ann, dd


close, daily, w = build()
target_vol = 0.0623
raw = (w.shift(1) * daily).sum(axis=1)
rv = raw.rolling(63).std() * np.sqrt(252)
scalar = (target_vol / rv.clip(lower=0.03)).shift(1).clip(upper=2.0).fillna(1.0)

# ---- turnover: how much does this thing actually trade? ----
lev_w = w.mul(scalar, axis=0)
turnover = lev_w.diff().abs().sum(axis=1)
print("=" * 78)
print("AUDIT: vol-targeted dual momentum (the source of Sharpe 1.331 / £807.52)")
print("=" * 78)
print(f"\nInstruments in panel        : {close.shape[1]}")
print(f"Trading days                : {len(close)}  ({len(close)/252:.1f} years)")
print(f"Mean DAILY turnover         : {turnover.mean()*100:.2f}% of equity")
print(f"Annualised turnover         : {turnover.mean()*252*100:,.0f}% of equity per year")
print(f"Max leverage applied        : {scalar.max():.2f}x  (vol scalar, clipped at 2.0)")
print(f"Mean leverage applied       : {scalar.mean():.2f}x")

print("\n--- 1. AS CLAIMED (zero costs, zero risk-free rate) ---")
stats(raw * scalar, "as reported by Gemini's script")

print("\n--- 2. WITH TRANSACTION COSTS (bps per unit turnover, round trip) ---")
for bps in (1, 2, 5, 10):
    stats(raw * scalar - turnover * bps / 10000.0, f"{bps} bps cost")

print("\n--- 3. RISK-FREE RATE (Sharpe must use EXCESS return) ---")
for rf in (0.0, 0.02, 0.03):
    stats(raw * scalar, f"rf = {rf*100:.0f}%", rf_ann=rf)

print("\n--- 4. BOTH, jointly (2bps costs + 2% risk-free) ---")
stats(raw * scalar - turnover * 2 / 10000.0, "realistic", rf_ann=0.02)

# ---- 5. the 'lowest balance £97,044' claim ----
eq = (1 + raw * scalar).cumprod() * 100000
print("\n--- 5. THE '£97,044 CAPITAL FLOOR' CLAIM ---")
print(f"  Lowest equity ever          : £{eq.min():,.0f}   on {eq.idxmin().date()}")
print(f"  ...which is day             : {eq.index.get_loc(eq.idxmin())} of {len(eq)}")
print(f"  Equity at the 13.3% peak    : £{eq[eq.cummax().idxmax():].max():,.0f}")
worst = ((eq - eq.cummax()) / eq.cummax()).idxmin()
peak_eq = float(eq.cummax()[worst])
print(f"  Worst drawdown IN £         : £{peak_eq - float(eq[worst]):,.0f}  "
      f"(peak £{peak_eq:,.0f} -> £{float(eq[worst]):,.0f} on {worst.date()})")

# ---- 6. arithmetic vs compounded return ----
r = raw * scalar
print("\n--- 6. THE '£807.52/month' FIGURE ---")
print(f"  Arithmetic ann (as claimed) : {float(r.mean()*252)*100:.2f}%  -> £{float(r.mean()*252)*100000/12:.2f}/mo")
cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252 / len(eq)) - 1
print(f"  Actual CAGR (compounded)    : {cagr*100:.2f}%  -> £{cagr*100000/12:.2f}/mo")
monthly = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
print(f"  MEDIAN real month           : {float(monthly.median())*100:+.2f}%  -> £{float(monthly.median())*100000:+.2f}")
print(f"  Losing months               : {int((monthly < 0).sum())} of {len(monthly)} "
      f"({float((monthly < 0).mean())*100:.0f}%)")
print(f"  Worst month                 : {float(monthly.min())*100:+.2f}%")
print("=" * 78)
