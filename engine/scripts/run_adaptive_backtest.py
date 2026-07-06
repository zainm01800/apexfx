"""Run an adaptive self-reflecting backtest across single or multiple instruments and styles.

Supports different styles (scalp, intraday, swing, position), maps them to their respective
timeframes (15m, 1h, 1d), adjusts lookbacks to find more opportunities, and clamps dates
automatically according to Yahoo Finance historical limitations.

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_adaptive_backtest.py --style scalp --instrument BTC/USD
    .venv\\Scripts\\python.exe scripts/run_adaptive_backtest.py --style intraday --instrument EUR/USD,GBP/USD
"""

from __future__ import annotations

import argparse
import sys
import json
import os
import warnings
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, clean, get_adapter
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.backtest.adaptive import AdaptiveWrapperStrategy
from apex_quant.backtest.engine import Backtester
from apex_quant.ai.client import AppAILLM, extract_json
from apex_quant.ml.dataset import compute_feature_frame

def calculate_portfolio_metrics(trades: list) -> dict:
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0}
    
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    win_rate = len(wins) / len(trades)
    net_pnl = sum(pnls)
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    
    return {
        "n_trades": len(trades),
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor if np.isfinite(profit_factor) else None,
    }

def main():
    parser = argparse.ArgumentParser(description="Adaptive Self-Reflecting Backtest Runner")
    parser.add_argument("--instrument", type=str, default="all", help="Instrument(s) to test (comma-separated list, or 'all')")
    parser.add_argument("--style", type=str, default="swing", choices=["scalp", "intraday", "swing", "position"], help="Trading style")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--rules", type=str, default="adaptive_rules.json", help="Filename to save generated rules")
    parser.add_argument("--max-rules", type=int, default=10, help="Max rules to generate")
    
    args = parser.parse_args()
    
    cfg = get_config()
    cfg.ai.enabled = True
    
    # 1. Map style to timeframe, holding horizon, and strategy parameters
    style_params = {
        "scalp": {
            "timeframe": "15m",
            "momentum_lookback": 14,
            "vol_window": 14,
            "holding_horizon": 20, # 20 bars of 15m = 5 hours
            "warmup": 40,
            "max_history_days": 59 # Yahoo 15m limit is 60 days
        },
        "intraday": {
            "timeframe": "1h",
            "momentum_lookback": 24,
            "vol_window": 24,
            "holding_horizon": 24, # 24 bars of 1h = 24 hours
            "warmup": 60,
            "max_history_days": 720 # Yahoo 1h limit is 730 days
        },
        "swing": {
            "timeframe": "1d",
            "momentum_lookback": 63,
            "vol_window": 63,
            "holding_horizon": 10, # 10 days
            "warmup": 100,
            "max_history_days": 10000
        },
        "position": {
            "timeframe": "1d",
            "momentum_lookback": 126,
            "vol_window": 126,
            "holding_horizon": 40, # 40 days
            "warmup": 150,
            "max_history_days": 10000
        }
    }
    
    params = style_params[args.style]
    timeframe = params["timeframe"]
    warmup = params["warmup"]
    
    # 2. Enforce Yahoo Finance API history limits to prevent server crashes
    now = datetime.utcnow()
    
    # Calculate fallback dates if not specified
    if args.start is None:
        if args.style == "scalp":
            start_dt = now - timedelta(days=20) # 20 days ago default for scalp
        elif args.style == "intraday":
            start_dt = now - timedelta(days=90) # 90 days ago default for intraday
        else:
            start_dt = datetime(2022, 1, 1)    # 3 years default for daily
        start_str = start_dt.strftime("%Y-%m-%d")
    else:
        start_str = args.start
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        
    if args.end is None:
        end_str = now.strftime("%Y-%m-%d")
    else:
        end_str = args.end
        
    # Clamp start date if it exceeds Yahoo's timeframe limits
    max_days = params["max_history_days"]
    earliest_allowed = now - timedelta(days=max_days)
    if start_dt < earliest_allowed:
        clamped_str = (earliest_allowed + timedelta(days=2)).strftime("%Y-%m-%d")
        print(f"[*] Warning: Yahoo Finance limits historical {timeframe} data to the last {max_days} days.")
        print(f"[*] Clamping start date from {start_str} to {clamped_str} to prevent API errors.\n")
        start_str = clamped_str
        
    # Override configuration timeframe
    cfg.data.timeframe = timeframe
    
    # Resolve target instruments list
    if args.instrument.lower() == "all":
        instruments = cfg.universe
    else:
        instruments = [i.strip() for i in args.instrument.split(",") if i.strip()]
        
    if not instruments:
        print("Error: No instruments found to backtest.")
        sys.exit(1)
        
    print(f"=== Portfolio Adaptive Backtest Run ===")
    print(f"Instruments ({len(instruments)}): {', '.join(instruments)}")
    print(f"Trading Style: {args.style.upper()} (Timeframe: {timeframe}, Holding: {params['holding_horizon']} bars)")
    print(f"Period: {start_str} to {end_str}")
    print(f"API Target: {cfg.ai.app_url} (useLocalLlm: {cfg.ai.use_local_llm}, model: {cfg.ai.local_llm_model})\n")
    
    adapter = get_adapter(cfg.data.provider)
    backtester = Backtester(cfg)
    llm = AppAILLM()
    
    # --------------------------------------------------------------------------
    # PASS 1: Blind run across all instruments
    # --------------------------------------------------------------------------
    print("--- Executing Pass 1 (Blind Run) ---")
    pass1_trades = []
    active_pits = {}
    
    for inst in instruments:
        try:
            # Override adapter parameter for Yahoo intraday retrieval
            df = clean(adapter.get_history(inst, start_str, end_str, timeframe=timeframe))
            if len(df) < warmup + params["momentum_lookback"] + 10:
                print(f"  [Skip] {inst}: insufficient data ({len(df)} bars, need at least {warmup + params['momentum_lookback'] + 10})")
                continue
            
            pit = PointInTimeAccessor(df)
            active_pits[inst] = pit
            
            # Setup base strategy calibrated for this specific style
            strat = RegimeGatedMomentum(
                momentum_lookback=params["momentum_lookback"],
                vol_window=params["vol_window"],
                holding_horizon=params["holding_horizon"],
                reward_risk=1.5,
                regime_method="rule_based"
            )
            strat.fit(pit, df.index)
            
            res = backtester.run(pit, strat, inst, start=start_str, end=end_str, warmup=warmup, max_hold=params["holding_horizon"])
            pass1_trades.extend(res.trades)
            print(f"  [Pass 1] {inst}: {len(res.trades)} trades, Win Rate: {res.metrics.get('win_rate', 0.0)*100:.1f}%")
        except Exception as e:
            print(f"  [Error] {inst}: {type(e).__name__}: {e}")
            
    if not pass1_trades:
        print("Error: No trades executed in Pass 1 across the portfolio.")
        sys.exit(1)
        
    p1_metrics = calculate_portfolio_metrics(pass1_trades)
    print(f"\nPass 1 Aggregated: {p1_metrics['n_trades']} trades, Win Rate: {p1_metrics['win_rate']*100:.1f}%, Net Return: ${p1_metrics['net_pnl']:.2f}")
    
    # --------------------------------------------------------------------------
    # AI CRITIC REFLECTION: Analyze aggregated portfolio losses
    # --------------------------------------------------------------------------
    losses = [t for t in pass1_trades if t.pnl < 0]
    rules = []
    
    if losses:
        # Sample up to 15 portfolio losses to represent main failure points
        np.random.seed(cfg.seed)
        sampled_losses = list(np.random.choice(losses, size=min(15, len(losses)), replace=False))
        
        reflected_trades = []
        for t in sampled_losses:
            pit = active_pits.get(t.instrument)
            if not pit:
                continue
            df_all = pit.as_of(pit.end)
            features_df = compute_feature_frame(df_all, cfg)
            ts = pd.Timestamp(t.entry_time)
            
            if ts in features_df.index:
                feat_row = features_df.loc[ts].to_dict()
                feat_str = ", ".join([f"{k}={v:.4f}" for k, v in feat_row.items() if np.isfinite(v)])
                reflected_trades.append({
                    "instrument": t.instrument,
                    "date": t.entry_time,
                    "direction": t.direction,
                    "exit_reason": t.exit_reason,
                    "pnl": t.pnl,
                    "indicators": feat_str
                })
                
        if reflected_trades:
            print(f"\nSending {len(reflected_trades)} portfolio-wide losing trades to AI Critic for post-mortem...")
            prompt = (
                f"Trading Style: {args.style.upper()} (Timeframe: {timeframe})\n"
                f"Aggregated Losing Trades Log:\n{json.dumps(reflected_trades, indent=2)}\n\n"
                f"Analyze these failures. Identify shared indicator values, volatility, or trend regimes "
                f"where the momentum model failed. Propose up to {args.max_rules} rules to filter out bad entries.\n"
                f"Rules must be simple and refer directly to indicator names in the logs (e.g. 'trend_slope_200', 'rvol_21', 'mom_vs_63').\n"
                f"Return ONLY a JSON array of strings containing the rules, for example:\n"
                f'["Avoid LONG when trend_slope_200 is negative", "Avoid SHORT when rvol_21 is low"]'
            )
            
            system_prompt = (
                "You are an expert portfolio risk manager. Analyze the trade failures and return "
                "a strict JSON array of simple rules to veto similar bad entries. Only return the JSON."
            )
            
            res_text = llm.complete(prompt, system_prompt, max_tokens=1000, temperature=0.2)
            extracted = extract_json(res_text)
            if isinstance(extracted, list):
                rules = [str(r) for r in extracted]
                print(f"AI Critic generated {len(rules)} adaptive rules:")
                for idx, r in enumerate(rules):
                    print(f"  {idx+1}. {r}")
            else:
                print("Failed to parse rules from AI Critic response.")
                
        # Save rules
        if rules:
            p = Path(args.rules)
            if not p.is_absolute():
                p = Path(cfg.data.store_dir) / p
            os.makedirs(p.parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=2)
            print(f"Saved master rules to: {p}")
            
    # --------------------------------------------------------------------------
    # PASS 2: Grounded run across all instruments using AI rules
    # --------------------------------------------------------------------------
    pass2_trades = []
    if rules:
        print("\n--- Executing Pass 2 (Grounded in Master Rules) ---")
        for inst in instruments:
            pit = active_pits.get(inst)
            if not pit:
                continue
            
            try:
                base_strat = RegimeGatedMomentum(
                    momentum_lookback=params["momentum_lookback"],
                    vol_window=params["vol_window"],
                    holding_horizon=params["holding_horizon"],
                    reward_risk=1.5,
                    regime_method="rule_based"
                )
                base_strat.fit(pit, pit.as_of(pit.end).index)
                
                wrapper_strat = AdaptiveWrapperStrategy(base_strat, rules, cfg.ai.app_url)
                res = backtester.run(pit, wrapper_strat, inst, start=start_str, end=end_str, warmup=warmup, max_hold=params["holding_horizon"])
                pass2_trades.extend(res.trades)
                print(f"  [Pass 2] {inst}: {len(res.trades)} trades, Win Rate: {res.metrics.get('win_rate', 0.0)*100:.1f}%")
            except Exception as e:
                print(f"  [Error Pass 2] {inst}: {type(e).__name__}: {e}")
                
    # --------------------------------------------------------------------------
    # REPORT PORTFOLIO COMPARISON
    # --------------------------------------------------------------------------
    print("\n" + "="*50)
    print("      AGGREGATED PORTFOLIO PERFORMANCE SUMMARY")
    print("="*50)
    
    p2_metrics = calculate_portfolio_metrics(pass2_trades) if pass2_trades else None
    
    def _fmt(val, is_pct=False, is_dec=False, is_pnl=False):
        if val is None:
            return "N/A"
        if is_pct:
            return f"{val * 100:.1f}%"
        if is_dec:
            return f"{val:.2f}"
        if is_pnl:
            return f"${val:.2f}"
        return str(val)

    print(f"{'Metric':<25} | {'Pass 1 (Blind)':<15} | {'Pass 2 (Adaptive)':<15}")
    print("-"*62)
    print(f"{'Total Trades':<25} | {_fmt(p1_metrics.get('n_trades')):<15} | {_fmt(p2_metrics.get('n_trades') if p2_metrics else None):<15}")
    print(f"{'Portfolio Win Rate':<25} | {_fmt(p1_metrics.get('win_rate'), True):<15} | {_fmt(p2_metrics.get('win_rate') if p2_metrics else None, True):<15}")
    print(f"{'Total Profit factor':<25} | {_fmt(p1_metrics.get('profit_factor'), False, True):<15} | {_fmt(p2_metrics.get('profit_factor') if p2_metrics else None, False, True):<15}")
    print(f"{'Aggregate Return':<25} | {_fmt(p1_metrics.get('net_pnl'), False, False, True):<15} | {_fmt(p2_metrics.get('net_pnl') if p2_metrics else None, False, False, True):<15}")
    print("="*62)

if __name__ == "__main__":
    main()
