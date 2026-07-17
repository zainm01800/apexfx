"""Offline smoke test for the 2026-07-17 live-script changes.

Covers (no MT4 bridge writes, no network):
  A. execution.managed_exits / execution.htf_direction_only defensive reads
     (config.yaml does NOT define the keys yet -> defaults must be True).
  B. _is_managed_trade flag semantics (old trades unmanaged, new trades managed).
  C. apply_trade_manager_tms: TradeManager state diff -> executor commands
     (recording fake executor; httpx.patch stubbed).
  D. HTF gate in scan_single_asset: FLAT HTF -> no entry; LONG HTF + no
     pullback -> no entry; LONG HTF + pullback -> entry with entry_origin.
  E. _get_htf_direction runs offline against a stubbed data provider.

Run:  cd engine && APEX_EXECUTION__ENABLED=false .venv-mac/bin/python scratch/smoke_managed_exits.py
"""

import os
import sys
from pathlib import Path

# Execution disabled BEFORE import -> _create_executor() returns None.
os.environ["APEX_EXECUTION__ENABLED"] = "false"

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np
import pandas as pd

import scripts.run_live_paper_trading as scanner
from apex_quant.risk import Direction

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def make_df(n, freq, end, closes):
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC", name="timestamp")
    closes = np.asarray(closes, dtype=float)
    spread = 0.0004
    df = pd.DataFrame({
        "open": np.roll(closes, 1),
        "high": closes + spread,
        "low": closes - spread,
        "close": closes,
        "volume": np.full(n, 1000.0),
    }, index=idx)
    df.iloc[0, df.columns.get_loc("open")] = closes[0]
    return df


print("=" * 70)
print("SMOKE TEST: managed exits + HTF-direction-only (offline)")
print("=" * 70)

# ── A. Flag defaults ─────────────────────────────────────────────────────────
print("\n[A] Defensive config reads (keys absent from config.yaml)")
check("execution disabled via env -> _EXECUTOR is None", scanner._EXECUTOR is None)
check("_MANAGED_EXITS_ENABLED defaults True", scanner._MANAGED_EXITS_ENABLED is True)
check("_HTF_DIRECTION_ONLY defaults True", scanner._HTF_DIRECTION_ONLY is True)
check("_EXECUTION_ONLY_TFS == ('15m','1h')", scanner._EXECUTION_ONLY_TFS == ("15m", "1h"))
check("_exec_flag missing key -> default", scanner._exec_flag("does_not_exist", False) is False)
check("_exec_flag hardened against None cfg", scanner._exec_flag("managed_exits", True) is True)

# ── B. _is_managed_trade ─────────────────────────────────────────────────────
print("\n[B] _is_managed_trade")
legacy_trade = {"setup_features": {"auto": True, "style": "swing"}}
managed_trade = {"setup_features": {"auto": True, "managed_exits": True}}
managed_str = {"setup_features": '{"auto": true, "managed_exits": true}'}
check("legacy trade (no flag) is unmanaged", scanner._is_managed_trade(legacy_trade) is False)
check("flagged trade is managed", scanner._is_managed_trade(managed_trade) is True)
check("string setup_features parsed", scanner._is_managed_trade(managed_str) is True)
check("empty trade unmanaged", scanner._is_managed_trade({}) is False)
scanner._MANAGED_EXITS_ENABLED = False
# Per-trade stamp trust (2026-07-17 audit fix): the management mode is decided
# by the trade's OWN setup_features stamp alone — flipping the global flag must
# never change how an already-open trade is exited.
check("global flag flip does NOT unmanage a stamped trade", scanner._is_managed_trade(managed_trade) is True)
scanner._MANAGED_EXITS_ENABLED = True

# ── C. apply_trade_manager_tms adapter ───────────────────────────────────────
print("\n[C] apply_trade_manager_tms (recording executor, stubbed patch)")

calls = {"partial_close": [], "modify_sl": [], "submit_order": [], "patch": []}


class RecExecutor:
    def partial_close(self, symbol, ticket, volume):
        calls["partial_close"].append((symbol, ticket, volume))

    def modify_sl(self, symbol, ticket, new_sl):
        calls["modify_sl"].append((symbol, ticket, new_sl))

    def submit_order(self, **kw):
        calls["submit_order"].append(kw)

    def close_position(self, symbol):
        calls["submit_order"].append({"cmd": "close", "symbol": symbol})


orig_executor = scanner._EXECUTOR
orig_get_pos = scanner._get_mt4_positions
orig_patch = scanner.httpx.patch

scanner._EXECUTOR = RecExecutor()
scanner._get_mt4_positions = lambda: [
    {"symbol": "EURUSD-g", "ticket": 12345, "volume": 1.00, "sl": 1.0900, "cmd": 0}
]
scanner.httpx.patch = lambda url, headers=None, json=None, **kw: calls["patch"].append(json) or type(
    "R", (), {"status_code": 204})()

# LONG trade, entry 1.1000, SL 1.0900 (1R = 0.0100). Last bar spikes to 1.1160
# high -> P1 (1R) + P2 (1.5R) + BE/lock/chandelier should all fire in one pass.
n = 30
base_closes = np.full(n, 1.1000)
base_closes[-1] = 1.1155
df = make_df(n, "1h", "2026-07-17 10:00", base_closes)
df.iloc[-1, df.columns.get_loc("high")] = 1.1160
df.iloc[-1, df.columns.get_loc("low")] = 1.1090
# give earlier bars a realistic range so ATR > 0
df.iloc[:-1, df.columns.get_loc("high")] = base_closes[:-1] + 0.0010
df.iloc[:-1, df.columns.get_loc("low")] = base_closes[:-1] - 0.0010

trade = {
    "id": "EURUSD_smoke1",
    "symbol": "EUR/USD",
    "verdict": "BUY",
    "price": 1.1000,
    "stop_loss": 1.0900,
    "target_price": 1.1300,
    "timeframe": "1d",
    "style": "swing",
    "created_at": "2026-07-17T08:00:00+00:00",
    "setup_features": {"auto": True, "managed_exits": True, "dispatched_volume": 1.00},
}

scanner.apply_trade_manager_tms(trade, df)

pc = calls["partial_close"]
ms = calls["modify_sl"]
check("one aggregated partial_close dispatched", len(pc) == 1, f"calls={pc}")
if pc:
    check("partial_close volume = 0.75 lots (50%+25% of 1.0)", abs(pc[0][2] - 0.75) < 1e-9,
          f"got {pc[0][2]}")
    check("partial_close ticket 12345", pc[0][1] == 12345)
check("modify_sl dispatched (BE/lock/chandelier collapsed to final stop)", len(ms) == 1,
      f"calls={ms}")
if ms:
    check("final stop above entry (locked profit)", ms[0][2] > 1.1000, f"stop={ms[0][2]}")
check("TMS state persisted via httpx.patch", len(calls["patch"]) == 1)
if calls["patch"]:
    sf = calls["patch"][0].get("setup_features", {})
    check("persisted tms_p1=True", sf.get("tms_p1") is True)
    check("persisted tms_p2=True", sf.get("tms_p2") is True)
    check("persisted tms_be=True", sf.get("tms_be") is True)
    check("persisted current_sl matches modify_sl", ms and abs(sf.get("current_sl", 0) - ms[0][2]) < 1e-4)
    check("tms_log records actions", len(sf.get("tms_log", [])) >= 2,
          f"actions={[a.get('action') for a in sf.get('tms_log', [])]}")

# Time-stop case: stagnant trade, bars_open beyond horizon, <0.25R -> close order
calls["submit_order"].clear(); calls["partial_close"].clear(); calls["modify_sl"].clear(); calls["patch"].clear()
old_trade = dict(trade)
old_trade["id"] = "EURUSD_smoke2"
old_trade["created_at"] = "2026-06-01T00:00:00+00:00"   # ~46 days old >> 10-bar 1d horizon
old_trade["setup_features"] = {"auto": True, "managed_exits": True, "dispatched_volume": 1.00}
flat_closes = np.full(n, 1.1010)   # +0.1R only -> stagnant
df_flat = make_df(n, "1h", "2026-07-17 10:00", flat_closes)
df_flat.iloc[:, df_flat.columns.get_loc("high")] = flat_closes + 0.0008
df_flat.iloc[:, df_flat.columns.get_loc("low")] = flat_closes - 0.0008
scanner.apply_trade_manager_tms(old_trade, df_flat)
closes = [c for c in calls["submit_order"] if c.get("cmd") == "close"]
check("time stop closes stagnant trade via submit_order cmd=close", len(closes) == 1,
      f"submit_order calls={calls['submit_order']}")

scanner._EXECUTOR = orig_executor
scanner._get_mt4_positions = orig_get_pos
scanner.httpx.patch = orig_patch

# ── D. HTF gate in scan_single_asset ─────────────────────────────────────────
print("\n[D] HTF gate (15m execution-only)")

now = pd.Timestamp.now(tz="UTC").floor("15min")
n_fast = 3000

# Sideways series: close sits ON the 20-period mean -> pullback trigger satisfied
sideways = 1.1000 + 0.0003 * np.sin(np.arange(n_fast) / 3.0)
# Sideways then a sharp terminal spike: last close far above SMA20 -> trigger NOT satisfied
rally = sideways.copy()
rally[-3:] += np.array([0.0100, 0.0180, 0.0250])

df_side = make_df(n_fast, "15min", now, sideways)
df_rally = make_df(n_fast, "15min", now, rally)
df_daily = make_df(320, "1D", pd.Timestamp.now(tz="UTC").floor("D"), 1.1000 * np.exp(np.cumsum(np.random.default_rng(7).normal(0, 0.004, 320))))


class FakeProvider:
    frame = df_side

    def get_history(self, instrument, start, end, timeframe):
        return self.frame.copy()


opened = []
orig = {k: getattr(scanner, k) for k in (
    "data_provider", "is_forex_market_open", "is_us_market_open",
    "apply_deepseek_structural_veto", "fetch_live_account_state",
    "fetch_resolved_trades_for_equity", "fetch_open_trades", "open_new_trade",
    "RiskManager", "_get_htf_direction",
)}

scanner.data_provider = FakeProvider()
scanner.is_forex_market_open = lambda: True
scanner.is_us_market_open = lambda: True
scanner.apply_deepseek_structural_veto = lambda sym, d, df, cfg: (True, "ok")
scanner.fetch_live_account_state = lambda *a, **kw: (100000.0, 100000.0, 100000.0)
scanner.fetch_resolved_trades_for_equity = lambda: []
scanner.fetch_open_trades = lambda: []
scanner.open_new_trade = lambda **kw: opened.append(kw) or True


class FakePermitted:
    permitted = True
    direction = Direction.LONG
    notional = 50000.0
    risk_fraction = 0.01
    units = 10000.0
    rationale = "fake"
    sizing_detail = {}
    constraints_applied = []


class FakeRiskManager:
    def __init__(self, *a, **kw):
        pass

    def permit(self, sig, account, market, t=None):
        return FakePermitted()


scanner.RiskManager = FakeRiskManager

import io, contextlib

item = {"instrument": "XXX/USD", "style": "scalp", "timeframe": "15m"}

# D1: HTF FLAT -> gate blocks, no entry even with pullback satisfied
scanner._get_htf_direction = lambda sym: ("FLAT", 0.5, 0.0)
scanner._reset_htf_direction_cache()   # per-cycle cache (audit M15) — reset per case
FakeProvider.frame = df_side
opened.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
out = buf.getvalue()
check("D1: HTF FLAT -> no entry", len(opened) == 0)
check("D1: gate logged", "[HTF GATE]" in out and "No 1d directional signal" in out)

# D2: HTF LONG but price extended -> waits, no entry
scanner._get_htf_direction = lambda sym: ("LONG", 0.62, 0.60)
scanner._reset_htf_direction_cache()
FakeProvider.frame = df_rally
opened.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
out = buf.getvalue()
check("D2: HTF LONG + no pullback -> no entry", len(opened) == 0)
check("D2: waiting-for-pullback logged", "waiting for pullback" in out)

# D3: HTF LONG + pullback satisfied -> entry in HTF direction with entry_origin
FakeProvider.frame = df_side
scanner._reset_htf_direction_cache()
opened.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
out = buf.getvalue()
check("D3: HTF LONG + pullback -> entry opened", len(opened) == 1, f"opened={opened}")
check("D3: [HTF-TIMED] logged", "[HTF-TIMED]" in out)
if opened:
    check("D3: direction is HTF LONG", str(opened[0]["direction"]).upper() == "LONG", f"got {opened[0]['direction']}")
    check("D3: entry_origin = htf_timed_15m", opened[0].get("entry_origin") == "htf_timed_15m",
          f"got {opened[0].get('entry_origin')}")
    check("D3: timeframe stays 15m (slot bucket preserved)", opened[0]["timeframe"] == "15m")

# D4: flag OFF -> fast TF trades its own signal again (legacy behaviour)
scanner._HTF_DIRECTION_ONLY = False
scanner._get_htf_direction = lambda sym: (_ for _ in ()).throw(AssertionError("must not be called"))
scanner._reset_htf_direction_cache()
FakeProvider.frame = df_side
opened.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
out = buf.getvalue()
check("D4: flag off -> HTF helper never called, no [HTF GATE] log", "[HTF GATE]" not in out)
scanner._HTF_DIRECTION_ONLY = True

# D5: managed active trade -> held (no invalidation close), no new entry
managed_open = {
    "id": "XXXUSD_smoke", "symbol": "XXX/USD", "verdict": "BUY",
    "price": 1.1, "stop_loss": 1.09, "target_price": 1.12, "timeframe": "15m",
    "setup_features": {"auto": True, "managed_exits": True},
}
val_calls = []
orig_add_val = scanner.add_validation_to_trade
scanner.add_validation_to_trade = lambda *a, **kw: val_calls.append((a, kw))
scanner._reset_htf_direction_cache()
opened.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {("XXX/USD", "15m"): managed_open})
out = buf.getvalue()
check("D5: managed trade held, no entry attempt", len(opened) == 0)
check("D5: managed_by_tms validation logged", any(kw.get("assessment") == "managed_by_tms" for a, kw in val_calls))
scanner.add_validation_to_trade = orig_add_val

for k, v in orig.items():
    setattr(scanner, k, v)

# ── E. _get_htf_direction runs offline ───────────────────────────────────────
print("\n[E] _get_htf_direction (stubbed provider, real strategy stack)")
scanner.data_provider = FakeProvider()
FakeProvider.frame = df_daily
res = scanner._get_htf_direction("XXX/USD")
check("returns (direction, prob, conf) tuple", isinstance(res, tuple) and len(res) == 3, f"got {res}")
check("direction in {LONG, SHORT, FLAT}", res[0] in ("LONG", "SHORT", "FLAT"), f"got {res[0]}")
scanner.data_provider = orig["data_provider"]

print("\n" + "=" * 70)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
print("=" * 70)
sys.exit(1 if FAIL else 0)
