"""Stage A: Correlation screen — intraday 1h Bollinger-band mean-reversion on FX
vs the daily trend book.

This script does NOT charge the trial ledger. It is a PRE-SCREEN to decide
whether building a full intraday MR sleeve is worth the ledger spend. If
|correlation| >= 0.3, the candidate is rejected before any trial is spent.

Signal logic (simple, point-in-time, no lookahead):
  On each 1h bar:
    z = (close − SMA_20h) / (BB_width_20h)
    If z < −2.0: enter LONG, target = SMA, stop = close − 1.5 × ATR_14h
    If z > +2.0: enter SHORT, target = SMA, stop = close + 1.5 × ATR_14h
    Exit: hit stop, hit target, or 8-bar time stop (8h max hold)

Daily P&L is the sum of intraday trade returns that close within each calendar day.
Correlation is computed on aligned daily returns (trend book vs MR sleeve).

FX pairs: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF, USD/CAD, NZD/USD
Timeframe: 1h bars, pre-2025-01-01
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

STORE = ENGINE_DIR / "data_store"
FX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD"]
HOLDOUT = pd.Timestamp("2025-01-01", tz="UTC")

# Bollinger Band parameters
BB_WINDOW = 20          # 20 × 1h = ~1 trading day
BB_STD_MULT = 2.0       # entry threshold: 2 standard deviations
ATR_WINDOW = 14          # 14h ATR for stop distance
STOP_ATR_MULT = 1.5      # stop = 1.5 × ATR
MAX_HOLD = 8             # 8 bars = 8h max hold
SPREAD_PIPS = 1.0        # cost: 1 pip spread (conservative for majors)
PIP_SIZE = 0.0001        # except JPY pairs


def load_1h(pair: str) -> pd.DataFrame:
    """Load 1h OHLCV for one FX pair, filter to iteration window."""
    p = STORE / f"{pair}_1h.parquet"
    df = pd.read_parquet(p)
    return df[df.index < HOLDOUT].copy()


def compute_atr(df: pd.DataFrame, window: int) -> pd.Series:
    """True Range → ATR (backward-looking)."""
    h = df["high"]
    l = df["low"]
    c_prev = df["close"].shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def simulate_mr_trades(df: pd.DataFrame, pair: str) -> list[dict]:
    """Run the simple BB mean-reversion strategy on 1h bars.
    
    Returns a list of trade dicts with entry/exit timestamps and returns.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    idx = df.index
    
    # Compute indicators (all backward-looking)
    sma = pd.Series(close, index=idx).rolling(BB_WINDOW).mean().values
    std = pd.Series(close, index=idx).rolling(BB_WINDOW).std(ddof=1).values
    atr = compute_atr(df, ATR_WINDOW).values
    
    pip = 0.01 if "JPY" in pair else PIP_SIZE
    spread_cost = SPREAD_PIPS * pip  # one-way cost
    
    trades = []
    in_trade = False
    entry_bar = 0
    entry_price = 0.0
    direction = 0  # +1 long, -1 short
    stop_price = 0.0
    target_price = 0.0
    
    for i in range(BB_WINDOW, len(close)):
        if np.isnan(sma[i]) or np.isnan(std[i]) or np.isnan(atr[i]) or std[i] == 0 or atr[i] == 0:
            continue
        
        if in_trade:
            # Check exit conditions on this bar
            bars_held = i - entry_bar
            hit_stop = False
            hit_target = False
            
            if direction == 1:  # long
                hit_stop = low[i] <= stop_price
                hit_target = high[i] >= target_price
            else:  # short
                hit_stop = high[i] >= stop_price
                hit_target = low[i] <= target_price
            
            time_stop = bars_held >= MAX_HOLD
            
            if hit_stop or hit_target or time_stop:
                # Exit at close of this bar (conservative)
                exit_price = close[i]
                if hit_stop:
                    exit_price = stop_price  # stopped out at stop price
                elif hit_target:
                    exit_price = target_price  # hit target
                
                raw_ret = direction * (exit_price - entry_price) / entry_price
                # Deduct spread on both entry and exit
                net_ret = raw_ret - 2 * spread_cost / entry_price
                
                trades.append({
                    "entry_time": idx[entry_bar],
                    "exit_time": idx[i],
                    "pair": pair,
                    "direction": direction,
                    "raw_return": raw_ret,
                    "net_return": net_ret,
                    "bars_held": bars_held,
                    "exit_type": "stop" if hit_stop else ("target" if hit_target else "time"),
                })
                in_trade = False
        
        if not in_trade:
            z = (close[i] - sma[i]) / std[i]
            
            if z < -BB_STD_MULT:
                # Long entry: price is below lower band
                in_trade = True
                entry_bar = i
                entry_price = close[i] + spread_cost  # buy at ask
                direction = 1
                stop_price = close[i] - STOP_ATR_MULT * atr[i]
                target_price = sma[i]  # mean-revert to SMA
            
            elif z > BB_STD_MULT:
                # Short entry: price is above upper band
                in_trade = True
                entry_bar = i
                entry_price = close[i] - spread_cost  # sell at bid
                direction = -1
                stop_price = close[i] + STOP_ATR_MULT * atr[i]
                target_price = sma[i]
    
    return trades


def trades_to_daily_returns(trades: list[dict]) -> pd.Series:
    """Convert trade list to a daily return series.
    
    Each trade's return is attributed to the day it EXITS (conservative —
    you don't know the P&L until the trade is closed).
    """
    if not trades:
        return pd.Series(dtype=float)
    
    df = pd.DataFrame(trades)
    df["exit_date"] = df["exit_time"].dt.normalize()
    daily = df.groupby("exit_date")["net_return"].sum()
    return daily


def get_trend_book_daily_returns() -> pd.Series:
    """Extract the trend book's daily equity returns from the existing gate JSON."""
    # Run a minimal backtest of the trend book to get its equity curve
    from apex_quant.config import get_config
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.data.store import ParquetStore
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    
    EQUITY_CORE = [
        "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
        "PLTR", "TSM", "NFLX", "UBER",
        "ISWD.L", "ISDU.L", "ISDE.L",
        "XLK", "XLE", "XBI", "SMH", "SOXX",
    ]
    GOLD_ETC = "SGLD.L"
    CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
              "ADA/USD", "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD"]
    FX_MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
    
    instruments = EQUITY_CORE + [GOLD_ETC] + CRYPTO + FX_MAJORS
    
    store = ParquetStore(str(STORE))
    bars = {}
    for inst in instruments:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    
    print(f"  Trend book: loaded {len(bars)} instruments with 1d data before {HOLDOUT.date()}")
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005  # best honest config
    cfg.risk.max_swing_slots = 10
    
    bt = PortfolioBacktester(
        cfg, slot_allocation="expected_value",
        exit_mode="managed", use_regime=True,
        vol_window=63, corr_window=63,
    )
    
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
    
    strats = {}
    pits = {}
    for inst, df in bars.items():
        pit = PointInTimeAccessor(df)
        base_strat = RegimeGatedMomentum(
            momentum_lookback=252, vol_window=63, holding_horizon=21,
            reward_risk=1.5, regime_method="rule_based", timeframe="1d",
            instrument=inst,
        )
        strat = MultiTimeframeMomentum(
            base_strategy=base_strat,
            htf_rule="1w",
            htf_ma_window=50,
            instrument=inst,
        )
        strats[inst] = strat
        pits[inst] = pit
    
    print("  Running trend book backtest (this takes a few minutes)...")
    from apex_quant.config import set_global_seeds
    set_global_seeds(42)
    result = bt.run(pits, strats)
    print(f"  Trend book: Sharpe={result.metrics.get('sharpe', 0):.3f}, "
          f"trades={result.metrics.get('n_trades', 0)}")
    
    return result.returns


def main():
    print("=" * 70)
    print("STAGE A: CORRELATION SCREEN — Intraday 1h Mean-Reversion vs Trend Book")
    print("=" * 70)
    print()
    
    # 1. Run intraday MR on all FX pairs
    all_trades = []
    for pair in FX_PAIRS:
        print(f"  Simulating MR trades on {pair}...")
        df = load_1h(pair)
        trades = simulate_mr_trades(df, pair)
        all_trades.extend(trades)
        
        if trades:
            rets = [t["net_return"] for t in trades]
            win_rate = sum(1 for r in rets if r > 0) / len(rets)
            print(f"    {len(trades)} trades, win rate {win_rate:.1%}, "
                  f"mean ret {np.mean(rets)*100:.3f}%, "
                  f"total {sum(rets)*100:.2f}%")
        else:
            print(f"    No trades")
    
    print(f"\n  Total MR trades across all pairs: {len(all_trades)}")
    
    if not all_trades:
        print("\n  VERDICT: No trades generated. Cannot compute correlation.")
        print("  Candidate REJECTED — no signal in the data.")
        return
    
    # 2. Convert to daily returns
    mr_daily = trades_to_daily_returns(all_trades)
    mr_daily.index = pd.DatetimeIndex(mr_daily.index)
    if mr_daily.index.tz is None:
        mr_daily.index = mr_daily.index.tz_localize("UTC")
    
    print(f"\n  MR daily returns: {len(mr_daily)} days with at least one trade")
    print(f"  MR annualised Sharpe (standalone): "
          f"{mr_daily.mean() / mr_daily.std() * np.sqrt(252):.3f}")
    
    # 3. Get trend book daily returns
    print("\n  Computing trend book daily returns...")
    trend_daily = get_trend_book_daily_returns()
    if trend_daily.index.tz is None:
        trend_daily.index = trend_daily.index.tz_localize("UTC")
    
    # 4. Align and compute correlation
    # MR only has returns on days with trades. Fill non-trade days with 0.
    combined = pd.DataFrame({
        "trend": trend_daily,
        "mr": mr_daily,
    })
    combined["mr"] = combined["mr"].fillna(0)  # no MR trade = zero return
    combined = combined.dropna(subset=["trend"])  # need trend data
    
    n_overlap = len(combined)
    corr = combined["trend"].corr(combined["mr"])
    
    print(f"\n{'=' * 70}")
    print(f"  CORRELATION RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overlapping days:     {n_overlap}")
    print(f"  Pearson correlation:  {corr:.4f}")
    print(f"  |r|:                  {abs(corr):.4f}")
    print()
    
    # Also compute rolling correlation (63-day window) to check stability
    roll_corr = combined["trend"].rolling(63).corr(combined["mr"])
    print(f"  Rolling 63-day corr:  min={roll_corr.min():.3f}, "
          f"max={roll_corr.max():.3f}, mean={roll_corr.mean():.3f}")
    
    # Summary stats for both
    for name, s in [("Trend book", combined["trend"]), ("MR sleeve", combined["mr"])]:
        ann_ret = s.mean() * 252
        ann_vol = s.std() * np.sqrt(252)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        print(f"\n  {name}:")
        print(f"    Ann return: {ann_ret*100:.2f}%")
        print(f"    Ann vol:    {ann_vol*100:.2f}%")
        print(f"    Sharpe:     {sharpe:.3f}")
    
    # Hypothetical combined (equal-vol weighted)
    w_mr = combined["trend"].std() / combined["mr"].std() if combined["mr"].std() > 0 else 0
    combined["combined"] = combined["trend"] + w_mr * combined["mr"]
    comb_sharpe = combined["combined"].mean() / combined["combined"].std() * np.sqrt(252) if combined["combined"].std() > 0 else 0
    print(f"\n  Combined (equal-vol weighted):")
    print(f"    Sharpe:     {comb_sharpe:.3f}")
    print(f"    Vol weight on MR: {w_mr:.2f}")
    
    print(f"\n{'=' * 70}")
    if abs(corr) < 0.3:
        print(f"  VERDICT: |r| = {abs(corr):.4f} < 0.30 — PASS correlation screen.")
        print(f"  Proceed to Stage B: write prereg and run full backtest.")
    else:
        print(f"  VERDICT: |r| = {abs(corr):.4f} >= 0.30 — FAIL correlation screen.")
        print(f"  Do NOT proceed. Move to next candidate.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
