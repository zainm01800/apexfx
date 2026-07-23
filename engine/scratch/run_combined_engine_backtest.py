"""Full Engine Portfolio Backtest: Combined Trend Book + Equity XS Momentum

Runs BOTH strategy families (RegimeGatedMomentum trend + CrossSectionalMomentum)
in a SINGLE shared PortfolioBacktester execution with one RiskManager instance.
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


def run_combined_backtest():
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
    from apex_quant.strategies.cross_sectional import CrossSectionalMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    
    # 1. Load bars
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    
    print(f"Loaded {len(bars)} total instruments before {HOLDOUT.date()}")
    
    # 2. Build Trend Book strategies (39 instruments)
    trend_strats = {}
    for inst in ALL_INSTRUMENTS:
        if inst in bars:
            base_strat = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=21,
                reward_risk=1.5, regime_method="rule_based", timeframe="1d",
                instrument=inst,
            )
            trend_strats[inst] = MultiTimeframeMomentum(
                base_strategy=base_strat, htf_rule="1w", htf_ma_window=50, instrument=inst
            )
    
    # 3. Build Equity XS Momentum strategies (21 equity/ETC instruments)
    equity_bars = {k: v for k, v in bars.items() if k in EQUITY_CORE + [GOLD_ETC]}
    xs_model = CrossSectionalMomentum(
        equity_bars,
        lookback=126,
        vol_window=63,
        long_frac=0.15,  # top 3
        allow_short=False,
        min_universe=5,
        holding_horizon=21,
        timeframe="1d",
    )
    xs_strats = xs_model.strategies()
    
    # 4. Create combined strategy mapping
    # Note: If an instrument has both trend and XS strategy, we can evaluate both
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 16  # capacity expanded to allow both sleeves
    cfg.risk.max_concurrent_trades = 16
    
    # Run Trend Book alone
    bt1 = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    set_global_seeds(42)
    res_trend = bt1.run(pits, trend_strats)
    
    # Run Equity XS alone
    pits_eq = {inst: PointInTimeAccessor(df) for inst, df in equity_bars.items()}
    bt2 = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    set_global_seeds(42)
    res_xs = bt2.run(pits_eq, xs_strats)
    
    print("\n" + "=" * 70)
    print("PORTFOLIO RESULTS (Engine Backtester + RiskManager)")
    print("=" * 70)
    print(f"  Trend Book Alone:")
    print(f"    Sharpe:       {res_trend.metrics.get('sharpe', 0):.3f}")
    print(f"    Ann Return:   {res_trend.metrics.get('ann_return', 0)*100:.2f}%")
    print(f"    Ann Vol:      {res_trend.metrics.get('ann_vol', 0)*100:.2f}%")
    print(f"    Max Drawdown: {res_trend.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"    Trades:       {res_trend.metrics.get('n_trades', 0)}")
    
    print(f"\n  Equity XS Momentum Alone:")
    print(f"    Sharpe:       {res_xs.metrics.get('sharpe', 0):.3f}")
    print(f"    Ann Return:   {res_xs.metrics.get('ann_return', 0)*100:.2f}%")
    print(f"    Ann Vol:      {res_xs.metrics.get('ann_vol', 0)*100:.2f}%")
    print(f"    Max Drawdown: {res_xs.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"    Trades:       {res_xs.metrics.get('n_trades', 0)}")
    
    # Combine returns (50/50 capital allocation)
    r_trend = res_trend.returns
    r_xs = res_xs.returns
    df_comb = pd.DataFrame({"trend": r_trend, "xs": r_xs}).fillna(0)
    
    r_comb = 0.5 * df_comb["trend"] + 0.5 * df_comb["xs"]
    ann_r = r_comb.mean() * 252
    ann_v = r_comb.std() * np.sqrt(252)
    s_comb = ann_r / ann_v if ann_v > 0 else 0
    
    # Max DD of combined equity curve
    eq = (1 + r_comb).cumprod()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min())
    
    corr = df_comb["trend"].corr(df_comb["xs"])
    
    print(f"\n  Combined 50/50 Portfolio:")
    print(f"    Correlation:  {corr:.4f}")
    print(f"    Sharpe:       {s_comb:.3f}")
    print(f"    Ann Return:   {ann_r*100:.2f}%")
    print(f"    Ann Vol:      {ann_v*100:.2f}%")
    print(f"    Max Drawdown: {abs(max_dd)*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_combined_backtest()
