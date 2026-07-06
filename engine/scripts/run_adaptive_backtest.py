"""Run an adaptive self-reflecting backtest.

Pass 1 executes a blind backtest. The AI Critic analyzes losing trades
and proposes up to 10 strict trading rules. Pass 2 re-runs the backtest
by routing candidate entries through these rules.

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_adaptive_backtest.py --instrument EUR/USD --start 2022-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, clean, get_adapter
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.backtest.adaptive import AdaptiveBacktester

def main():
    parser = argparse.ArgumentParser(description="Adaptive Self-Reflecting Backtest Runner")
    parser.add_argument("--instrument", type=str, default="EUR/USD", help="Instrument (e.g. EUR/USD, AAPL)")
    parser.add_argument("--start", type=str, default="2022-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--rules", type=str, default="adaptive_rules.json", help="Filename to save generated rules")
    parser.add_argument("--warmup", type=int, default=100, help="Bar warmup count")
    
    args = parser.parse_args()
    
    cfg = get_config()
    # Temporarily enable AI configuration in memory for the run
    cfg.ai.enabled = True
    
    print(f"=== Adaptive Backtest Run for {args.instrument} ===")
    print(f"Period: {args.start} to {args.end}")
    print(f"API Target: {cfg.ai.app_url} (useLocalLlm: {cfg.ai.use_local_llm}, model: {cfg.ai.local_llm_model})\n")
    
    # 1. Fetch data
    adapter = get_adapter(cfg.data.provider)
    try:
        df = clean(adapter.get_history(args.instrument, args.start, args.end))
        if len(df) < 150:
            print(f"Error: insufficient data bars found ({len(df)})")
            sys.exit(1)
        pit = PointInTimeAccessor(df)
    except Exception as e:
        print(f"Error loading data for {args.instrument}: {type(e).__name__}: {e}")
        sys.exit(1)
        
    print(f"Loaded {len(df)} price bars. Calibrating strategy...")
    
    # 2. Setup strategy
    strat = RegimeGatedMomentum()
    # Fit the strategy on the full available dataset (baseline momentum is stateless or fits calibrator)
    strat.fit(pit, df.index)
    
    # 3. Execute adaptive backtest
    backtester = AdaptiveBacktester(cfg)
    res1, res2, rules = backtester.run_with_reflection(
        pit,
        strat,
        args.instrument,
        start=args.start,
        end=args.end,
        warmup=args.warmup,
        rules_path=args.rules
    )
    
    # 4. Print comparison table
    print("\n" + "="*50)
    print("           BACKTEST RESULTS COMPARISON")
    print("="*50)
    
    m1 = res1.metrics
    m2 = res2.metrics if res2 else None
    
    def _fmt(val, is_pct=False, is_dec=False):
        if val is None:
            return "N/A"
        if is_pct:
            return f"{val * 100:.2f}%"
        if is_dec:
            return f"{val:.2f}"
        return str(val)

    print(f"{'Metric':<25} | {'Pass 1 (Blind)':<15} | {'Pass 2 (Adaptive)':<15}")
    print("-"*62)
    print(f"{'Total Return':<25} | {_fmt(m1.get('total_return'), True):<15} | {_fmt(m2.get('total_return') if m2 else None, True):<15}")
    print(f"{'Ann. Return':<25} | {_fmt(m1.get('ann_return'), True):<15} | {_fmt(m2.get('ann_return') if m2 else None, True):<15}")
    print(f"{'Ann. Volatility':<25} | {_fmt(m1.get('ann_vol'), True):<15} | {_fmt(m2.get('ann_vol') if m2 else None, True):<15}")
    print(f"{'Sharpe Ratio':<25} | {_fmt(m1.get('sharpe'), False, True):<15} | {_fmt(m2.get('sharpe') if m2 else None, False, True):<15}")
    print(f"{'Max Drawdown':<25} | {_fmt(m1.get('max_drawdown'), True):<15} | {_fmt(m2.get('max_drawdown') if m2 else None, True):<15}")
    print(f"{'Total Trades':<25} | {_fmt(m1.get('n_trades')):<15} | {_fmt(m2.get('n_trades') if m2 else None):<15}")
    print(f"{'Win Rate':<25} | {_fmt(m1.get('win_rate'), True):<15} | {_fmt(m2.get('win_rate') if m2 else None, True):<15}")
    print(f"{'Profit Factor':<25} | {_fmt(m1.get('profit_factor'), False, True):<15} | {_fmt(m2.get('profit_factor') if m2 else None, False, True):<15}")
    print("="*62)

    if rules:
        print(f"\nRules applied in Pass 2:")
        for idx, rule in enumerate(rules):
            print(f"  [{idx+1}] {rule}")
    else:
        print("\nNo rules were generated (no losses to inspect or model returned empty).")

if __name__ == "__main__":
    main()
