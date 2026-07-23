"""Test 4-Sleeve Diversified Portfolio on 1 Single £100k Account.

Goal: £700 - £1000 / month WITH Max DD <= 10.5% on 1 Single £100k Account.

Architecture:
  Sleeve 1: Book Runner Trend (45% weight) @ 0.85% risk
  Sleeve 2: Equity XS Momentum (25% weight) @ 0.60% risk
  Sleeve 3: TOM Seasonality (15% weight) @ 0.50% risk
  Sleeve 4: Crypto XS Momentum (15% weight) @ 0.40% risk
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

from scratch.run_runner_ev_test import ALL_INSTRUMENTS
from apex_quant.config import get_config, set_global_seeds
from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
from apex_quant.strategies.cross_sectional import CrossSectionalMomentum
from apex_quant.strategies.crypto_xs_momentum import CryptoXsMomentum
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.trade_manager import TradeManager
from scratch.correlation_screen_tom_seasonality import simulate_tom_sleeve

EQUITY_CORE = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
    "PLTR", "TSM", "NFLX", "UBER", "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX",
]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
          "ADA/USD", "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD"]


def load_bars():
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    return bars


def run_4sleeve_backtest():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("4-SLEEVE DIVERSIFIED PORTFOLIO ON 1 SINGLE £100K ACCOUNT")
    print("=" * 70)
    
    # 1. Sleeve 1: Trend Book Runner @ 0.85% risk
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.0085
    cfg.risk.max_swing_slots = 10
    cfg.risk.max_concurrent_trades = 10
    
    tm = TradeManager(runner_mode=True)
    strats = {}
    for inst, df in bars.items():
        pit = PointInTimeAccessor(df)
        b = RegimeGatedMomentum(
            momentum_lookback=252, vol_window=63, holding_horizon=252,
            reward_risk=1.5, regime_method="rule_based", timeframe="1d",
            instrument=inst,
        )
        strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
        
    bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", trade_manager=tm, vol_window=63, corr_window=63)
    set_global_seeds(42)
    res_trend = bt.run(pits, strats)
    r_trend = res_trend.returns
    if r_trend.index.tz is None: r_trend.index = r_trend.index.tz_localize("UTC")
    
    # 2. Sleeve 2: Equity XS Momentum
    eq_bars = {k: v for k, v in bars.items() if k in EQUITY_CORE}
    eq_model = CrossSectionalMomentum(eq_bars, lookback=126, vol_window=63, long_frac=0.30, allow_short=False)
    eq_strats = eq_model.strategies()
    pits_eq = {k: PointInTimeAccessor(v) for k, v in eq_bars.items()}
    bt_eq = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    set_global_seeds(42)
    res_eq = bt_eq.run(pits_eq, eq_strats)
    r_eq = res_eq.returns
    if r_eq.index.tz is None: r_eq.index = r_eq.index.tz_localize("UTC")
    
    # 3. Sleeve 3: TOM Seasonality
    r_tom = simulate_tom_sleeve()
    if r_tom.index.tz is None: r_tom.index = r_tom.index.tz_localize("UTC")
    
    # 4. Sleeve 4: Crypto XS Momentum
    crypto_bars = {k: v for k, v in bars.items() if k in CRYPTO}
    crypto_model = CryptoXsMomentum(crypto_bars, lookback=21, vol_window=63, top_n=3, min_universe=4, min_history=300, regime_filter=True)
    crypto_strats = crypto_model.strategies()
    pits_crypto = {k: PointInTimeAccessor(v) for k, v in crypto_bars.items()}
    bt_crypto = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
    set_global_seeds(42)
    res_crypto = bt_crypto.run(pits_crypto, crypto_strats)
    r_crypto = res_crypto.returns
    if r_crypto.index.tz is None: r_crypto.index = r_crypto.index.tz_localize("UTC")
    
    # Align all 4 sleeves
    df_comb = pd.DataFrame({
        "trend": r_trend,
        "eq_xs": r_eq,
        "tom": r_tom,
        "crypto_xs": r_crypto,
    }).fillna(0)
    
    for w_trend, w_eq, w_tom, w_crypto in [
        (0.50, 0.20, 0.15, 0.15),
        (0.55, 0.20, 0.15, 0.10),
        (0.60, 0.20, 0.10, 0.10),
    ]:
        p_ret = w_trend * df_comb["trend"] + w_eq * df_comb["eq_xs"] + w_tom * df_comb["tom"] + w_crypto * df_comb["crypto_xs"]
        r = p_ret.to_numpy()
        ann_r = float(r.mean() * 252)
        monthly_ret_pct = ann_r / 12
        monthly_gbp = 100000 * monthly_ret_pct
        
        eq = (1 + p_ret).cumprod()
        peak = eq.cummax()
        dd = (eq - peak) / peak
        max_dd = float(abs(dd.min()))
        
        ann_vol = float(r.std(ddof=1) * np.sqrt(252))
        sh = float(ann_r / ann_vol if ann_vol > 0 else 0)
        
        print(f"Weights ({w_trend*100:.0f}% Trend / {w_eq*100:.0f}% Eq / {w_tom*100:.0f}% TOM / {w_crypto*100:.0f}% Crypto):")
        print(f"  Sharpe Ratio:       {sh:.3f}")
        print(f"  Monthly Profit:     £{monthly_gbp:.2f} / month ({monthly_ret_pct*100:.2f}%/mo)")
        print(f"  Annual Return:      {ann_r*100:.2f}%")
        print(f"  Max Drawdown:       {max_dd*100:.2f}%")
        print("-" * 50)
    print("=" * 70)


if __name__ == "__main__":
    run_4sleeve_backtest()
