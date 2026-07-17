import sys
from pathlib import Path
# Insert the 'engine' directory into path so apex_quant can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "scripts"))

import run_live_paper_trading as rpt

# 1. Force the live timeframes list to include 15m and 1h so we scan JPY systems
rpt.cfg = rpt.get_config()
rpt.cfg.data.live_timeframes = ["15m", "1h", "1d", "1w"]

# 2. Mock portfolio to scan only CAD/JPY systems
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

# 3. Mock resolve_closed_mt4_setups to skip network calls
def mock_resolve():
    print("[MOCK] Skipped resolve_closed_mt4_setups to run instantly.")
rpt.resolve_closed_mt4_setups = mock_resolve

print("Running mock live JPY scan...")
rpt.run_once()
print("Scan completed.")
