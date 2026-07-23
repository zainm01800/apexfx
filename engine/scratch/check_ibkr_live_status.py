import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd
from apex_quant.config import get_config
from apex_quant.storage import SupabaseClient

cfg = get_config()
sb = SupabaseClient(cfg)

print("=" * 80)
print("  LIVE ACCOUNT & PAPER TRADING STATUS AUDIT")
print("=" * 80)

# Fetch trades from Supabase
try:
    trades_resp = sb.client.table("trades").select("*").execute()
    trades_data = trades_resp.data if trades_resp else []
    
    if trades_data:
        df_trades = pd.DataFrame(trades_data)
        print(f"Total Trades Recorded in Supabase: {len(df_trades)}")
        
        # Open vs Closed
        open_trades = df_trades[df_trades["status"] == "open"] if "status" in df_trades.columns else pd.DataFrame()
        closed_trades = df_trades[df_trades["status"] == "closed"] if "status" in df_trades.columns else pd.DataFrame()
        
        print(f"Open Positions:   {len(open_trades)}")
        print(f"Closed Trades:    {len(closed_trades)}")
        
        if "pnl_usd" in df_trades.columns or "pnl" in df_trades.columns:
            pnl_col = "pnl_usd" if "pnl_usd" in df_trades.columns else "pnl"
            tot_pnl = df_trades[pnl_col].sum()
            print(f"Total Net PnL:    ${tot_pnl:,.2f}")
            
        print("-" * 80)
        print("RECENT TRADES:")
        for idx, row in df_trades.tail(10).iterrows():
            sym = row.get("symbol", row.get("instrument", "N/A"))
            side = row.get("direction", row.get("side", "N/A"))
            pnl = row.get("pnl_usd", row.get("pnl", 0.0))
            status = row.get("status", "N/A")
            entry_p = row.get("entry_price", 0.0)
            print(f"  [{status.upper()}] {sym:<10s} | {side:<5s} | Entry: {entry_p:<8.2f} | PnL: ${pnl:>9.2f}")
    else:
        print("No live trades recorded in Supabase yet.")
except Exception as e:
    print(f"Error fetching Supabase trades: {e}")

# Check IBKR mirror sync log
sync_log = ENGINE_DIR / "data_store" / "ibkr_mirror_sync.log"
if sync_log.exists():
    print("-" * 80)
    print("LAST IBKR SYNC LOG STATUS:")
    lines = sync_log.read_text().splitlines()[-10:]
    for line in lines:
        print(f"  {line}")

print("=" * 80)
