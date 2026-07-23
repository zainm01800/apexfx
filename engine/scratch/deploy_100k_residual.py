"""What residual momentum actually delivers on a £100,000 IBKR account — real costs.

The screen charged a flat 2 bps per side to everything. That is right for liquid US equities
and WRONG for FX and crypto, and it ignores IBKR's per-ORDER minimums, which are a percentage
cost that grows as the account shrinks — the single most under-modelled drag on a small account.

This applies:
  * per-asset-class spread/slippage at the ENGINE's own rates (equity 4bps RT, crypto 9bps RT,
    FX on the per-pair pips model),
  * IBKR per-order commission with minimums, charged on the ACTUAL number of orders implied by
    each rebalance at £100k across 15 positions,
  * the fact that a £100k book split 15 ways is £6,667 per position, so the $1-2 order minimum
    is a real percentage, not a rounding error.

SCREEN economics, deployment costs. Still no stops/slot caps/CPCV/DSR/PBO. Not gated.
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

LOOKBACK, SKIP, VOL_WIN, REBAL = 252, 21, 63, 21
MIN_NAMES, TOP_N = 40, 15
CAPITAL = 100_000.0
GBPUSD = 1.27

#: One-way cost in bps, by asset class — the engine's own rates.
#: equity (0.5*2.0 + 1.0) = 2.0 bps/side; crypto (0.5*5.0 + 2.0) = 4.5 bps/side.
#: FX majors ~0.6 pip round trip on ~1.10 => ~2.7bps RT => ~1.4 bps/side, but retail
#: crosses (GBP/NZD, EUR/NZD...) are far wider, so 3.0 bps/side is used for crosses.
COST_BPS = {"equity": 2.0, "crypto": 4.5, "fx_major": 1.4, "fx_cross": 3.0}
#: IBKR per-order commission minimum, USD.
ORDER_MIN_USD = {"equity": 1.00, "crypto": 1.75, "fx_major": 2.00, "fx_cross": 2.00}

MAJORS = {"EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "USD_CAD", "AUD_USD", "NZD_USD"}
CRYPTO_TOK = ("BTC", "ETH", "LTC", "XRP", "ADA", "SOL", "DOGE")


def klass(sym: str) -> str:
    if any(t in sym for t in CRYPTO_TOK):
        return "crypto"
    if "_" in sym and len(sym) == 7:
        return "fx_major" if sym in MAJORS else "fx_cross"
    return "equity"


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
                closes[name] = df["close"]
            break
    close = pd.DataFrame(closes).sort_index()
    close = close.dropna(axis=1, thresh=int(len(close) * 0.6))
    scored = close.notna().sum(axis=1)
    keep = scored >= MIN_NAMES
    close = close.loc[keep.idxmax():]
    return close.loc[close.notna().sum(axis=1) >= MIN_NAMES]


def main() -> int:
    close = build()
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

    live = w.abs().sum(axis=1) > 0
    first = live.idxmax()
    w, rets = w.loc[first:], rets.loc[first:]

    dw = w.diff().abs().fillna(0.0)
    bps_vec = pd.Series({c: COST_BPS[klass(c)] for c in w.columns})
    min_vec = pd.Series({c: ORDER_MIN_USD[klass(c)] for c in w.columns})

    # 1. spread/slippage, per asset class, per unit turnover
    spread_cost = (dw * bps_vec / 1e4).sum(axis=1)

    # 2. per-ORDER commission minimums. An order fires whenever a weight changes
    #    materially. Cost is max(min_commission, ~0.05% of trade value) in USD, converted
    #    to a fraction of the GBP book.
    traded = dw > 1e-6
    notional_usd = dw * CAPITAL * GBPUSD
    # Broadcast the per-instrument minimum across dates before taking the max.
    min_frame = pd.DataFrame(
        np.tile(min_vec.reindex(notional_usd.columns).to_numpy(), (len(notional_usd), 1)),
        index=notional_usd.index, columns=notional_usd.columns,
    )
    per_order_usd = (notional_usd * 0.0005).clip(lower=min_frame).where(traded, 0.0)
    commission = per_order_usd.sum(axis=1) / GBPUSD / CAPITAL

    gross = (w.shift(1) * rets).sum(axis=1)
    net_screen = gross - (dw.sum(axis=1) * 2.0 / 1e4)          # what the screen charged
    net_real = gross - spread_cost - commission                 # what you would pay

    def rep(r, label):
        r = r.dropna()
        eq = (1 + r).cumprod()
        yrs = len(r) / 252
        cagr = float(eq.iloc[-1]) ** (1 / yrs) - 1
        vol_ = float(r.std(ddof=1) * np.sqrt(252))
        sh = float(r.mean() * 252) / vol_
        dd = float(abs(((eq - eq.cummax()) / eq.cummax()).min()))
        rng = np.random.default_rng(42)
        sim = np.cumprod(1 + rng.choice(r.to_numpy(), size=(20000, 252), replace=True), axis=1)
        pk = np.maximum.accumulate(sim, axis=1)
        fdd = ((pk - sim) / pk).max(axis=1)
        m = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        print(f"  {label:<26} CAGR {cagr*100:5.2f}%  £{cagr*CAPITAL/12:5.0f}/mo  "
              f"Sharpe {sh:5.3f}  maxDD {dd*100:5.2f}%  fwdP95 {np.percentile(fdd,95)*100:5.2f}%  "
              f"losing mo {float((m<0).mean())*100:4.1f}%")
        return cagr

    print("=" * 100)
    print(f"DEPLOYMENT ON £{CAPITAL:,.0f} — residual momentum top {TOP_N}, "
          f"{w.shape[1]} names, {len(w)} bars ({len(w)/252:.1f}y)")
    print("=" * 100)
    print("\nUNIVERSE (all reachable from a UK retail IBKR account)")
    ks = pd.Series({c: klass(c) for c in w.columns}).value_counts()
    for k, v in ks.items():
        print(f"  {k:<10} {v:3d} instruments   {COST_BPS[k]:.1f} bps/side, "
              f"${ORDER_MIN_USD[k]:.2f} order minimum")
    print(f"\n  £{CAPITAL:,.0f} / {TOP_N} positions = £{CAPITAL/TOP_N:,.0f} per position")

    print("\nWHAT THE COSTS DO")
    c0 = rep(gross, "gross (no costs)")
    c1 = rep(net_screen, "screen (flat 2bps)")
    c2 = rep(net_real, "REAL £100k deployment")

    ann_spread = float(spread_cost.mean() * 252)
    ann_comm = float(commission.mean() * 252)
    orders_yr = float(traded.sum(axis=1).sum()) / (len(w) / 252)
    print(f"\nCOST BREAKDOWN (annualised, as % of the £{CAPITAL:,.0f} book)")
    print(f"  spread + slippage           {ann_spread*100:5.2f}%/yr")
    print(f"  commission (order minimums) {ann_comm*100:5.2f}%/yr   "
          f"~{orders_yr:.0f} orders/yr, £{ann_comm*CAPITAL:,.0f}/yr")
    print(f"  TOTAL                       {(ann_spread+ann_comm)*100:5.2f}%/yr   "
          f"= £{(ann_spread+ann_comm)*CAPITAL:,.0f}/yr")
    print(f"  screen assumed              {(c0-c1)*100:5.2f}%/yr   -> understated by "
          f"£{((c1-c2))*CAPITAL:,.0f}/yr")

    print(f"\nBOTTOM LINE ON £{CAPITAL:,.0f}")
    print(f"  Screen said                 £{c1*CAPITAL/12:,.0f}/month")
    print(f"  Realistic deployment        £{c2*CAPITAL/12:,.0f}/month  "
          f"({(c2-c1)*CAPITAL/12:+,.0f}/mo vs screen)")
    print(f"  Your engine today (gated)   £413/month")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
