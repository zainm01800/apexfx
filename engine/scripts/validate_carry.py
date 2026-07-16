#!/usr/bin/env python3
"""Run cross-sectional carry validation against the three-gate gauntlet."""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")

# Load .env file manually
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
from apex_quant.validation import TrialLedger
from apex_quant.validation.portfolio_report import run_portfolio_validation, _portfolio_returns, sharpe_ratio
from apex_quant.data.rates import CSVRateProvider
from apex_quant.strategies.carry import CrossSectionalCarry

# Initialize the shared rate provider
provider = CSVRateProvider()


def carry_factory(panel, **params):
    return CrossSectionalCarry(panel, provider, **params)


def carry_grid():
    """Param grid for sweeps."""
    return [
        {"long_frac": 0.30, "short_frac": 0.30},  # Baseline
        {"long_frac": 0.20, "short_frac": 0.20},
        {"long_frac": 0.40, "short_frac": 0.40},
        {"long_frac": 0.30, "short_frac": 0.00},  # Long-only
    ]


def main():
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    
    start_date = "2014-01-01"
    end_date = "2024-12-31"
    
    print(f"Loading data from {start_date} to {end_date}...")
    
    # 22 daily Forex currency pairs
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
    
    # Load / create trial ledger
    ledger_path = Path(__file__).resolve().parent.parent / "scratch/carry_ledger.json"
    ledger = TrialLedger.load(ledger_path)
    
    grid = carry_grid()
    
    # Record each parameter configuration's Sharpe to ledger first
    print("\nEvaluating and recording parameter grid to TrialLedger...")
    for params in grid:
        model = carry_factory(panel, **params)
        rets = _portfolio_returns(
            pits, model.strategies(), cfg=cfg, timeframes=None,
            warmup=250, periods_per_year=252, exit_mode="barrier"
        )
        sr = sharpe_ratio(rets, periods_per_year=1)
        ledger.record(params, sr)
        print(f"Recorded config: {params} | Sharpe: {sr:.4f}")
        
    ledger.save(ledger_path)
    print(f"Persisted TrialLedger to {ledger_path}. Total distinct trials: {ledger.n_trials}")
    
    print("\n--- Running 22-Pair Book Carry Cross-Sectional Validation ---")
    port_report = run_portfolio_validation(
        panel, pits, carry_factory, grid, strategy_name="cross_sectional_carry",
        generated_for=end_date, n_trials=ledger.n_trials, exit_mode="barrier"
    )
    
    print("\n" + "="*50)
    print("VALIDATION REPORT:")
    print("="*50)
    print(port_report.summary())
    print("="*50)


if __name__ == "__main__":
    main()
