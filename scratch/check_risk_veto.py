import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

import pandas as pd
import numpy as np
from apex_quant.config import get_config
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, MarketState, Signal, Direction
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer

def check_veto():
    cfg = get_config()
    print("CONFIG:")
    print("  drawdown_breaker:", cfg.risk.drawdown_breaker)
    print("  max_risk_per_trade:", cfg.risk.max_risk_per_trade)
    print("  target_portfolio_vol:", cfg.risk.target_portfolio_vol)
    print("  kelly_fraction:", cfg.risk.kelly_fraction)
    print("  live_min_position:", cfg.execution.live_min_position)
    print("  min_position in risk:", cfg.risk.min_position)
    print()

    # Let's mock an AccountState
    # Live Equity is around 98,800 GBP, peak is 100,000 GBP
    account_state = AccountState(
        equity=98800.0,
        peak_equity=100000.0,
        open_positions=[]
    )

    # Let's mock a MarketState for CAD/JPY
    # CAD/JPY is around 115.80 JPY
    # Let's say ATR is 1.0 JPY, and quote_to_account_rate (JPY/GBP) is 0.005
    market_state = MarketState(
        instrument="CAD/JPY",
        price=115.80,
        ann_vol=0.15,
        atr=1.0,
        quote_to_account_rate=0.005,
        correlations={}
    )

    # Let's mock a Signal for CAD/JPY
    signal = Signal(
        instrument="CAD/JPY",
        direction=Direction.LONG,
        probability=0.82,
        reward_risk=2.0,
        confidence=0.8,
        rationale="MTF trend alignment",
        timeframe="15m"
    )

    # Risk manager setup
    live_risk_cfg = cfg.risk.model_copy(update={"min_position": cfg.execution.live_min_position})
    
    # Try with Bayesian sizer
    try:
        from apex_quant.risk.bayesian_sizer import BayesianRiskSizer
        bayesian_sizer = BayesianRiskSizer()
    except Exception as e:
        print("Failed to load Bayesian sizer:", e)
        bayesian_sizer = None

    risk_manager = RiskManager(live_risk_cfg, bayesian_sizer=bayesian_sizer)
    permitted_pos = risk_manager.permit(signal, account_state, market_state)
    
    print("PERMITTED POSITION DETAILS:")
    print("  permitted:", permitted_pos.permitted)
    print("  rationale:", permitted_pos.rationale)
    print("  units:", permitted_pos.units)
    print("  notional:", permitted_pos.notional)
    print("  risk_fraction:", permitted_pos.risk_fraction)
    print("  constraints_applied:", permitted_pos.constraints_applied)
    print("  sizing_detail:")
    for k, v in permitted_pos.sizing_detail.items():
        print(f"    {k}: {v}")

if __name__ == "__main__":
    check_veto()
