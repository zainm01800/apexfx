import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "scripts"))

import run_live_paper_trading as rpt
from apex_quant.config import get_config

def calculate_stats():
    rpt.cfg = get_config()
    print("CONFIG:")
    print("  max_portfolio_risk:", getattr(rpt.cfg.risk, "max_portfolio_risk", 0.035))
    print("  max_risk_per_trade:", rpt.cfg.risk.max_risk_per_trade)
    print("  live_min_position:", rpt.cfg.execution.live_min_position)
    print()

    equity, balance, peak = rpt.fetch_live_account_state()
    print(f"Account state: Equity={equity:.2f}, Balance={balance:.2f}, Peak={peak:.2f}")

    open_trades_list = rpt.fetch_open_trades()
    print(f"Pending Forex setups in DB: {len(open_trades_list)}")
    
    total_open_risk = 0.0
    for ot in open_trades_list:
        sym_ot = ot["symbol"]
        price_ot = rpt._safe_float(ot.get("price")) or 0.0
        sl_ot = rpt._safe_float(ot.get("stop_loss"))
        asset_class_ot = rpt.cfg.asset_class_of(sym_ot)
        
        quote_ot = rpt.get_quote_currency(sym_ot)
        rate_ot = rpt.get_quote_to_account_rate(quote_ot, "GBP")
        
        risk_gbp = 0.0
        if sl_ot and abs(price_ot - sl_ot) > 1e-6:
            stop_dist_ot_gbp = abs(price_ot - sl_ot) * rate_ot
            risk_cap = 0.01 * equity
            units = risk_cap / stop_dist_ot_gbp if stop_dist_ot_gbp > 0 else 1000.0
            if asset_class_ot == "forex":
                units = min(units, 500000.0)
            else:
                units = min(units, 1000.0)
            trade_notional = units * (price_ot * rate_ot)
            risk_gbp = units * stop_dist_ot_gbp
        else:
            price_ot_gbp = price_ot * rate_ot
            if asset_class_ot == "forex":
                trade_notional = price_ot_gbp * 10000.0
            else:
                trade_notional = price_ot_gbp * 1.0
                
        total_open_risk += risk_gbp
        print(f"  {sym_ot}: notional={trade_notional:,.2f} GBP, risk={risk_gbp:,.2f} GBP")
        
    print()
    total_open_risk_pct = total_open_risk / equity if equity > 0 else 0.0
    print(f"Total Open Risk: £{total_open_risk:,.2f} ({total_open_risk_pct:.2%})")
    
    max_port_risk = getattr(rpt.cfg.risk, "max_portfolio_risk", 0.035)
    max_proposed_risk = max_port_risk - total_open_risk_pct
    print(f"Max Proposed Risk: {max_proposed_risk:.4%}")

if __name__ == "__main__":
    calculate_stats()
