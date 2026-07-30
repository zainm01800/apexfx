#!/bin/bash
# IBKR mirror sync & execution — invoked by launchd (com.apexfx.ibkr-sync) every 120s.
cd /Users/zain/Documents/apexfx/engine || exit 1
export IBKR_CLIENT_ID=18
/Users/zain/Documents/apexfx/engine/.venv-mac/bin/python scripts/run_paper_portfolio.py --no-supabase >> /Users/zain/Documents/apexfx/engine/data_store/ibkr_mirror_sync.log 2>&1
exec /Users/zain/Documents/apexfx/engine/.venv-mac/bin/python scripts/run_ibkr_mirror.py >> /Users/zain/Documents/apexfx/engine/data_store/ibkr_mirror_sync.log 2>&1
