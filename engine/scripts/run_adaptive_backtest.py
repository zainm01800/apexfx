"""Run an adaptive self-reflecting backtest across single or multiple instruments and styles.

Supports testing a single instrument (or portfolio) across ALL styles (scalp, intraday,
swing, position) in a single run, printing a comparative summary report.

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_adaptive_backtest.py --style all --instrument BTC/USD
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

# Load .env variables manually from engine/.env
def load_local_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    os.environ[k] = v

load_local_env()

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

def run_style_backtest(style: str, instruments: list[str], start_val: str, end_val: str, rules_filename: str, max_rules: int, warmup_override: int | None = None) -> dict | None:
    cfg = get_config()
    
    style_params = {
        "scalp": {
            "timeframe": "15m",
            "momentum_lookback": 14,
            "vol_window": 14,
            "holding_horizon": 36, # shortened from 48 for faster trade exit/capital recycling
            "warmup": 70, # ma_window=50 + momentum_lookback=14 + buffer
            "max_history_days": 59,
            "atr_stop_mult": 2.5,  # optimized stop mult for active scalping
            "reward_risk": 1.5     # optimized R:R target for faster take-profits
        },
        "intraday": {
            "timeframe": "1h",
            "momentum_lookback": 24,
            "vol_window": 24,
            "holding_horizon": 72, # 3 days max hold
            "warmup": 80, # ma_window=50 + momentum_lookback=24 + buffer
            "max_history_days": 720,
            "atr_stop_mult": 2.5,  # optimized stop mult for intraday trading
            "reward_risk": 2.0     # 2.0 R:R for intraday
        },
        "swing": {
            "timeframe": "1d",
            "momentum_lookback": 63,
            "vol_window": 63,
            "holding_horizon": 10,
            "warmup": 120, # ma_window=50 + momentum_lookback=63 + buffer
            "max_history_days": 10000,
            "atr_stop_mult": 3.0,  # wider daily baseline stop
            "reward_risk": 2.0
        },
        "position": {
            "timeframe": "1d",
            "momentum_lookback": 126,
            "vol_window": 126,
            "holding_horizon": 40,
            "warmup": 180, # ma_window=50 + momentum_lookback=126 + buffer
            "max_history_days": 10000,
            "atr_stop_mult": 3.0,  # wider daily baseline stop
            "reward_risk": 2.0
        }
    }
    
    # Load Twelve Data key
    twelve_key = os.getenv("APEX_TWELVE_DATA_KEY")
    use_twelve = bool(twelve_key and twelve_key.strip())

    params = style_params[style]
    timeframe = params["timeframe"]
    warmup = warmup_override if warmup_override is not None else params["warmup"]
    
    now = datetime.utcnow()
    
    # Calculate start date
    if start_val is None:
        if use_twelve:
            # Twelve Data has deep history pager - default all to 1 year ago!
            start_dt = now - timedelta(days=365)
        else:
            if style == "scalp":
                start_dt = now - timedelta(days=20)
            elif style == "intraday":
                start_dt = now - timedelta(days=90)
            else:
                start_dt = datetime(2022, 1, 1)
        start_str = start_dt.strftime("%Y-%m-%d")
    else:
        start_str = start_val
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        
    end_str = end_val if end_val is not None else now.strftime("%Y-%m-%d")
    
    # Clamp start date for Yahoo Finance
    if not use_twelve:
        max_days = params["max_history_days"]
        earliest_allowed = now - timedelta(days=max_days)
        if start_dt < earliest_allowed:
            clamped_str = (earliest_allowed + timedelta(days=2)).strftime("%Y-%m-%d")
            print(f"[*] Warning [{style.upper()}]: Yahoo limits historical {timeframe} to last {max_days} days. Clamped to {clamped_str}.")
            start_str = clamped_str
            
    cfg.data.timeframe = timeframe
    
    if use_twelve:
        from apex_quant.data.twelve_data_adapter import TwelveDataAdapter
        adapter = TwelveDataAdapter(api_key=twelve_key.strip())
    else:
        adapter = get_adapter(cfg.data.provider)
        
    backtester = Backtester(cfg)
    llm = AppAILLM()
    
    print(f"\n--- [{style.upper()}] Executing Pass 1 (Blind Run) ---")
    pass1_trades = []
    active_pits = {}
    
    from apex_quant.data.store import ParquetStore
    store = ParquetStore()
    
    for idx_inst, inst in enumerate(instruments):
        try:
            # Check cache-miss before sleep
            cached = store.load(inst, timeframe)
            start_ts = pd.Timestamp(start_str, tz="UTC") if pd.Timestamp(start_str).tzinfo is None else pd.Timestamp(start_str)
            end_ts = pd.Timestamp(end_str, tz="UTC") if pd.Timestamp(end_str).tzinfo is None else pd.Timestamp(end_str)
            need_fetch = cached.empty or cached.index[0] > start_ts or cached.index[-1] < end_ts

            if need_fetch and use_twelve and idx_inst > 0:
                import time
                print("  [*] Fetching new history from Twelve Data - waiting 8 seconds to respect rate limits...")
                time.sleep(8.0)
                
            df = clean(store.get_or_fetch(inst, adapter, start_str, end_str, timeframe=timeframe))
            if len(df) < warmup + params["momentum_lookback"] + 10:
                continue
                
            pit = PointInTimeAccessor(df)
            active_pits[inst] = pit
            
            # Override risk parameters in config dynamically for this style
            cfg.risk.atr_stop_mult = params.get("atr_stop_mult", 3.0)

            strat = RegimeGatedMomentum(
                momentum_lookback=params["momentum_lookback"],
                vol_window=params["vol_window"],
                holding_horizon=params["holding_horizon"],
                reward_risk=params.get("reward_risk", 2.0),
                regime_method="rule_based",
                timeframe=timeframe,
                bypass_calibration=False,
                instrument=inst
            )
            strat.fit(pit, df.index)
            
            res = backtester.run(pit, strat, inst, start=start_str, end=end_str, warmup=warmup, max_hold=params["holding_horizon"])
            pass1_trades.extend(res.trades)
        except Exception as e:
            import traceback
            print(f"  [Error Pass 1] {inst}: {type(e).__name__}: {e}")
            traceback.print_exc()
            
    if not pass1_trades:
        print(f"  No trades executed for style {style.upper()}.")
        return None
        
    p1_metrics = calculate_portfolio_metrics(pass1_trades)
    
    # --------------------------------------------------------------------------
    # AI CRITIC REFLECTION
    # --------------------------------------------------------------------------
    losses = [t for t in pass1_trades if t.pnl < 0]
    rules = []
    
    if losses:
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
            if features_df.index.tz is not None:
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            else:
                ts = ts.tz_localize(None)
            
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
            print(f"  Sending {len(reflected_trades)} losing trades to AI Critic...")
            prompt = (
                f"Trading Style: {style.upper()} (Timeframe: {timeframe})\n"
                f"Aggregated Losing Trades Log:\n{json.dumps(reflected_trades, indent=2)}\n\n"
                f"Analyze these failures. Identify shared indicator values, volatility, or trend regimes "
                f"where the momentum model failed. Propose up to {max_rules} rules to filter out bad entries.\n"
                f"Rules must be simple and refer directly to indicator names in the logs.\n"
                f"Return ONLY a JSON array of strings containing the rules."
            )
            
            system_prompt = (
                "You are an expert portfolio risk manager. Analyze the trade failures and return "
                "a strict JSON array of simple rules to veto similar bad entries. Only return the JSON."
            )
            
            res_text = llm.complete(prompt, system_prompt, max_tokens=1000, temperature=0.2)
            extracted = extract_json(res_text)
            if isinstance(extracted, list):
                rules = [str(r) for r in extracted]
                print(f"  AI Critic generated {len(rules)} adaptive rules.")
                
        if rules:
            # Resolve unique rules path based on style
            base, ext = os.path.splitext(rules_filename)
            unique_rules_path = f"{base}_{style}{ext}"
            p = Path(unique_rules_path)
            if not p.is_absolute():
                p = Path(cfg.data.store_dir) / p
            os.makedirs(p.parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=2)
                
    # --------------------------------------------------------------------------
    # PASS 2: Grounded run using AI rules
    # --------------------------------------------------------------------------
    pass2_trades = []
    if rules:
        print(f"--- [{style.upper()}] Executing Pass 2 (Grounded Run) ---")
        for inst in instruments:
            pit = active_pits.get(inst)
            if not pit:
                continue
            try:
                # Override risk parameters in config dynamically for this style
                cfg.risk.atr_stop_mult = params.get("atr_stop_mult", 3.0)

                base_strat = RegimeGatedMomentum(
                    momentum_lookback=params["momentum_lookback"],
                    vol_window=params["vol_window"],
                    holding_horizon=params["holding_horizon"],
                    reward_risk=params.get("reward_risk", 2.0),
                    regime_method="rule_based",
                    timeframe=timeframe,
                    bypass_calibration=False,
                    instrument=inst
                )
                base_strat.fit(pit, pit.as_of(pit.end).index)
                
                wrapper_strat = AdaptiveWrapperStrategy(base_strat, rules, cfg.ai.app_url)
                res = backtester.run(pit, wrapper_strat, inst, start=start_str, end=end_str, warmup=warmup, max_hold=params["holding_horizon"])
                pass2_trades.extend(res.trades)
            except Exception as e:
                import traceback
                print(f"  [Error Pass 2] {inst}: {type(e).__name__}: {e}")
                traceback.print_exc()
                
    p2_metrics = calculate_portfolio_metrics(pass2_trades) if pass2_trades else None
    
    return {
        "style": style.upper(),
        "timeframe": timeframe,
        "p1": p1_metrics,
        "p2": p2_metrics,
        "rules_count": len(rules)
    }

def main():
    parser = argparse.ArgumentParser(description="Adaptive Self-Reflecting Backtest Runner")
    parser.add_argument("--instrument", type=str, default="all", help="Instrument(s) to test (comma-separated list, or 'all')")
    parser.add_argument("--style", type=str, default="swing", choices=["scalp", "intraday", "swing", "position", "all"], help="Trading style, or 'all' to run all styles sequentially")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--rules", type=str, default="adaptive_rules.json", help="Base filename to save rules")
    parser.add_argument("--max-rules", type=int, default=10, help="Max rules to generate")
    
    args = parser.parse_args()
    
    cfg = get_config()
    cfg.ai.enabled = True
    
    # Resolve target instruments list
    if args.instrument.lower() == "all":
        instruments = cfg.universe
    else:
        instruments = [i.strip() for i in args.instrument.split(",") if i.strip()]
        
    if not instruments:
        print("Error: No instruments found to backtest.")
        sys.exit(1)
        
    target_styles = ["scalp", "intraday", "swing", "position"] if args.style.lower() == "all" else [args.style]
    
    print(f"=== Adaptive Backtester CLI Runner ===")
    print(f"Instruments: {', '.join(instruments)}")
    print(f"Target Styles: {', '.join([s.upper() for s in target_styles])}")
    twelve_key = os.getenv("APEX_TWELVE_DATA_KEY")
    if twelve_key and twelve_key.strip():
        print(f"[*] Grounding data adapter in Twelve Data (Key: {twelve_key[:6]}...)")
    else:
        print("[*] Grounding data adapter in Yahoo Finance (No Twelve Data key detected)")
    print(f"API Target: {cfg.ai.app_url} (useLocalLlm: {cfg.ai.use_local_llm}, model: {cfg.ai.local_llm_model})\n")
    
    style_reports = []
    for s in target_styles:
        rep = run_style_backtest(s, instruments, args.start, args.end, args.rules, args.max_rules)
        if rep:
            style_reports.append(rep)
            
    # Print Master Summary Report
    print("\n" + "="*80)
    print("                      CONSOLIDATED STYLE COMPARISON SUMMARY")
    print("="*80)
    
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

    # Header
    print(f"{'Style':<10} | {'TF':<5} | {'P1 Trades':<10} | {'P1 WinRate':<10} | {'P1 Return':<10} || {'P2 Trades':<10} | {'P2 WinRate':<10} | {'P2 Return':<10}")
    print("-" * 80)
    
    for r in style_reports:
        style = r["style"]
        tf = r["timeframe"]
        p1 = r["p1"]
        p2 = r["p2"] if r["p2"] else {}
        
        print(f"{style:<10} | {tf:<5} | "
              f"{_fmt(p1.get('n_trades')):<10} | "
              f"{_fmt(p1.get('win_rate'), True):<10} | "
              f"{_fmt(p1.get('net_pnl'), False, False, True):<10} || "
              f"{_fmt(p2.get('n_trades')):<10} | "
              f"{_fmt(p2.get('win_rate'), True):<10} | "
              f"{_fmt(p2.get('net_pnl'), False, False, True):<10}")
              
    print("="*80)

if __name__ == "__main__":
    main()
