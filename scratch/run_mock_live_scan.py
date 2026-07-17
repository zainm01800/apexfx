import sys
from pathlib import Path
# Insert the 'engine' directory into path so apex_quant can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "scripts"))

import run_live_paper_trading as rpt

# Mock the portfolio to only contain CAD/JPY systems to run instantly
rpt.ROBUST_CORE_PORTFOLIO = [
    {
        "instrument": "CAD/JPY",
        "timeframe": "15m",
        "style": "swing"
    },
    {
        "instrument": "CAD/JPY",
        "timeframe": "1h",
        "style": "swing"
    }
]

print("Running mock live scan once...")
rpt.run_once()
