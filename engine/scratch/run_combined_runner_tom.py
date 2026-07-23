"""Combined Backtest: Book Runner (Sharpe 1.002) + TOM Seasonality (r=0.20)

Evaluates the daily return series of:
  1. Book Runner (EV Slot Allocation, 0.5% risk/trade, uncapped trailing exit)
  2. Turn-of-Month (TOM) Seasonality on Equities/FX
  3. Combined Volatility-Weighted Portfolio
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

from scratch.correlation_screen_tom_seasonality import simulate_tom_sleeve
from scratch.run_runner_ev_test import ALL_INSTRUMENTS


def run_runner_daily_returns() -> pd.Series:
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    from apex_quant.risk.trade_manager import TradeManager
    
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    
    strats = {}
    pits = {}
    for inst, df in bars.items():
        pit = PointInTimeAccessor(df)
        base_strat = RegimeGatedMomentum(
            momentum_lookback=252, vol_window=63, holding_horizon=252,
            reward_risk=1.5, regime_method="rule_based", timeframe="1d",
            instrument=inst,
        )
        strat = MultiTimeframeMomentum(
            base_strategy=base_strat, htf_rule="1w", htf_ma_window=50, instrument=inst
        )
        strats[inst] = strat
        pits[inst] = pit
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    tm = TradeManager(runner_mode=True)
    bt = PortfolioBacktester(
        cfg, slot_allocation="expected_value", exit_mode="managed",
        trade_manager=tm, vol_window=63, corr_window=63,
    )
    
    set_global_seeds(42)
    result = bt.run(pits, strats)
    return result.returns


def main():
    print("=" * 70)
    print("COMBINED PORTFOLIO: Book Runner (Sharpe 1.002) + TOM Seasonality")
    print("=" * 70)
    
    print("  Running Book Runner backtest...")
    runner_rets = run_runner_daily_returns()
    if runner_rets.index.tz is None:
        runner_rets.index = runner_rets.index.tz_localize("UTC")
        
    print("  Running TOM Seasonality simulation...")
    tom_rets = simulate_tom_sleeve()
    if tom_rets.index.tz is None:
        tom_rets.index = tom_rets.index.tz_localize("UTC")
        
    df = pd.DataFrame({"runner": runner_rets, "tom": tom_rets}).dropna()
    corr = df["runner"].corr(df["tom"])
    
    print(f"\n{'=' * 70}")
    print(f"  RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overlapping days:     {len(df)}")
    print(f"  Pearson correlation:  {corr:.4f}")
    
    s_runner = df["runner"].mean() / df["runner"].std() * np.sqrt(252)
    s_tom = df["tom"].mean() / df["tom"].std() * np.sqrt(252)
    
    print(f"\n  Book Runner Alone:")
    print(f"    Ann Return: {df['runner'].mean()*252*100:.2f}%")
    print(f"    Ann Vol:    {df['runner'].std()*np.sqrt(252)*100:.2f}%")
    print(f"    Sharpe:     {s_runner:.3f}")
    
    print(f"\n  TOM Seasonality Alone:")
    print(f"    Ann Return: {df['tom'].mean()*252*100:.2f}%")
    print(f"    Ann Vol:    {df['tom'].std()*np.sqrt(252)*100:.2f}%")
    print(f"    Sharpe:     {s_tom:.3f}")
    
    # Grid of weights to find optimal allocation
    print(f"\n  Weight Optimization Grid (Runner : TOM):")
    best_sharpe = 0.0
    best_w = 0.0
    for w in np.linspace(0.0, 1.0, 21):
        port = w * df["runner"] + (1 - w) * df["tom"]
        ann_r = port.mean() * 252
        ann_v = port.std() * np.sqrt(252)
        sh = ann_r / ann_v if ann_v > 0 else 0
        if sh > best_sharpe:
            best_sharpe = sh
            best_w = w
        print(f"    Weight {w*100:3.0f}% Runner / {(1-w)*100:3.0f}% TOM -> Sharpe: {sh:.3f}, Return: {ann_r*100:.2f}%, Vol: {ann_v*100:.2f}%")
        
    print(f"\n  Optimal Allocation: {best_w*100:.0f}% Book Runner / {(1-best_w)*100:.0f}% TOM Seasonality -> Peak Sharpe: {best_sharpe:.3f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
