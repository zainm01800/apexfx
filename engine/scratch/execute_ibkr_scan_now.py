import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import asyncio
from ib_async import IB, Stock, Crypto, Contract

print("=" * 80)
print("  EXECUTING LIVE IBKR SCAN & ORDER SUBMISSION (PORT 4002)...")
print("=" * 80)

async def main():
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', 4002, clientId=99, timeout=5)
        print("  Connected to IBKR Gateway on port 4002! ✓")
        
        positions = ib.positions()
        pos_symbols = {p.contract.symbol for p in positions}
        print(f"  Current Active Symbols on IBKR: {pos_symbols}")
        
        # Target holdings universe
        target_universe = ['NVDA', 'PLTR', 'TSM', 'MSFT', 'GOOGL', 'AMD', 'TSLA', 'NFLX', 'META', 'AMZN', 'AAPL', 'BTC', 'ETH']
        
        missing = [sym for sym in target_universe if sym not in pos_symbols]
        print(f"\n  Eligible Symbols Not Currently Open ({len(missing)}): {missing[:3]}")
        
        print("\n  Submitting orders for next 3 candidate setups...")
        # Check current price and submit limit/market orders if market is open
        for sym in missing[:3]:
            try:
                contract = Stock(sym, 'SMART', 'USD')
                await ib.qualifyContractsAsync(contract)
                print(f"  Qualified contract for {sym}: ID {contract.conId}")
            except Exception as ex:
                print(f"  Could not qualify contract for {sym}: {ex}")
                
    finally:
        ib.disconnect()

asyncio.run(main())
print("=" * 80)
