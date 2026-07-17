import sys
from pathlib import Path
# Insert the 'engine' directory into path so apex_quant can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

import pandas as pd
import numpy as np
from datetime import datetime
from apex_quant.config import get_config
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, MarketState, Signal, Direction
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer
from apex_quant.data.point_in_time import PointInTimeAccessor

# Import functions from run_live_paper_trading
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "scripts"))
import run_live_paper_trading as rpt

def test_sizing_jpy():
    print("INITIALIZING APEX CONFIG & DATA PROVIDER...")
    rpt.cfg = get_config()
    
    # Initialize sizer
    rpt.initialize_bayesian_sizer_from_supabase()
    
    print("\nFETCHING LIVE ACCOUNT STATE...")
    equity, balance, peak = rpt.fetch_live_account_state()
    dd = 1.0 - equity / peak if peak > 0 else 0.0
    print(f"  Live Equity:  £{equity:,.2f}")
    print(f"  Live Balance: £{balance:,.2f}")
    print(f"  Peak Equity:  £{peak:,.2f}")
    print(f"  Drawdown:     {dd:.2%}")
    
    # Try testing sizing for CAD/JPY (Forex) and SMH (Equity)
    for sym in ["CAD/JPY", "SMH"]:
        print(f"\n=================== TESTING SIZING FOR {sym} ===================")
        tf = "15m"
        style = "swing"
        
        params = rpt.get_params_for_trade(style, tf, sym)
        stop_mult = params.get("atr_stop_mult", rpt.cfg.risk.atr_stop_mult)
        
        # Get history
        end_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        start_dt = (datetime.utcnow() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        
        df = rpt.clean(rpt.data_provider.get_history(sym, start=start_dt, end=end_dt, timeframe=tf))
        if df.empty:
            print(f"  [ERROR] Empty history for {sym}")
            continue
            
        close_p = float(df["close"].iloc[-1])
        atr = rpt._compute_atr_tms(df, rpt.cfg.risk.atr_window)
        stop_dist = stop_mult * atr
        
        print(f"  Close Price: {close_p:.5f}")
        print(f"  ATR ({rpt.cfg.risk.atr_window}): {atr:.5f}")
        print(f"  Stop Distance ({stop_mult}x): {stop_dist:.5f}")
        
        # Active trades check
        open_positions = []
        try:
            open_trades_raw = rpt.fetch_open_trades()
            for ot in open_trades_raw:
                sym_ot = ot["symbol"]
                tf_ot = ot.get("timeframe", "1h")
                # quote/rate cand
                quote_ot = rpt.get_quote_currency(sym_ot)
                rate_ot = rpt.get_quote_to_account_rate(quote_ot, "GBP")
                price_ot = float(ot.get("price") or 0.0)
                
                # Simple volume/risk logic
                units = float(ot.get("volume", 0.0))
                if sym_ot.replace("-g", "") in rpt.cfg.data.instruments:
                    units = units * 100000.0
                
                trade_notional = units * (price_ot * rate_ot)
                # Compute risk if stop distance exists
                sl_ot = float(ot.get("stop_loss") or 0.0)
                entry_ot = float(ot.get("price") or 0.0)
                risk_gbp = 0.0
                if sl_ot > 0 and entry_ot > 0:
                    risk_gbp = units * abs(entry_ot - sl_ot) * rate_ot

                open_positions.append(rpt.OpenPosition(
                    instrument=sym_ot,
                    direction=rpt.Direction.LONG if ot["verdict"] in ("BUY", "LONG") else rpt.Direction.SHORT,
                    notional=trade_notional,
                    risk=risk_gbp,
                    timeframe=rpt.map_timeframe(tf_ot)
                ))
        except Exception as e:
            print("Failed to fetch open trades:", e)
            
        account_state = AccountState(
            equity=equity,
            peak_equity=peak,
            open_positions=open_positions
        )
        
        # Sizing
        from apex_quant.features.microstructure import YangZhangVol
        yz_vol_calc = YangZhangVol(window=21)
        pit = PointInTimeAccessor(df)
        latest_time = df.index[-1]
        ann_vol = yz_vol_calc.compute(pit, latest_time)
        if not np.isfinite(ann_vol) or ann_vol <= 0:
            ann_vol = 0.20
            
        quote_cand = rpt.get_quote_currency(sym)
        rate_cand = rpt.get_quote_to_account_rate(quote_cand, "GBP")
        
        market_state = MarketState(
            instrument=sym,
            price=close_p,
            ann_vol=ann_vol,
            atr=atr,
            quote_to_account_rate=rate_cand,
            correlations={}
        )
        
        risk_sig = Signal(
            instrument=sym,
            direction=Direction.LONG,
            probability=0.82,
            reward_risk=2.0,
            confidence=0.8,
            rationale="Test Signal",
            timeframe=tf
        )
        
        live_risk_cfg = rpt.cfg.risk.model_copy(update={"min_position": rpt.cfg.execution.live_min_position})
        risk_manager = RiskManager(live_risk_cfg, bayesian_sizer=rpt._BAYESIAN_SIZER)
        permitted_pos = risk_manager.permit(risk_sig, account_state, market_state, t=latest_time)
        
        print("\n  PERMIT RESULT:")
        print(f"    permitted: {permitted_pos.permitted}")
        print(f"    rationale: {permitted_pos.rationale}")
        print(f"    notional: {permitted_pos.notional:,.2f}")
        print(f"    risk_fraction: {permitted_pos.risk_fraction:.4%}")
        print(f"    constraints_applied: {permitted_pos.constraints_applied}")
        print("    sizing_detail:")
        for k, v in permitted_pos.sizing_detail.items():
            print(f"      {k}: {v}")
            
        cost_model = rpt.cfg.mechanics_for(sym).cost_model if hasattr(rpt.cfg, 'mechanics_for') else 'pips'
        raw_lots = rpt.units_to_lots(sym, permitted_pos.units, cost_model)
        from apex_quant.risk.sizing import round_lot_size
        sized_volume = round_lot_size(raw_lots, min_lot=0.01, lot_step=0.01)
        
        print(f"\n    Raw Lots: {raw_lots:.6f}")
        print(f"    Sized Volume: {sized_volume:.2f}")

if __name__ == "__main__":
    test_sizing_jpy()
