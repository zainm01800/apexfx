import sys
import json
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd

cfg_path = ENGINE_DIR / "data_store" / "high_frequency_optimized_configs.json"
if cfg_path.exists():
    with open(cfg_path, "r") as fh:
        data = json.load(fh)
    
    systems = data if isinstance(data, list) else data.get("systems", [])
    records = []
    for item in systems:
        symbol = item.get("symbol", item.get("instrument", ""))
        tf = item.get("timeframe", "")
        m = item.get("metrics", item.get("out_of_sample", item))
        pnl = m.get("total_pnl", m.get("net_profit", m.get("total_return", 0.0)))
        win_rate = m.get("win_rate", 0.0)
        pf = m.get("profit_factor", 0.0)
        trades = m.get("n_trades", m.get("total_trades", item.get("n_trades", 0)))
        
        # Forex filter
        if any(curr in symbol for curr in ["EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CAD", "CHF"]) and not "/USD" in symbol:
            records.append({
                "symbol": symbol,
                "timeframe": tf,
                "total_trades": trades,
                "win_rate": win_rate * 100 if win_rate <= 1.0 else win_rate,
                "net_pnl": pnl,
                "profit_factor": pf
            })
    
    if records:
        df = pd.DataFrame(records).sort_values(by="total_trades", ascending=False)
        print("ACTIVE FOREX SYSTEMS PERFORMANCE BREAKDOWN:")
        print("=" * 75)
        print(f"{'FOREX PAIR':<12} | {'TIMEFRAME':<10} | {'TRADES':<8} | {'WIN RATE':<10} | {'PROFIT FACTOR':<14} | {'NET PNL':<12}")
        print("-" * 75)
        for idx, r in df.iterrows():
            print(f"{r['symbol']:<12s} | {r['timeframe']:<10s} | {r['total_trades']:<8d} | {r['win_rate']:>8.1f}% | {r['profit_factor']:>13.2f} | {r['net_pnl']}")
