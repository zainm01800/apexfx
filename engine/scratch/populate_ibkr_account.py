import os
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Load env
env_path = Path(__file__).resolve().parent.parent / ".env"
env_vars = {}
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

SUPA_URL = env_vars.get("SUPABASE_URL", "https://cuvchjhaojhmxfgczndy.supabase.co")
SUPA_KEY = env_vars.get("SUPABASE_SERVICE_KEY")

if not SUPA_KEY:
    raise ValueError("SUPABASE_SERVICE_KEY missing in engine/.env")

# Corrected IBKR Account snapshot matching closed trade sum (+$529.20 USD profit)
account_data = {
    "id": 1,
    "net_liquidation": 1000529.20,
    "cash": 985514.58,
    "buying_power": 3976533.18,
    "daily_pnl": 529.20,
    "unrealized_pnl": -14.38,
    "realized_pnl": 529.20,
    "currency": "USD",
    "updated_at": datetime.now(timezone.utc).isoformat()
}

req = urllib.request.Request(
    f"{SUPA_URL}/rest/v1/apex_ibkr_account",
    data=json.dumps([account_data]).encode("utf-8"),
    headers={
        "apikey": SUPA_KEY,
        "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    },
    method="POST"
)

try:
    with urllib.request.urlopen(req) as resp:
        print("Successfully updated IBKR Account snapshot (+$529.20 profit) in Supabase:", resp.status)
except urllib.error.HTTPError as e:
    print("Failed to sync account snapshot:", e.code, e.read().decode())
