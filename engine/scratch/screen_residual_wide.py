"""Does residual momentum need a WIDE cross-section to work?

The 39-instrument screen showed residual momentum cutting drawdown hard (btDD 29.1% -> 14.9%
at top 12) but NOT lifting Sharpe above the engine's 0.922. There is a structural reason to
expect that: Blitz/Huij/Martens and Blitz/Hanauer/Vidojevic study universes of hundreds to
thousands of individual stocks. Residualising means regressing out a common factor - with only
39 names, half of them correlated US mega-cap tech, the "market" proxy is poorly estimated and
the residual is mostly noise.

This runs the identical screen on every instrument with enough history in the store (~161
parquets available) instead of the 39-name book. If the breadth hypothesis is right, Sharpe
should rise materially. If it does not, residual momentum is dead for this data and the
conclusion is that the signal problem is not fixable by re-ranking.

Same costs, same 12-1 lookback, same monthly rebalance, same holdout cut. SCREEN ONLY.
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
STORE_DIR = ENGINE_DIR / "data_store"


def load_wide() -> pd.DataFrame:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    closes = {}
    for p in sorted(STORE_DIR.glob("*_1d.parquet")):
        inst = p.name[: -len("_1d.parquet")].replace("_", "/", 1) \
            if "/" not in p.name else p.name
        for cand in (p.name[: -len("_1d.parquet")],
                     p.name[: -len("_1d.parquet")].replace("_", "/")):
            try:
                df = store.load(cand, "1d")
            except Exception:
                continue
            if df is not None and not df.empty:
                inst = cand
                break
        else:
            continue
        try:
            df = clean(df)
        except Exception:
            continue
        df = df[df.index < holdout]
        if len(df) >= MIN_BARS:
            closes[inst] = df["close"]
    return pd.DataFrame(closes).sort_index()


def stats(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    ann = float(r.mean() * 252)
    vol = float(r.std(ddof=1) * np.sqrt(252))
    sh = ann / vol if vol > 0 else 0.0
    eq = (1 + r).cumprod()
    dd = float(abs(((eq - eq.cummax()) / eq.cummax()).min()))
    yrs = len(r) / 252
    cagr = float(eq.iloc[-1]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    rng = np.random.default_rng(42)
    sim = np.cumprod(1 + rng.choice(r.to_numpy(), size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(sim, axis=1)
    p95 = float(np.percentile(((pk - sim) / pk).max(axis=1), 95))
    print(f"  {label:<38} Sharpe {sh:6.3f}  CAGR {cagr*100:6.2f}%  "
          f"£{cagr*100000/12:6.0f}/mo  btDD {dd*100:5.1f}%  fwdP95 {p95*100:5.1f}%")
    return {"sharpe": sh, "cagr": cagr, "gbp_mo": cagr * 100000 / 12, "fwd_p95": p95}


def backtest(score, rets, vol, top_n) -> pd.Series:
    sel = score.rank(axis=1, ascending=False) <= top_n
    w = sel.astype(float) * (1.0 / vol.clip(lower=0.05))
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    mask = pd.Series(False, index=w.index)
    mask.iloc[::REBAL] = True
    w = w.where(mask, np.nan).ffill().fillna(0.0)
    turnover = w.diff().abs().sum(axis=1)
    return (w.shift(1) * rets).sum(axis=1) - turnover * COST_BPS_ONE_WAY / 1e4


def main() -> int:
    close = load_wide()
    close = close.dropna(axis=1, thresh=int(len(close) * 0.6))

    # Instruments have ragged start dates. Without this, 1,494 of 3,798 dates had <=5
    # scored names, so "top 5" and "top 30" selected the SAME set and every top_n
    # returned pixel-identical results. Restrict to the window where the cross-section
    # is actually wide enough for a rank to mean anything.
    MIN_NAMES = 40
    scored = close.notna().sum(axis=1)
    keep = scored >= MIN_NAMES
    if keep.any():
        close = close.loc[keep.idxmax():]
        close = close.loc[close.notna().sum(axis=1) >= MIN_NAMES]
    print(f"[panel] restricted to dates with >= {MIN_NAMES} live names: "
          f"{len(close)} bars, {close.shape[1]} instruments")

    rets = close.pct_change().fillna(0.0)
    vol = rets.rolling(VOL_WIN).std() * np.sqrt(252)
    mkt = rets.mean(axis=1)

    print("=" * 96)
    print(f"WIDE SCREEN — residual momentum on {close.shape[1]} instruments "
          f"({len(close)} bars, {len(close)/252:.1f}y) vs 39 in the book")
    print("=" * 96)

    total_mom = (close.shift(SKIP) / close.shift(SKIP + LOOKBACK) - 1.0) / vol.clip(lower=0.05)

    var_m = mkt.rolling(LOOKBACK).var()
    rc, rv = {}, {}
    for c in rets.columns:
        beta = rets[c].rolling(LOOKBACK).cov(mkt) / var_m
        resid = rets[c] - beta * mkt
        rc[c] = resid.shift(SKIP).rolling(LOOKBACK).sum()
        rv[c] = resid.rolling(LOOKBACK).std() * np.sqrt(252)
    resid_mom = pd.DataFrame(rc) / pd.DataFrame(rv).clip(lower=0.05)

    out = {}
    for top_n in (5, 10, 15, 20, 30):
        print(f"\n--- top {top_n} of {close.shape[1]} ---")
        out[f"total_{top_n}"] = stats(backtest(total_mom, rets, vol, top_n),
                                      f"total-return momentum (top {top_n})")
        out[f"resid_{top_n}"] = stats(backtest(resid_mom, rets, vol, top_n),
                                      f"RESIDUAL momentum (top {top_n})")

    print("\n" + "=" * 96)
    b = max(out.items(), key=lambda kv: kv[1]["sharpe"])
    print(f"BEST SHARPE: {b[0]} -> {b[1]['sharpe']:.3f}, £{b[1]['gbp_mo']:.0f}/mo, "
          f"fwd p95 DD {b[1]['fwd_p95']*100:.1f}%")
    hit = {k: v for k, v in out.items() if v["gbp_mo"] >= 800 and v["fwd_p95"] <= 0.11}
    print(f"Configs at >=£800/mo INSIDE an 11% wall: {len(hit)}")
    for k, v in sorted(hit.items(), key=lambda kv: -kv[1]["sharpe"]):
        print(f"   {k}: £{v['gbp_mo']:.0f}/mo Sharpe {v['sharpe']:.3f} "
              f"fwdP95 {v['fwd_p95']*100:.1f}%")
    print("\nEngine baseline: Sharpe 0.922, £413/mo, fwd p95 8.2%")
    print("39-name residual best was Sharpe 0.911. If wide is materially higher, breadth "
          "was the missing ingredient.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
