from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")

# Load .env file manually before imports
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line_str = line.strip()
            if line_str and not line_str.startswith("#") and "=" in line_str:
                key, val = line_str.split("=", 1)
                os.environ[key] = val

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, clean, get_adapter
from apex_quant.validation import run_validation
from apex_quant.validation.portfolio_report import run_portfolio_validation
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.cross_sectional import CrossSectionalMomentum

def rgm_factory(**params):
    return RegimeGatedMomentum(**params)

def rgm_grid():
    return [
        {"momentum_lookback": 63, "vol_window": 63},
        {"momentum_lookback": 21, "vol_window": 21},
        {"momentum_lookback": 126, "vol_window": 126},
    ]

def cs_factory(panel, **params):
    return CrossSectionalMomentum(panel, **params)

def cs_grid():
    return [
        {"lookback": 21, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 6},
        {"lookback": 63, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 6},
        {"lookback": 126, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 6},
    ]

def main():
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    
    start_date = "2014-01-01"
    end_date = "2024-12-31"
    
    print(f"Loading data from {start_date} to {end_date}...")
    
    # EUR/USD daily data
    eurusd_df = clean(adapter.get_history("EUR/USD", start_date, end_date))
    eurusd_pit = PointInTimeAccessor(eurusd_df)
    
    # 22-pair panel daily data
    pairs = [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD",
        "GBP/JPY", "EUR/GBP", "EUR/JPY", "GBP/NZD", "AUD/NZD", "EUR/AUD", "EUR/CAD",
        "CHF/JPY", "GBP/CAD", "GBP/CHF", "GBP/AUD", "EUR/CHF", "EUR/NZD", "NZD/JPY",
        "CAD/JPY"
    ]
    
    panel = {}
    pits = {}
    for p in pairs:
        try:
            df = clean(adapter.get_history(p, start_date, end_date))
            if len(df) >= 300:
                panel[p] = df
                pits[p] = PointInTimeAccessor(df)
            else:
                print(f"Skipping {p} (insufficient bars: {len(df)})")
        except Exception as e:
            print(f"Failed to load {p}: {e}")
            
    print(f"Loaded {len(panel)} pairs for the cross-sectional panel.")
    
    print("\n--- Running EUR/USD (Regime-Gated Momentum) ---")
    print("Running EUR/USD under exit_mode=managed...")
    val_managed = run_validation(
        eurusd_pit, "EUR/USD", strategy_factory=rgm_factory, param_grid=rgm_grid(),
        generated_for=end_date, exit_mode="managed"
    )
    
    print("Running EUR/USD under exit_mode=barrier...")
    val_barrier = run_validation(
        eurusd_pit, "EUR/USD", strategy_factory=rgm_factory, param_grid=rgm_grid(),
        generated_for=end_date, exit_mode="barrier"
    )
    
    print("\n--- Running 22-Pair Book (Cross-Sectional Momentum) ---")
    print("Running 22-pair panel under exit_mode=managed...")
    port_managed = run_portfolio_validation(
        panel, pits, cs_factory, cs_grid(), strategy_name="cross_sectional_momentum",
        generated_for=end_date, exit_mode="managed"
    )
    
    print("Running 22-pair panel under exit_mode=barrier...")
    port_barrier = run_portfolio_validation(
        panel, pits, cs_factory, cs_grid(), strategy_name="cross_sectional_momentum",
        generated_for=end_date, exit_mode="barrier"
    )
    
    print("\n" + "="*50)
    print("RESULTS MATRIX:")
    print("="*50)
    print(f"EUR/USD - Managed: {val_managed.summary()} (Sharpe = {val_managed.dsr.get('observed_sharpe_ann', 0.0):.4f})")
    print(f"EUR/USD - Barrier: {val_barrier.summary()} (Sharpe = {val_barrier.barrier_sharpe if hasattr(val_barrier, 'barrier_sharpe') else val_barrier.dsr.get('observed_sharpe_ann', 0.0):.4f})")
    print(f"22-Pair - Managed: {port_managed.summary()} (Sharpe = {port_managed.dsr.get('observed_sharpe_ann', 0.0):.4f})")
    print(f"22-Pair - Barrier: {port_barrier.summary()} (Sharpe = {port_barrier.barrier_sharpe if hasattr(port_barrier, 'barrier_sharpe') else port_barrier.dsr.get('observed_sharpe_ann', 0.0):.4f})")
    print("="*50)

if __name__ == "__main__":
    main()
