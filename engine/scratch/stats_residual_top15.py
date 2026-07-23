"""Full stat sheet for the residual-momentum top-15 screen, plus the engine baseline.

Everything the summary tables left out: risk-free-adjusted Sharpe, Sortino/Calmar, the monthly
distribution (which is what you actually live through), year-by-year returns, drawdown depth
AND duration, turnover, and the forward drawdown distribution.

SCREEN numbers — no stops, no slot caps, no CPCV/DSR/PBO, in-sample. Not a gated result.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean  # noqa: E402
from run_portfolio_gate import DEFAULT_HOLDOUT_START, MIN_BARS, _utc  # noqa: E402

COST_BPS_ONE_WAY = 2.0
LOOKBACK, SKIP, VOL_WIN, REBAL = 252, 21, 63, 21
MIN_NAMES, TOP_N = 40, 15
CAPITAL = 100_000.0


def build():
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    closes = {}
    for p in sorted((ENGINE_DIR / "data_store").glob("*_1d.parquet")):
        name = p.name[: -len("_1d.parquet")]
        for cand in (name, name.replace("_", "/")):
            try:
                df = store.load(cand, "1d")
            except Exception:
                continue
            if df is None or df.empty:
                continue
            try:
                df = clean(df)
            except Exception:
                break
            df = df[df.index < holdout]
            if len(df) >= MIN_BARS:
                closes[cand] = df["close"]
            break
    close = pd.DataFrame(closes).sort_index()
    close = close.dropna(axis=1, thresh=int(len(close) * 0.6))
    scored = close.notna().sum(axis=1)
    keep = scored >= MIN_NAMES
    close = close.loc[keep.idxmax():]
    close = close.loc[close.notna().sum(axis=1) >= MIN_NAMES]
    return close


def residual_returns(close):
    rets = close.pct_change().fillna(0.0)
    vol = rets.rolling(VOL_WIN).std() * np.sqrt(252)
    mkt = rets.mean(axis=1)
    var_m = mkt.rolling(LOOKBACK).var()
    rc, rv = {}, {}
    for c in rets.columns:
        beta = rets[c].rolling(LOOKBACK).cov(mkt) / var_m
        resid = rets[c] - beta * mkt
        rc[c] = resid.shift(SKIP).rolling(LOOKBACK).sum()
        rv[c] = resid.rolling(LOOKBACK).std() * np.sqrt(252)
    score = pd.DataFrame(rc) / pd.DataFrame(rv).clip(lower=0.05)

    sel = score.rank(axis=1, ascending=False) <= TOP_N
    w = sel.astype(float) * (1.0 / vol.clip(lower=0.05))
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    mask = pd.Series(False, index=w.index)
    mask.iloc[::REBAL] = True
    w = w.where(mask, np.nan).ffill().fillna(0.0)
    turn = w.diff().abs().sum(axis=1)
    net = (w.shift(1) * rets).sum(axis=1) - turn * COST_BPS_ONE_WAY / 1e4
    return net.dropna(), turn, w


def sheet(r: pd.Series, turn: pd.Series, w: pd.DataFrame):
    eq = (1 + r).cumprod()
    yrs = len(r) / 252.0
    cagr = float(eq.iloc[-1]) ** (1 / yrs) - 1
    ann_arith = float(r.mean() * 252)
    vol = float(r.std(ddof=1) * np.sqrt(252))
    dd_s = (eq - eq.cummax()) / eq.cummax()
    maxdd = float(-dd_s.min())
    down = r[r < 0]
    sortino = float(r.mean() / down.std(ddof=1) * np.sqrt(252)) if len(down) > 1 else 0.0

    # drawdown duration
    in_dd = dd_s < -1e-9
    runs, cur = [], 0
    for v in in_dd:
        cur = cur + 1 if v else 0
        runs.append(cur)
    max_dd_days = max(runs) if runs else 0

    # forward 1y drawdown distribution
    rng = np.random.default_rng(42)
    sim = np.cumprod(1 + rng.choice(r.to_numpy(), size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(sim, axis=1)
    fdd = ((pk - sim) / pk).max(axis=1)
    fsim = sim[:, -1] - 1.0

    m = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    y = r.resample("YE").apply(lambda x: (1 + x).prod() - 1)

    print("=" * 78)
    print(f"RESIDUAL MOMENTUM — top {TOP_N} of {w.shape[1]} | {len(r)} bars ({yrs:.1f}y) "
          f"| £{CAPITAL:,.0f}")
    print("=" * 78)

    print("\nRETURN")
    print(f"  CAGR (compounded)               {cagr*100:8.2f}%")
    print(f"  Total return over {yrs:.1f}y        {(float(eq.iloc[-1])-1)*100:8.1f}%")
    print(f"  Arithmetic ann (for reference)  {ann_arith*100:8.2f}%   <- NOT what you earn")
    print(f"  £/month (CAGR/12)               £{cagr*CAPITAL/12:7.0f}")
    print(f"  £/year                          £{cagr*CAPITAL:7.0f}")

    print("\nRISK-ADJUSTED")
    print(f"  Sharpe (rf=0, engine convention){vol and (ann_arith/vol) or 0:8.3f}")
    for rf in (0.02, 0.03, 0.04):
        print(f"  Sharpe (rf={rf*100:.0f}%)                   {(ann_arith-rf)/vol:8.3f}")
    print(f"  Sortino                         {sortino:8.3f}")
    print(f"  Calmar (CAGR / maxDD)           {cagr/maxdd if maxdd else 0:8.3f}")
    print(f"  Annualised volatility           {vol*100:8.2f}%")
    print(f"  Skew                            {float(r.skew()):8.2f}")
    print(f"  Excess kurtosis                 {float(r.kurtosis()):8.2f}")

    print("\nDRAWDOWN")
    print(f"  Backtest max drawdown           {maxdd*100:8.2f}%   (£{maxdd*CAPITAL:,.0f})")
    print(f"  Longest drawdown                {max_dd_days:6d} bars  ({max_dd_days/252:.1f}y)")
    print(f"  Forward 1y median DD            {np.median(fdd)*100:8.2f}%")
    print(f"  Forward 1y 95th-pct DD          {np.percentile(fdd,95)*100:8.2f}%   "
          f"(£{np.percentile(fdd,95)*CAPITAL:,.0f})")
    print(f"  Forward 1y 99th-pct DD          {np.percentile(fdd,99)*100:8.2f}%")
    print(f"  P(drawdown > 10%) in 1 year     {(fdd>0.10).mean()*100:8.1f}%")
    print(f"  P(drawdown > 11%) in 1 year     {(fdd>0.11).mean()*100:8.1f}%")
    print(f"  P(drawdown > 15%) in 1 year     {(fdd>0.15).mean()*100:8.1f}%")
    print(f"  P(drawdown > 20%) in 1 year     {(fdd>0.20).mean()*100:8.1f}%")

    print("\nMONTHLY DISTRIBUTION  (what you actually live through)")
    print(f"  Months observed                 {len(m):6d}")
    print(f"  Median month                    {float(m.median())*100:+8.2f}%   "
          f"(£{float(m.median())*CAPITAL:+,.0f})")
    print(f"  Mean month                      {float(m.mean())*100:+8.2f}%")
    print(f"  Winning months                  {float((m>0).mean())*100:8.1f}%  "
          f"({int((m>0).sum())} of {len(m)})")
    print(f"  LOSING months                   {float((m<0).mean())*100:8.1f}%  "
          f"({int((m<0).sum())} of {len(m)})")
    print(f"  Best month                      {float(m.max())*100:+8.2f}%   "
          f"(£{float(m.max())*CAPITAL:+,.0f})")
    print(f"  Worst month                     {float(m.min())*100:+8.2f}%   "
          f"(£{float(m.min())*CAPITAL:+,.0f})")
    print(f"  Worst 3 months in a row         {float(m.rolling(3).sum().min())*100:+8.2f}%")
    print(f"  Months >= +£800                 {float((m*CAPITAL>=800).mean())*100:8.1f}%")

    print("\nFORWARD 1-YEAR OUTCOME (20,000 bootstrapped years)")
    print(f"  P(losing year)                  {(fsim<0).mean()*100:8.1f}%")
    print(f"  5th pct year                    {np.percentile(fsim,5)*100:+8.2f}%")
    print(f"  Median year                     {np.percentile(fsim,50)*100:+8.2f}%")
    print(f"  95th pct year                   {np.percentile(fsim,95)*100:+8.2f}%")

    print("\nYEAR BY YEAR")
    for ts, v in y.items():
        print(f"  {ts.year}                            {v*100:+8.2f}%   "
              f"(£{v*CAPITAL:+,.0f})")

    print("\nTRADING ACTIVITY")
    print(f"  Rebalance frequency             every {REBAL} bars (~monthly)")
    print(f"  Positions held                  {TOP_N}")
    print(f"  Mean daily turnover             {float(turn.mean())*100:8.2f}% of equity")
    print(f"  Annualised turnover             {float(turn.mean())*252*100:8.0f}%")
    print(f"  Cost drag (applied)             {float(turn.mean())*252*COST_BPS_ONE_WAY/100:8.2f}%/yr")

    print("\nSCALED BY CAPITAL (drawdown % is unchanged)")
    for cap in (100_000, 150_000, 200_000, 250_000):
        print(f"  £{cap:>7,}  ->  £{cagr*cap/12:6.0f}/mo   "
              f"p95 DD = £{np.percentile(fdd,95)*cap:,.0f}")

    print("\n" + "=" * 78)
    print("ENGINE BASELINE (gated, for comparison): Sharpe 0.922, CAGR 4.95%, £413/mo,")
    print("  backtest maxDD 10.3%, forward p95 DD 8.2%, 1,694 trades over 12.8y.")
    print("SCREEN ONLY — no stops, no slot caps, no CPCV/DSR/PBO, in-sample, top-15 chosen")
    print("after seeing 5 values. Not gated, not adopted.")
    print("=" * 78)


if __name__ == "__main__":
    close = build()
    r, turn, w = residual_returns(close)

    # The residual score needs LOOKBACK + SKIP + a rolling beta window before it produces
    # anything, so the first ~2 years hold NO position. Those flat bars are not a result:
    # they drag CAGR down, drag the median month to zero, and depress Sharpe (which scales
    # with sqrt(fraction of time invested)). Report the active window, and say so.
    live = w.abs().sum(axis=1) > 0
    first = live.idxmax()
    n_flat = int((~live).sum())
    print(f"[warmup] {n_flat} of {len(r)} bars hold no position; "
          f"first live bar {first.date()}. Reporting the ACTIVE window only.\n")
    r_active = r.loc[first:]
    sheet(r_active, turn.loc[first:], w.loc[first:])
