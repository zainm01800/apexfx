import sys
import json
from pathlib import Path
import pandas as pd

ENGINE_DIR = Path.cwd()

print("=" * 80)
print("  LIVE LOCAL ENGINE & IBKR PAPER ACCOUNT STATE")
print("=" * 80)

# 1. Paper portfolio state
p_state = ENGINE_DIR / "data_store" / "paper_portfolio_state.json"
if p_state.exists():
    with open(p_state, "r") as fh:
        data = json.load(fh)
    print(f"Account Starting Balance:  ${data.get('initial_capital', 100000.0):,.2f}")
    print(f"Current Account Equity:    ${data.get('equity', 100000.0):,.2f}")
    print(f"Cumulative Realized PnL:   ${data.get('cum_pnl', 0.0):,.2f}")
    print(f"Current Portfolio Peak:    ${data.get('peak_equity', 100000.0):,.2f}")
    
    positions = data.get("positions", {})
    print(f"\nCurrently Open Positions ({len(positions)}):")
    for sym, pos in positions.items():
        entry_p = pos.get("entry_price", 0.0)
        units = pos.get("units", 0.0)
        side = pos.get("side", pos.get("direction", "long"))
        print(f"  {sym:<12s} | {side:<5s} | Units: {units:<10.2f} | Entry: ${entry_p:<8.2f}")
else:
    print("No paper_portfolio_state.json found yet.")

# 2. IBKR Mirror State
m_state = ENGINE_DIR / "data_store" / "ibkr_mirror_state.json"
if m_state.exists():
    with open(m_state, "r") as fh:
        m_data = json.load(fh)
    print("-" * 80)
    print("IBKR MIRROR AGENT STATE:")
    print(f"Last Sync Time: {m_data.get('last_sync', 'N/A')}")
    print(f"Synced Orders Count: {len(m_data.get('synced_orders', {}))}")

print("=" * 80)
