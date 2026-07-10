import os
import sys
import json
import argparse
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Add engine directory to sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

# Load .env
load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, clean, get_adapter
from apex_quant.data.store import ParquetStore
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.backtest.engine import Backtester
from apex_quant.ai.client import DeepSeekLLM
from apex_quant.ml.dataset import compute_feature_frame

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

def fetch_lessons_pool():
    print("Loading AI lessons pool from Supabase...")
    try:
        url = f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit,expired,invalidated)&limit=1000"
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            trades = r.json()
            pool = [t for t in trades if t.get("lesson")]
            print(f"✓ Loaded {len(pool)} lessons from Supabase memory.")
            return pool
        print(f"[WARN] Failed to load lessons: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[WARN] Connection error fetching lessons: {e}")
    return []

def get_similar_lessons(symbol, verdict, pool, limit=3):
    # Match lessons based on asset class (FX vs stock) or similar verdict direction
    is_fx = "/" in symbol
    verdict_dir = "LONG" if verdict.upper() in ("BUY", "LONG") else "SHORT"
    
    similar = []
    for t in pool:
        t_sym = t.get("symbol", "")
        t_is_fx = "/" in t_sym
        t_verdict = t.get("verdict", "").upper()
        t_dir = "LONG" if t_verdict in ("BUY", "LONG") else "SHORT"
        
        # Priority matches: same asset class and direction
        if t_is_fx == is_fx and t_dir == verdict_dir:
            similar.append(t)
            
    # Fallback to general matches if not enough
    if len(similar) < limit:
        similar.extend([t for t in pool if t not in similar])
        
    return similar[:limit]

def ask_ai_to_veto(inst, verdict, row_features, lessons, llm):
    # Format features
    feat_str = ", ".join([f"{k}: {v:.2f}" for k, v in row_features.items() if np.isfinite(v)])
    
    # Format lessons
    lessons_str = ""
    for idx, l in enumerate(lessons):
        lessons_str += f"{idx+1}. [{l['symbol']} {l['verdict']} -> {l['outcome']}]: \"{l['lesson']}\"\n"
        
    prompt = f"""
We are considering executing a new {verdict} trade on {inst}.

Current Market Indicators:
{feat_str}

Here are relevant lessons from past resolved trades:
{lessons_str}

DIRECTIVE: Act as a cynical hedge fund risk manager. Review the current market indicators against the lessons from similar past trades. 
Determine if this setup is a high-risk trap (e.g. buying a falling knife in strong distribution, entering a low-momentum flat market, or ignoring overhead resistance).
If it is high risk, reply with VETO. Otherwise, reply with ALLOW.

Return ONLY a strict JSON object:
{{
  "verdict": "VETO" or "ALLOW",
  "reason": "1-sentence explanation of your assessment"
}}
"""
    system = "You are a cynical risk manager. Reply only with valid JSON containing 'verdict' and 'reason'."
    
    try:
        resp = llm.complete(prompt, system=system, temperature=0.1, max_tokens=300)
        if not resp:
            return "ALLOW", "AI call failed (fail-ALLOW)"
        
        # Clean response
        clean_resp = resp.strip()
        if clean_resp.startswith("```"):
            clean_resp = clean_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
        data = json.loads(clean_resp)
        verdict_res = data.get("verdict", "ALLOW").upper()
        reason = data.get("reason", "No reason provided")
        return verdict_res, reason
    except Exception as e:
        return "ALLOW", f"Error parsing AI response: {e}"

def main():
    parser = argparse.ArgumentParser(description="APEX FX - Live Emulation AI Backtester")
    parser.add_argument("--instrument", type=str, default="EUR/USD", help="Single symbol (e.g. EUR/USD) or 'all'")
    parser.add_argument("--style", type=str, default="swing", choices=["scalp", "intraday", "swing", "position", "all"], help="Trading style")
    parser.add_argument("--start", type=str, default="2024-01-01", help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()

    cfg = get_config()
    llm = DeepSeekLLM(cfg=cfg.ai)
    
    if not llm.available:
        print("[ERROR] DeepSeek LLM API key not loaded. Check your engine/.env file.")
        return
        
    lessons_pool = fetch_lessons_pool()
    if not lessons_pool:
        print("[WARN] No lessons found in database. The AI will make decisions blindly.")
        
    instruments = list(cfg.universe) if args.instrument == "all" else [args.instrument]
    styles = ["swing", "position"] if args.style == "all" else [args.style]
    
    style_params = {
        "scalp": {"timeframe": "15m", "momentum_lookback": 14, "vol_window": 14, "holding_horizon": 36, "warmup": 70, "atr_stop_mult": 2.5, "reward_risk": 1.5},
        "intraday": {"timeframe": "1h", "momentum_lookback": 24, "vol_window": 24, "holding_horizon": 72, "warmup": 80, "atr_stop_mult": 2.5, "reward_risk": 2.0},
        "swing": {"timeframe": "1d", "momentum_lookback": 63, "vol_window": 63, "holding_horizon": 10, "warmup": 120, "atr_stop_mult": 3.0, "reward_risk": 2.0},
        "position": {"timeframe": "1d", "momentum_lookback": 126, "vol_window": 126, "holding_horizon": 40, "warmup": 180, "atr_stop_mult": 3.0, "reward_risk": 2.0},
    }

    store = ParquetStore()
    backtester = Backtester(cfg)
    
    print(f"\n================================================================================")
    print(f"  AI LIVE-EMULATION BACKTESTER")
    print(f"  Emulating Live DeepSeek Sentiment & Lessons filters over 2 years")
    print(f"  Symbols: {instruments}")
    print(f"  Styles: {styles}")
    print(f"================================================================================\n")
    
    for inst in instruments:
        is_equity = inst.upper() in (cfg.data.equities or [])
        
        # Route equities automatically to Yahoo, Forex to Oanda/Default
        if is_equity:
            adapter = get_adapter("yahoo")
        else:
            adapter = get_adapter(cfg.data.provider)
            
        print(f"\n--- Processing {inst} (Adapter: {adapter.__class__.__name__}) ---")
        
        for style in styles:
            params = style_params[style]
            tf = params["timeframe"]
            warmup = params["warmup"]
            
            try:
                df = clean(store.get_or_fetch(inst, adapter, args.start, datetime.utcnow().strftime("%Y-%m-%d"), timeframe=tf))
                if len(df) < warmup + 50:
                    continue
                    
                pit = PointInTimeAccessor(df)
                
                # Compute feature frame for indicators at any timestamp
                features_df = compute_feature_frame(df, cfg)
                
                # Setup strategy
                strat = RegimeGatedMomentum(
                    momentum_lookback=params["momentum_lookback"],
                    vol_window=params["vol_window"],
                    holding_horizon=params["holding_horizon"],
                    reward_risk=params["reward_risk"],
                    regime_method="rule_based",
                    timeframe=tf,
                    bypass_calibration=True,
                    instrument=inst
                )
                strat.fit(pit, df.index)
                
                # Run blind backtest first (WITHOUT AI)
                res_blind = backtester.run(pit, strat, inst, start=args.start, warmup=warmup, max_hold=params["holding_horizon"])
                blind_trades = res_blind.trades
                
                # Run grounded backtest (WITH AI live-emulation vetoes)
                ai_trades = []
                vetoed_count = 0
                
                for t in blind_trades:
                    # Extract features at entry time
                    ts = pd.Timestamp(t.entry_time)
                    if features_df.index.tz is not None:
                        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                    else:
                        ts = ts.tz_localize(None)
                        
                    row_features = {}
                    if ts in features_df.index:
                        row_features = features_df.loc[ts].to_dict()
                        
                    # Get similar lessons
                    similar_lessons = get_similar_lessons(inst, t.direction, lessons_pool, limit=3)
                    
                    # Call DeepSeek API to decide whether to veto or allow
                    verdict_res, reason = ask_ai_to_veto(inst, t.direction, row_features, similar_lessons, llm)
                    
                    if verdict_res == "VETO":
                        print(f"  [VETOED] Trade on {t.entry_time}: {t.direction} vetoed by DeepSeek -> Reason: {reason}")
                        vetoed_count += 1
                    else:
                        # Allow trade
                        ai_trades.append(t)
                        
                # Calculate metrics
                blind_metrics = calculate_portfolio_metrics(blind_trades)
                ai_metrics = calculate_portfolio_metrics(ai_trades)
                
                print(f"\nResults for {inst} ({style.upper()} - {tf}):")
                print(f"  MATH-ONLY (No AI): {blind_metrics['n_trades']} trades | WR: {blind_metrics['win_rate']:.1%} | Net PnL: ${blind_metrics['net_pnl']:.2f} | PF: {blind_metrics['profit_factor'] or 'N/A'}")
                print(f"  AI-EMULATED      : {ai_metrics['n_trades']} trades | WR: {ai_metrics['win_rate']:.1%} | Net PnL: ${ai_metrics['net_pnl']:.2f} | PF: {ai_metrics['profit_factor'] or 'N/A'} (Vetoed: {vetoed_count} setups)")
                
            except Exception as e:
                print(f"Error backtesting {inst} / {style}: {e}")

if __name__ == "__main__":
    main()
