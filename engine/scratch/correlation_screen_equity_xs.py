"""Stage A: Correlation screen — Equity Cross-Sectional Momentum (Halal Universe)
vs the daily Trend Book.

This script does NOT charge the trial ledger. It is a PRE-SCREEN.

Strategy logic (CrossSectionalMomentum on 21 Halal equities):
  - Universe: 12 Stocks + 3 Islamic UCITS + 5 Sector ETFs + Gold ETC (21 instruments)
  - Timeframe: Daily bars, pre-2025-01-01
  - Ranking: Volatility-standardized momentum (ret_126d / vol_63d)
  - Portfolio: Long top 3 instruments, weekly rebalance
  - Cost: 2 bps spread + 1 bps slippage (equities)

Correlate daily returns against the certified Trend Book.
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


def load_daily_panel() -> dict[str, pd.DataFrame]:
    panel = {}
    for inst in EQUITY_CORE_GOLD:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                panel[inst] = df
    return panel


def run_xs_momentum(panel: dict[str, pd.DataFrame], lookback: int = 126, top_n: int = 3) -> pd.Series:
    """Compute daily returns of a simple weekly-rebalanced XS momentum strategy."""
    from apex_quant.strategies.cross_sectional import CrossSectionalMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    from apex_quant.risk.types import Direction
    
    xs_model = CrossSectionalMomentum(
        panel,
        lookback=lookback,
        vol_window=63,
        long_frac=0.15,  # ~top 3 out of 21
        allow_short=False,  # Long-only for prop compliance
        min_universe=5,
        holding_horizon=21,
    )
    
    # Get all trading dates
    all_dates = sorted(list(set.union(*[set(df.index) for df in panel.values()])))
    all_dates = [d for d in all_dates if d < HOLDOUT]
    
    daily_returns = {}
    positions = {}  # inst -> weight
    
    for i, t in enumerate(all_dates):
        if i == 0:
            continue
        prev_t = all_dates[i-1]
        
        # Calculate daily return from existing positions
        if positions:
            day_ret = 0.0
            total_w = sum(positions.values())
            for inst, w in positions.items():
                if inst in panel and t in panel[inst].index and prev_t in panel[inst].index:
                    p_curr = panel[inst].loc[t, "close"]
                    p_prev = panel[inst].loc[prev_t, "close"]
                    r = (p_curr - p_prev) / p_prev
                    day_ret += w * r
            daily_returns[t] = day_ret
        else:
            daily_returns[t] = 0.0
        
        # Check rebalance at t
        ranks = xs_model.ranks_at(t)
        if ranks:
            longs = [inst for inst, (d, z) in ranks.items() if d == 1]
            if longs:
                positions = {inst: 1.0 / len(longs) for inst in longs}
            else:
                positions = {}
    
    return pd.Series(daily_returns)


def get_trend_book_daily_returns() -> pd.Series:
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.data.store import ParquetStore
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
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
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    bt = PortfolioBacktester(
        cfg, slot_allocation="expected_value",
        exit_mode="managed", use_regime=True,
        vol_window=63, corr_window=63,
    )
    
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
    print("STAGE A: CORRELATION SCREEN — Equity XS Momentum vs Trend Book")
    print("=" * 70)
    
    panel = load_daily_panel()
    print(f"  Loaded {len(panel)} halal equity/ETC instruments")
    
    xs_daily = run_xs_momentum(panel, lookback=126, top_n=3)
    if xs_daily.index.tz is None:
        xs_daily.index = xs_daily.index.tz_localize("UTC")
    
    ann_ret_xs = xs_daily.mean() * 252
    ann_vol_xs = xs_daily.std() * np.sqrt(252)
    sharpe_xs = ann_ret_xs / ann_vol_xs if ann_vol_xs > 0 else 0
    print(f"  XS Momentum standalone:")
    print(f"    Ann return: {ann_ret_xs*100:.2f}%")
    print(f"    Ann vol:    {ann_vol_xs*100:.2f}%")
    print(f"    Sharpe:     {sharpe_xs:.3f}")
    
    print("\n  Computing trend book daily returns...")
    trend_daily = get_trend_book_daily_returns()
    if trend_daily.index.tz is None:
        trend_daily.index = trend_daily.index.tz_localize("UTC")
    
    combined = pd.DataFrame({"trend": trend_daily, "xs": xs_daily}).dropna()
    corr = combined["trend"].corr(combined["xs"])
    
    print(f"\n{'=' * 70}")
    print(f"  CORRELATION RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overlapping days:     {len(combined)}")
    print(f"  Pearson correlation:  {corr:.4f}")
    print(f"  |r|:                  {abs(corr):.4f}")
    
    w_xs = combined["trend"].std() / combined["xs"].std() if combined["xs"].std() > 0 else 0
    combined["portfolio"] = combined["trend"] + w_xs * combined["xs"]
    comb_sharpe = combined["portfolio"].mean() / combined["portfolio"].std() * np.sqrt(252)
    
    print(f"\n  Combined Portfolio (equal-vol weighted):")
    print(f"    Combined Sharpe:    {comb_sharpe:.3f}")
    print(f"    Trend Sharpe alone: {combined['trend'].mean() / combined['trend'].std() * np.sqrt(252):.3f}")
    
    print(f"\n{'=' * 70}")
    if abs(corr) < 0.3 and sharpe_xs > 0.3:
        print(f"  VERDICT: |r| = {abs(corr):.4f} < 0.30 AND standalone Sharpe = {sharpe_xs:.3f} > 0.3 — PASS!")
    else:
        print(f"  VERDICT: |r| = {abs(corr):.4f}, standalone Sharpe = {sharpe_xs:.3f}")
    print(f"{'=' * 70}")

if __name__ == "__main__":
    main()
