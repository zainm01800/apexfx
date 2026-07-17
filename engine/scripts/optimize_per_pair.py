#!/usr/bin/env python3
"""
APEX Quant — Per-Pair Production-Grade Strategy Optimiser
=========================================================
Performs walk-forward sweeps per symbol/timeframe using the actual live engine
strategy (MultiTimeframeMomentum wrapping RegimeGatedMomentum).
Saves results to data_store/high_frequency_optimized_configs.json.
"""

import sys
import json
import warnings
import argparse
import multiprocessing
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Ensure engine/ is on sys.path
_ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
from apex_quant.backtest import Backtester

DATA_DIR = _ENGINE_DIR / "data_store"
OUTPUT_FILE = DATA_DIR / "high_frequency_optimized_configs.json"
REPORT_FILE = DATA_DIR / "optimisation_report.md"

# ── Asset classification map ────────────────────────────────────────────────
def classify(symbol: str) -> str:
    s = symbol.upper()
    if "USD" in s and "/" in s:
        if any(c in s for c in ["BTC","ETH","SOL","BNB","XRP","ADA","AVAX","DOGE","LINK","ARB","SUI","MATIC"]):
            return "Crypto"
        return "Forex"
    if "/" in s:
        return "Forex"
    return "Equity"

# ── Build full asset list from disk ────────────────────────────────────────
def build_asset_list():
    assets = []
    seen = set()
    for fp in sorted(DATA_DIR.glob("*.parquet")):
        if fp.stat().st_size < 5000:   # skip empty/tiny files
            continue
        name = fp.stem  # e.g. BTC_USD_1h
        parts = name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        raw_sym, tf = parts[0], parts[1]
        
        # Format the symbol consistently
        if raw_sym.count("_") == 1 and raw_sym.endswith("USD"):
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and "JPY" in raw_sym:
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and "GBP" in raw_sym:
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and ("EUR" in raw_sym or "AUD" in raw_sym or
                                           "NZD" in raw_sym or "CHF" in raw_sym or
                                           "CAD" in raw_sym):
            symbol = raw_sym.replace("_", "/")
        else:
            symbol = raw_sym.replace("_", "/") if "_" in raw_sym else raw_sym

        key = (symbol, tf)
        if key not in seen:
            seen.add(key)
            assets.append((symbol, fp.name, tf, classify(symbol)))
    return assets

# ── Optimization Worker ──────────────────────────────────────────────────────
def optimize_single_asset(args):
    symbol, fname, tf, asset_class, grid_params = args
    fp = DATA_DIR / fname
    
    try:
        df = pd.read_parquet(fp)
    except Exception as e:
        print(f"  [ERROR] Failed to read {fname}: {e}")
        return None
        
    if len(df) < 300:
        return None

    # Timezone conversion/handling
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    # Train/Test Split (75% Train, 25% OOS)
    split = int(len(df) * 0.75)
    df_train = df.iloc[:split]
    df_oos = df.iloc[split:]
    
    pit_train = PointInTimeAccessor(df_train)
    pit_oos = PointInTimeAccessor(df_oos)

    best_train_score = -np.inf
    best_config = None
    
    min_trades = {"15m": 12, "1h": 8, "1d": 4}.get(tf, 4)
    
    total_combos = (len(grid_params["mom_lookbacks"]) * 
                    len(grid_params["vol_windows"]) * 
                    len(grid_params["hold_horizons"]) * 
                    len(grid_params["reward_risks"]) * 
                    len(grid_params["atr_stop_mults"]))
    counter = 0

    # Walk through the grid
    for mom in grid_params["mom_lookbacks"]:
        for vol in grid_params["vol_windows"]:
            for hh in grid_params["hold_horizons"]:
                for rr in grid_params["reward_risks"]:
                    for stop_mult in grid_params["atr_stop_mults"]:
                        counter += 1
                        if counter % 50 == 0:
                            print(f"    [{symbol} {tf}] Sweeping combination {counter}/{total_combos}...")
                        
                        warmup = max(mom, 50) + 15
                        if len(df_train) < warmup + 10:
                            continue
                            
                        # Setup actual production strategies
                        base_strat = RegimeGatedMomentum(
                            momentum_lookback=mom,
                            vol_window=vol,
                            holding_horizon=hh,
                            reward_risk=rr,
                            regime_method="rule_based",
                            timeframe=tf,
                            bypass_calibration=True,
                            instrument=symbol,
                            atr_stop_mult=stop_mult,
                            enable_mean_reversion=True
                        )
                        
                        # High-Timeframe Trend filter mapping (mirroring run_live_paper_trading.py)
                        htf_rule = None
                        htf_ma_window = 200
                        if tf == "15m":
                            htf_rule = "1h"
                        elif tf == "1h":
                            htf_rule = "1d"
                        elif tf == "1d":
                            htf_rule = "1w"
                        if tf == "1d":
                            htf_ma_window = 50

                        strat = MultiTimeframeMomentum(
                            base_strategy=base_strat,
                            htf_rule=htf_rule,
                            htf_ma_window=htf_ma_window,
                            instrument=symbol
                        )
                        
                        try:
                            # Run backtest on train slice
                            strat.fit(pit_train, df_train.index[:-1])
                            
                            # Custom config copy with current parameters
                            cfg = get_config().model_copy(deep=True)
                            cfg.risk.atr_stop_mult = stop_mult
                            
                            # Standardise backtest execution risk parameters to isolate strategy edge
                            cfg.risk.max_risk_per_trade = 0.02
                            cfg.risk.max_concurrent_trades = 12
                            cfg.risk.max_portfolio_risk = 0.99
                            cfg.risk.drawdown_breaker = 0.99
                            cfg.risk.drawdown_reducing_limit = 0.99
                            
                            bt_train = Backtester(cfg=cfg, use_regime=True, exit_mode="managed")
                            res_train = bt_train.run(pit_train, strat, symbol, warmup=warmup, timeframe=tf)
                            
                            metrics = res_train.metrics
                            n_trades = metrics.get("n_trades", 0)
                            pnl = metrics.get("net_pnl", 0.0)
                            pf = metrics.get("profit_factor")
                            sharpe = metrics.get("sharpe", 0.0)
                            
                            if pf is None:
                                pf = 1.05 if pnl > 0 else 0.0
                                
                            # Check minimal validation criteria
                            if n_trades < min_trades or pnl <= 0 or pf < 1.02 or sharpe <= 0:
                                continue
                                
                            # Score calculation: Sharpe * ProfitFactor * sqrt(trades)
                            score = sharpe * pf * np.sqrt(n_trades)
                            if score > best_train_score:
                                best_train_score = score
                                best_config = {
                                    "parameters": {
                                        "momentum_lookback": mom,
                                        "vol_window": vol,
                                        "holding_horizon": hh,
                                        "reward_risk": rr,
                                        "atr_stop_mult": stop_mult
                                    },
                                    "train": {
                                        "n_trades": n_trades,
                                        "net_pnl": pnl,
                                        "win_rate": metrics.get("win_rate", 0.0),
                                        "profit_factor": pf,
                                        "sharpe": sharpe
                                    },
                                    "warmup": warmup,
                                    "htf_rule": htf_rule,
                                    "htf_ma_window": htf_ma_window
                                }
                        except Exception as e:
                            # Suppress backtest loop errors to continue grid search
                            pass

    # If a good configuration was found on training set, run it OOS (Out-of-sample)
    if best_config is not None:
        p = best_config["parameters"]
        warmup = best_config["warmup"]
        
        base_strat_oos = RegimeGatedMomentum(
            momentum_lookback=p["momentum_lookback"],
            vol_window=p["vol_window"],
            holding_horizon=p["holding_horizon"],
            reward_risk=p["reward_risk"],
            regime_method="rule_based",
            timeframe=tf,
            bypass_calibration=True,
            instrument=symbol,
            atr_stop_mult=p["atr_stop_mult"],
            enable_mean_reversion=True
        )
        strat_oos = MultiTimeframeMomentum(
            base_strategy=base_strat_oos,
            htf_rule=best_config["htf_rule"],
            htf_ma_window=best_config["htf_ma_window"],
            instrument=symbol
        )
        
        try:
            strat_oos.fit(pit_oos, df_oos.index[:-1])
            
            cfg_oos = get_config().model_copy(deep=True)
            cfg_oos.risk.atr_stop_mult = p["atr_stop_mult"]
            cfg_oos.risk.max_risk_per_trade = 0.02
            cfg_oos.risk.max_concurrent_trades = 12
            cfg_oos.risk.max_portfolio_risk = 0.99
            cfg_oos.risk.drawdown_breaker = 0.99
            cfg_oos.risk.drawdown_reducing_limit = 0.99
            
            bt_oos = Backtester(cfg=cfg_oos, use_regime=True, exit_mode="managed")
            res_oos = bt_oos.run(pit_oos, strat_oos, symbol, warmup=warmup, timeframe=tf)
            
            metrics_oos = res_oos.metrics
            n_oos = metrics_oos.get("n_trades", 0)
            pnl_oos = metrics_oos.get("net_pnl", 0.0)
            pf_oos = metrics_oos.get("profit_factor")
            sharpe_oos = metrics_oos.get("sharpe", 0.0)
            wr_oos = metrics_oos.get("win_rate", 0.0)
            
            if pf_oos is None:
                pf_oos = 1.0 if pnl_oos > 0 else 0.0
                
            # Veto condition: If OOS results are unprofitable, have negative Sharpe, or extremely low winrate
            # Or if no trades were executed OOS at all.
            veto = False
            if n_oos == 0 or pnl_oos <= 0 or sharpe_oos <= 0 or wr_oos < 0.25:
                veto = True
                
            result = {
                "symbol": symbol,
                "asset_class": asset_class,
                "timeframe": tf,
                "parameters": {
                    "momentum_lookback": p["momentum_lookback"],
                    "vol_window": p["vol_window"],
                    "hold_horizon": p["holding_horizon"],  # key expected by run_live_paper_trading.py
                    "reward_risk": p["reward_risk"],
                    "atr_stop_mult": p["atr_stop_mult"]
                },
                "train": best_config["train"],
                "oos": {
                    "n_trades": n_oos,
                    "net_pnl": pnl_oos,
                    "win_rate": wr_oos,
                    "profit_factor": pf_oos,
                    "sharpe": sharpe_oos
                },
                "veto": veto
            }
            print(f"  [OK] {symbol:12s} {tf:4s} | Train: {best_config['train']['n_trades']} trades, PF={best_config['train']['profit_factor']:.2f} | "
                  f"OOS: {n_oos} trades, PnL=+${pnl_oos:,.0f}, WR={wr_oos*100:.0f}%, Sharpe={sharpe_oos:.2f} | VETO={veto}")
            return result
        except Exception as e:
            # Fallback if OOS fails
            print(f"  [WARN] OOS failed for {symbol} ({tf}): {e}")
            pass

    # Vetoed default result if no parameter set could be validated
    print(f"  [VETOED] {symbol:12s} {tf:4s} | No robust parameter set found during training sweep.")
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "timeframe": tf,
        "parameters": {
            "momentum_lookback": 28,
            "vol_window": 28,
            "hold_horizon": 24,
            "reward_risk": 2.0,
            "atr_stop_mult": 2.5
        },
        "train": {"n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0},
        "oos": {"n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0},
        "veto": True
    }

# ── Main Sweep Execution ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Sweep robust configurations per pair.")
    parser.add_argument("--instruments", type=str, default="", help="Comma-separated list of symbols to run, e.g. EUR/USD,USD/JPY")
    parser.add_argument("--timeframes", type=str, default="15m,1h,1d", help="Comma-separated list of timeframes to run")
    parser.add_argument("--asset-class", type=str, default="", help="Filter by asset class (e.g. Forex, Crypto, Equity)")
    parser.add_argument("--jobs", type=int, default=0, help="Number of concurrent multiprocessing jobs")
    args = parser.parse_args()

    # Define Parameter Grid
    grid_params = {
        "mom_lookbacks": [14, 28, 63, 126],
        "vol_windows": [21, 63],
        "hold_horizons": [10, 20, 40, 60],
        "reward_risks": [0.5, 1.0, 1.5, 2.0],
        "atr_stop_mults": [1.5, 2.5, 3.0, 4.0]
    }

    # Filter selections
    select_instruments = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    select_timeframes = [t.strip().lower() for t in args.timeframes.split(",") if t.strip()]

    assets = build_asset_list()
    tasks = []

    for symbol, fname, tf, asset_class_val in assets:
        if select_instruments and symbol.upper() not in select_instruments:
            continue
        if select_timeframes and tf.lower() not in select_timeframes:
            continue
        if args.asset_class and asset_class_val.lower() != args.asset_class.lower():
            continue
        tasks.append((symbol, fname, tf, asset_class_val, grid_params))

    print(f"APEX Quant — Per-Pair Production Sweep ({len(tasks)} symbol/timeframe combos)")
    print(f"Timeframes: {select_timeframes or 'all'} | Instruments: {select_instruments or 'all'} | Class: {args.asset_class or 'all'}")
    print("=" * 75)

    if not tasks:
        print("No matching tasks found.")
        return

    n_workers = args.jobs if args.jobs > 0 else multiprocessing.cpu_count()
    
    if n_workers == 1:
        print("Running sequentially (single process mode)...")
        results = [optimize_single_asset(t) for t in tasks]
    else:
        print(f"Spawning process pool with {n_workers} workers...")
        results = []
        with multiprocessing.Pool(processes=n_workers) as pool:
            for r in pool.imap_unordered(optimize_single_asset, tasks):
                if r is not None:
                    results.append(r)
                    sym = r["symbol"]
                    tf = r["timeframe"]
                    vetoed = r.get("veto", False)
                    status = "VETOED" if vetoed else "PASS"
                    p = r["parameters"]
                    print(f"  [FINISHED] {sym:12s} {tf:4s} | Status: {status:6s} | Mom={p['momentum_lookback']}, Vol={p['vol_window']}, Hold={p['hold_horizon']}, RR={p['reward_risk']}, ATR={p['atr_stop_mult']}", flush=True)

    # Filter out empty results
    results = [r for r in results if r is not None]

    # Save to disk: merge with existing configs if running a subset
    final_configs = []
    if select_instruments or select_timeframes:
        if OUTPUT_FILE.exists():
            try:
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    old_configs = json.load(f)
                    # Filter out the keys we just recalculated
                    recalc_keys = {(r["symbol"], r["timeframe"]) for r in results}
                    final_configs = [c for c in old_configs if (c["symbol"], c["timeframe"]) not in recalc_keys]
            except Exception as e:
                print(f"[WARN] Failed to load old configs to merge: {e}")
                
    final_configs.extend(results)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_configs, f, indent=2)
    print(f"\nSaved {len(final_configs)} configurations to {OUTPUT_FILE}")

    # Generate Optimisation Report Markdown
    generate_markdown_report(results)

def generate_markdown_report(results):
    lines = []
    lines.append("# APEX Quant — Per-Pair Walk-Forward Optimisation Report")
    lines.append(f"Generated at: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("\n## Sweep Performance Summary")
    lines.append("| Instrument | TF | Status | Parameters | Train WR | OOS WR | OOS Sharpe |")
    lines.append("|---|---|---|---|---|---|---|")

    for r in sorted(results, key=lambda x: (x["symbol"], x["timeframe"])):
        sym = r["symbol"]
        tf = r["timeframe"]
        vetoed = r.get("veto", False)
        status = "🔴 VETOED" if vetoed else "🟢 PASS"
        p = r["parameters"]
        param_str = f"Mom={p['momentum_lookback']}, Vol={p['vol_window']}, Hold={p['hold_horizon']}, RR={p['reward_risk']}, ATR={p['atr_stop_mult']}"
        tr_wr = f"{r['train']['win_rate']*100:.0f}%" if r["train"]["n_trades"] > 0 else "N/A"
        oos_wr = f"{r['oos']['win_rate']*100:.0f}%" if r["oos"]["n_trades"] > 0 else "N/A"
        oos_sharpe = f"{r['oos']['sharpe']:.2f}" if r["oos"]["n_trades"] > 0 else "N/A"

        lines.append(f"| {sym} | {tf} | {status} | {param_str} | {tr_wr} | {oos_wr} | {oos_sharpe} |")

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Optimisation report saved to {REPORT_FILE}")

if __name__ == "__main__":
    main()
