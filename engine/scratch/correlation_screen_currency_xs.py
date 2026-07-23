"""Stage A: Correlation Screen — Currency-Leg Cross-Sectional Momentum (22 FX pairs)
vs the daily Trend Book.

This script does NOT charge the trial ledger. It is a PRE-SCREEN.

Evaluates CurrencyCrossSectionalMomentum (decomposing 22 FX pairs into 8 currency legs)
for:
  1. Standalone profitability and Sharpe.
  2. Daily return correlation vs the certified Trend Book.
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

FX_22_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD",
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/NZD", "CAD/JPY", "CHF/JPY",
    "EUR/AUD", "EUR/CAD", "EUR/CHF", "EUR/NZD", "GBP/AUD", "GBP/CAD",
    "GBP/CHF", "GBP/NZD", "NZD/JPY"
]


def load_fx_panel() -> dict[str, pd.DataFrame]:
    panel = {}
    for pair in FX_22_PAIRS:
        key = pair.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                panel[pair] = df
    return panel


def run_currency_xs_backtest():
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum, CurrencyCrossSectionalMomentumStrategy
    from apex_quant.data.point_in_time import PointInTimeAccessor
    
    panel = load_fx_panel()
    print(f"  Loaded {len(panel)} FX pairs before {HOLDOUT.date()}")
    
    model = CurrencyCrossSectionalMomentum(
        panel,
        lookback=63,
        vol_window=63,
        k=2,            # top 2 vs bottom 2 currencies
        min_universe=6,
        allow_short=True,  # FX is symmetric and allowed on prop accounts
        holding_horizon=21,
        timeframe="1d",
    )
    
    strats = model.strategies()
    pits = {inst: PointInTimeAccessor(df) for inst, df in panel.items()}
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    set_global_seeds(42)
    result = bt.run(pits, strats)
    
    print("\n" + "=" * 70)
    print("STANDALONE CURRENCY-LEG XS MOMENTUM (Engine Backtester + RiskManager)")
    print("=" * 70)
    print(f"  Sharpe:       {result.metrics.get('sharpe', 0):.3f}")
    print(f"  Ann return:   {result.metrics.get('ann_return', 0)*100:.2f}%")
    print(f"  Ann vol:      {result.metrics.get('ann_vol', 0)*100:.2f}%")
    print(f"  Max drawdown: {result.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"  Trades:       {result.metrics.get('n_trades', 0)}")
    print(f"  Win rate:     {result.metrics.get('win_rate', 0)*100:.1f}%")
    print(f"  Profit factor:{result.metrics.get('profit_factor', 0):.2f}")
    
    return result.returns


def get_trend_book_daily_returns() -> pd.Series:
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
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
    
    ALL_INSTRUMENTS = EQUITY_CORE + [GOLD_ETC] + CRYPTO + FX_MAJORS
    
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
    print("STAGE A: CORRELATION SCREEN — Currency-Leg XS Momentum vs Trend Book")
    print("=" * 70)
    
    c_returns = run_currency_xs_backtest()
    if c_returns.index.tz is None:
        c_returns.index = c_returns.index.tz_localize("UTC")
        
    print("\n  Computing Trend Book daily returns...")
    t_returns = get_trend_book_daily_returns()
    if t_returns.index.tz is None:
        t_returns.index = t_returns.index.tz_localize("UTC")
        
    df = pd.DataFrame({"trend": t_returns, "currency_xs": c_returns}).dropna()
    corr = df["trend"].corr(df["currency_xs"])
    
    print(f"\n{'=' * 70}")
    print(f"  CORRELATION RESULTS")
    print(f"{'=' * 70}")
    print(f"  Overlapping days:     {len(df)}")
    print(f"  Pearson correlation:  {corr:.4f}")
    print(f"  |r|:                  {abs(corr):.4f}")
    
    w = df["trend"].std() / df["currency_xs"].std() if df["currency_xs"].std() > 0 else 0
    df["comb"] = df["trend"] + w * df["currency_xs"]
    comb_sharpe = df["comb"].mean() / df["comb"].std() * np.sqrt(252)
    
    print(f"\n  Combined 50/50 Vol-Weighted Portfolio:")
    print(f"    Combined Sharpe:    {comb_sharpe:.3f}")
    print(f"    Trend Sharpe alone: {df['trend'].mean() / df['trend'].std() * np.sqrt(252):.3f}")
    print(f"    Currency XS alone:  {df['currency_xs'].mean() / df['currency_xs'].std() * np.sqrt(252):.3f}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
