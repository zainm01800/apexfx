import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))

import asyncio

try:
    from ib_async import IB
except ImportError:
    try:
        from ib_insync import IB
    except ImportError:
        import socket
        print("Neither ib_async nor ib_insync installed in python environment.")
        sys.exit(1)

print("=" * 80)
print("  CONNECTING TO IBKR GATEWAY (PORT 4001 / 4002 / 7497)...")
print("=" * 80)

async def main():
    ib = IB()
    connected = False
    for port in [4001, 4002, 7496, 7497]:
        try:
            await ib.connectAsync('127.0.0.1', port, clientId=99, timeout=3)
            print(f"  Connected to IBKR Gateway successfully on port {port}! ✓")
            connected = True
            break
        except Exception as e:
            print(f"  Port {port} connection attempt: {e}")

    if connected:
        try:
            account_values = ib.accountValues()
            portfolio = ib.portfolio()
            positions = ib.positions()
            open_orders = ib.openOrders()
            
            account_summary = {}
            for item in account_values:
                if item.tag in ['NetLiquidation', 'TotalCashValue', 'UnrealizedPnL', 'RealizedPnL', 'GrossPositionValue']:
                    account_summary[item.tag] = item.value
                    
            print("\nLIVE IBKR GATEWAY ACCOUNT SUMMARY:")
            print("-" * 80)
            net_liq = float(account_summary.get('NetLiquidation', 0.0))
            cash = float(account_summary.get('TotalCashValue', 0.0))
            unrealized = float(account_summary.get('UnrealizedPnL', 0.0))
            realized = float(account_summary.get('RealizedPnL', 0.0))
            
            print(f"  Account Net Liquidation (Equity): ${net_liq:>12,.2f}")
            print(f"  Total Cash Value:                ${cash:>12,.2f}")
            print(f"  Unrealized PnL (Open Positions):  ${unrealized:>12,.2f}")
            print(f"  Realized PnL (Closed Trades):     ${realized:>12,.2f}")
            print("-" * 80)
            
            print(f"\nOPEN IBKR POSITIONS ({len(positions)}):")
            if positions:
                for pos in positions:
                    contract = pos.contract
                    print(f"  Symbol: {contract.symbol:<10s} | SecType: {contract.secType:<6s} | Pos: {pos.position:<8.2f} | AvgCost: ${pos.avgCost:<8.2f}")
            else:
                print("  No active open positions on IBKR Gateway.")
                
            print(f"\nWORKING / PENDING ORDERS ({len(open_orders)}):")
            if open_orders:
                for ord in open_orders:
                    print(f"  Order: {ord.action} {ord.totalQuantity} {ord.orderType}")
            else:
                print("  No pending orders currently working on IBKR Gateway.")
                
        finally:
            ib.disconnect()
            print("\nDisconnected from IBKR Gateway cleanly.")
    else:
        print("\nCould not connect to IBKR Gateway. Checking local socket listener on ports...")

asyncio.run(main())
print("=" * 80)
