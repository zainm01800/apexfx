"""Stage A: Correlation Screen — Calendar & Seasonality Effects vs Trend Book

This script does NOT charge the trial ledger. It is a PRE-SCREEN.

Hypothesis:
  1. Turn-of-Month (TOM) Effect: Equities and FX tend to show positive drift
     during the last 1 trading day and first 3 trading days of each calendar month
     (asset allocation rebalancing inflows).
  2. Day-of-Week Effect: Friday close to Monday open gap / momentum.

Evaluates standalone profitability and daily return correlation vs the Trend Book.
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

EQUITY_CORE_GOLD = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
    "PLTR", "TSM", "NFLX", "UBER",
    "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX", "SGLD.L"
]

FX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD"]


def simulate_tom_sleeve() -> pd.Series:
    """Simulate Turn-of-Month (TOM) drift strategy on Halal Equities + FX."""
    daily_returns = {}
    
    # Load all equity 1d bars
    for inst in EQUITY_CORE_GOLD:
        p = STORE / f"{inst.replace('/', '_')}_1d.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df = df[df.index < HOLDOUT]
        if len(df) < 252:
            continue
        
        # Calculate trading day of month for each bar
        df["month_year"] = df.index.to_period("M")
        df["day_in_month"] = df.groupby("month_year").cumcount() + 1
        df["days_in_month"] = df.groupby("month_year")["close"].transform("count")
        df["rev_day_in_month"] = df["days_in_month"] - df["day_in_month"] + 1
        
        # TOM condition: Last 1 day of month OR First 3 days of month
        tom_mask = (df["rev_day_in_month"] <= 1) | (df["day_in_month"] <= 3)
        
        # Calculate daily return
        rets = df["close"].pct_change()
        tom_rets = rets.where(tom_mask, 0.0)
        
        for dt, r in tom_rets.dropna().items():
            daily_returns[dt] = daily_returns.get(dt, 0.0) + r / len(EQUITY_CORE_GOLD)
            
    return pd.Series(daily_returns).sort_index()


def get_trend_book_daily_returns() -> pd.Series:
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.data.store import ParquetStore
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    
    ALL_INSTRUMENTS = EQUITY_CORE_GOLD + [
        "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
        "ADA/USD", "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD",
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"
    ]
    
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    
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
            base_strategy=base_strat, htf_rule="1w", htf_ma_window=50, instrument=inst
        )
        strats[inst] = strat
        pits[inst] = pit
    
    set_global_seeds(42)
    result = bt.run(pits, strats)
    return result.returns


def main():
    print("=" * 70)
    print("STAGE A: CORRELATION SCREEN — Turn-of-Month (TOM) Seasonality vs Trend Book")
    print("=" * 70)
    
    tom_series = simulate_tom_sleeve()
    if tom_series.index.tz is None:
        tom_series.index = tom_series.index.tz_localize("UTC")
        
    ann_ret = tom_series.mean() * 252
    ann_vol = tom_series.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    
    print(f"  Turn-of-Month Seasonality Standalone:")
    print(f"    Ann Return: {ann_ret*100:.2f}%")
    print(f"    Ann Vol:    {ann_vol*100:.2f}%")
    print(f"    Sharpe:     {sharpe:.3f}")
    
    print("\n  Computing Trend Book daily returns...")
    trend_series = get_trend_book_daily_returns()
    if trend_series.index.tz is None:
        trend_series.index = trend_series.index.tz_localize("UTC")
        
    df = pd.DataFrame({"trend": trend_series, "tom": tom_series}).dropna()
    corr = df["trend"].corr(df["tom"])
    
    print(f"\n{'=' * 70}")
    print(f"  CORRELATION RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overlapping days:     {len(df)}")
    print(f"  Pearson correlation:  {corr:.4f}")
    print(f"  |r|:                  {abs(corr):.4f}")
    
    w_tom = df["trend"].std() / df["tom"].std() if df["tom"].std() > 0 else 0
    df["comb"] = df["trend"] + w_tom * df["tom"]
    comb_sharpe = df["comb"].mean() / df["comb"].std() * np.sqrt(252)
    
    print(f"\n  Combined Portfolio:")
    print(f"    Combined Sharpe:    {comb_sharpe:.3f}")
    print(f"    Trend Sharpe alone: {df['trend'].mean() / df['trend'].std() * np.sqrt(252):.3f}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
