import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import json
import pandas as pd

print("=" * 80)
print("  CHECKING ALL GENERATED SETUPS VS ACTIVE IBKR POSITIONS")
print("=" * 80)

# Check data_store for candidate setups
cand_path = ENGINE_DIR / "data_store" / "candidate_setups.json"
if cand_path.exists():
    with open(cand_path, "r") as fh:
        cands = json.load(fh)
    print(f"Total Candidate Setups Generated in Data Store: {len(cands)}")
    for item in cands:
        sym = item.get("symbol", item.get("instrument", "N/A"))
        side = item.get("direction", item.get("side", "N/A"))
        tf = item.get("timeframe", "1d")
        reason = item.get("reason", "Pending Execution")
        print(f"  Setup: {sym:<10s} | {side:<5s} | {tf:<4s} | Status/Reason: {reason}")
else:
    print("No candidate_setups.json found in data_store.")

# Check setup log CSV
csv_path = ENGINE_DIR / "data_store" / "paper_portfolio_setups.csv"
if csv_path.exists():
    df_s = pd.read_csv(csv_path)
    print(f"\nTotal Logged Setups in CSV: {len(df_s)}")
    print(df_s.tail(10))

print("=" * 80)
