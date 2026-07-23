import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd

csv_path = ENGINE_DIR / "data_store" / "baseline_portfolio_trades_fxcorr_2026-07-17.csv"
if csv_path.exists():
    df = pd.read_csv(csv_path)
    
    records = []
    for (pair, tf), group in df.groupby(["pair", "tf"]):
        n_trades = len(group)
        wins = len(group[group["pnl"] > 0])
        losses = len(group[group["pnl"] < 0])
        tot_pnl = group["pnl"].sum()
        win_rate = (wins / n_trades) * 100 if n_trades > 0 else 0.0
        
        # Calculate profit factor: win_pnl / loss_pnl
        win_pnl = group[group["pnl"] > 0]["pnl"].sum()
        loss_pnl = abs(group[group["pnl"] < 0]["pnl"].sum())
        pf = (win_pnl / loss_pnl) if loss_pnl > 0 else 0.0
        
        records.append({
            "symbol": pair,
            "timeframe": tf,
            "n_trades": n_trades,
            "win_rate": win_rate,
            "tot_pnl": tot_pnl,
            "profit_factor": pf
        })
            
    res_df = pd.DataFrame(records).sort_values(by="tot_pnl", ascending=False)
    print("ALL FOREX SYSTEMS PERFORMANCE BREAKDOWN:")
    print("=" * 75)
    print(f"{'FOREX PAIR':<12} | {'TIMEFRAME':<10} | {'TRADES':<8} | {'WIN RATE (%)':<12} | {'PROFIT FACTOR':<14} | {'NET PNL ($)':<12}")
    print("-" * 75)
    for idx, r in res_df.iterrows():
        print(f"{r['symbol']:<12s} | {r['timeframe']:<10s} | {r['n_trades']:<8d} | {r['win_rate']:>11.1f}% | {r['profit_factor']:>13.2f} | ${r['tot_pnl']:>11,.2f}")
