import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import json
from dotenv import load_dotenv
import os

load_dotenv(ENGINE_DIR / ".env")
url = os.getenv("SUPABASE_URL", "https://cuvchjhaojhmxfgczndy.supabase.co")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY", "")

headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}"
}

from apex_quant.storage.supabase_util import fetch_all_rows

print("=" * 80)
print("  LIVE SUPABASE & IBKR PAPER ACCOUNT AUDIT")
print("=" * 80)

# 1. IBKR Trades
try:
    req_url = f"{url}/rest/v1/apex_ibkr_trades?select=*&order=created_at.desc"
    ibkr_trades = fetch_all_rows(req_url, headers)
    print(f"Total Recorded IBKR Mirror Trades: {len(ibkr_trades)}")
    if ibkr_trades:
        import pandas as pd
        df_ib = pd.DataFrame(ibkr_trades)
        print("\nRECENT IBKR PAPER TRADES:")
        for idx, row in df_ib.head(10).iterrows():
            sym = row.get("symbol", "N/A")
            side = row.get("side", "N/A")
            pnl = row.get("realized_pnl", 0.0)
            status = row.get("status", "N/A")
            created = str(row.get("created_at", "N/A"))
            print(f"  [{created[:16]}] {status:<8s} | {sym:<10s} | {side:<5s} | Realized PnL: ${pnl:>9.2f}")
except Exception as e:
    print(f"IBKR Trades Table Notice: {e}")

# 2. Paper Portfolio Daily State
try:
    req_url = f"{url}/rest/v1/apex_paper_daily?select=*&order=date.desc"
    paper_state = fetch_all_rows(req_url, headers)
    print(f"\nLive Paper Portfolio Daily Records: {len(paper_state)}")
    if paper_state:
        import pandas as pd
        df_st = pd.DataFrame(paper_state)
        for idx, row in df_st.head(5).iterrows():
            date = row.get("date", "N/A")
            eq = row.get("equity", 0.0)
            cum_pnl = row.get("cum_pnl", 0.0)
            dd = row.get("drawdown", 0.0)
            print(f"  Date: {date} | Equity: ${eq:>11,.2f} | Cum PnL: ${cum_pnl:>9.2f} | Drawdown: {dd*100:.2f}%")
except Exception as e:
    print(f"Paper State Notice: {e}")

# 3. MT4 Trades
try:
    req_url = f"{url}/rest/v1/apex_mt4_trades?select=*&order=open_time.desc"
    mt4_trades = fetch_all_rows(req_url, headers)
    print(f"\nTotal MT4 Active/Closed Trades Logged: {len(mt4_trades)}")
    if mt4_trades:
        import pandas as pd
        df_mt4 = pd.DataFrame(mt4_trades)
        closed = df_mt4[df_mt4["status"] == "closed"] if "status" in df_mt4.columns else pd.DataFrame()
        tot_mt4_pnl = closed["pnl_usd"].sum() if not closed.empty and "pnl_usd" in closed.columns else 0.0
        print(f"  Closed MT4 Trades: {len(closed)} | Total Realized Net PnL: ${tot_mt4_pnl:,.2f}")
except Exception as e:
    print(f"MT4 Trades Notice: {e}")

print("=" * 80)
