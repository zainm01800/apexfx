"""Offline smoke test for the 2026-07-17 live-path hardening (audit fixes).

Covers (no MT4 terminal, no network, no daemon):
  A. Single-instance lock: second instance refused (exit 1), stale PID reaped.
  B. MT4Executor: unique signal_*.json per write; ticket embedded in close /
     partial_close / modify_sl; missing common_dir fails closed (no mkdir).
  C. Freshness gate: fresh EA files -> OK; stale files -> fail-closed.
  D. Fills handshake: dispatch -> fake EA writes ack -> ticket bound to the
     trade row + filled_at stamped; stale bridge -> NOT dispatched.
  E. Fail-closed sizing: risk pipeline raising SKIPS the trade.
  F. LLM structural veto gated OFF by default (and consulted when enabled).
  G. Ticket-scoped TMS: time-stop close carries the stored ticket, not the
     first symbol match.

Run:  cd engine && APEX_EXECUTION__ENABLED=false .venv-mac/bin/python scratch/smoke_live_hardening.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Execution disabled BEFORE import -> module import stays inert.
os.environ["APEX_EXECUTION__ENABLED"] = "false"

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import io
import contextlib

import numpy as np
import pandas as pd

import scripts.run_live_paper_trading as scanner
from apex_quant.execution.mt4_executor import MT4Executor
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


def write_fresh_bridge_files(d: Path):
    (d / "mt4_positions.json").write_text("[]")
    (d / "mt4_account.json").write_text('{"balance": 100, "equity": 100}')


print("=" * 70)
print("SMOKE TEST: live-path hardening (offline)")
print("=" * 70)

# ── A. Single-instance lock ──────────────────────────────────────────────────
print("\n[A] Single-instance lock (audit L1)")
with tempfile.TemporaryDirectory() as td:
    lock_path = Path(td) / "live_engine.lock"
    orig_lock_path, orig_held = scanner._LOCK_PATH, scanner._LOCK_HELD
    scanner._LOCK_PATH = lock_path
    scanner._LOCK_HELD = False
    try:
        scanner._acquire_instance_lock()
        check("lock acquired (file written with our PID)",
              lock_path.exists() and lock_path.read_text().strip() == str(os.getpid()))
        # Second instance: same live PID holds it -> SystemExit(1)
        scanner._LOCK_HELD = False
        exit_code = None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                scanner._acquire_instance_lock()
        except SystemExit as e:
            exit_code = e.code
        check("second instance refused with exit 1", exit_code == 1)
        check("refusal is loud (mentions PID + double-fire)",
              "already running" in buf.getvalue() and "DOUBLE-FIRE" in buf.getvalue())
        # Stale lock (dead PID) -> reaped and acquired
        dead = subprocess.Popen(["true"])
        dead.wait()
        lock_path.write_text(str(dead.pid))
        scanner._LOCK_HELD = False
        scanner._acquire_instance_lock()
        check("stale PID lock reaped and re-acquired",
              lock_path.read_text().strip() == str(os.getpid()))
        scanner._release_instance_lock()
        check("release removes the lockfile", not lock_path.exists())
    finally:
        scanner._LOCK_PATH = orig_lock_path
        scanner._LOCK_HELD = orig_held

# ── B. MT4Executor protocol ──────────────────────────────────────────────────
print("\n[B] MT4Executor: unique signals, ticket scoping, fail-closed dir")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    ex = MT4Executor(common_dir=d, default_volume=0.10)
    ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.25, sl=1.09, tp=1.12)
    ex.submit_order(symbol="GBPUSD", cmd="sell", volume=0.10)
    files = sorted(d.glob("signal_*.json"))
    check("two unique signal files written (no last-write-wins)", len(files) == 2,
          f"files={[f.name for f in files]}")
    payloads = [json.loads(f.read_text()) for f in files]
    ids = {p["id"] for p in payloads}
    check("each payload carries a distinct client order id", len(ids) == 2)
    check("no .tmp residue", not list(d.glob("*.tmp")))
    check("no legacy single-slot file", not (d / "mt4_signals.json").exists())
    buy = next(p for p in payloads if p["cmd"] == "buy")
    check("buy payload fields intact", buy["volume"] == 0.25 and buy["sl"] == 1.09 and buy["tp"] == 1.12)

    ex.close_position(symbol="EURUSD", ticket=424242)
    ex.close_position(symbol="EURUSD")
    ex.partial_close(symbol="EURUSD", ticket=777, volume=0.05)
    ex.modify_sl(symbol="EURUSD", ticket=777, new_sl=1.0955)
    payloads = [json.loads(f.read_text()) for f in sorted(d.glob("signal_*.json"))]
    closes = [p for p in payloads if p["cmd"] == "close"]
    check("ticket-scoped close embeds the ticket",
          any(p.get("ticket") == 424242 for p in closes))
    check("legacy close has no ticket field",
          any("ticket" not in p for p in closes))
    pc = next(p for p in payloads if p["cmd"] == "partial_close")
    ms = next(p for p in payloads if p["cmd"] == "modify_sl")
    check("partial_close carries ticket+volume", pc["ticket"] == 777 and pc["volume"] == 0.05)
    check("modify_sl carries ticket+new_sl", ms["ticket"] == 777 and ms["new_sl"] == 1.0955)

with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "wrong" / "path"
    try:
        MT4Executor(common_dir=missing, default_volume=0.10)
        raised = False
    except FileNotFoundError:
        raised = True
    check("missing common_dir fails closed (FileNotFoundError, no mkdir)", raised and not missing.exists())

# ── C. Freshness gate (audit L9/L10) ─────────────────────────────────────────
print("\n[C] MT4 bridge freshness gate")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    write_fresh_bridge_files(d)
    os.environ["MT4_COMMON_DIR"] = td
    scanner._MT4_STALE_LOGGED = False
    check("fresh files -> bridge OK", scanner._check_mt4_bridge_fresh() is True)
    old = time.time() - 3600
    for f in ("mt4_positions.json", "mt4_account.json"):
        os.utime(d / f, (old, old))
    check("stale files (1h old) -> fail closed", scanner._check_mt4_bridge_fresh() is False)
    write_fresh_bridge_files(d)
    check("fresh again -> bridge OK", scanner._check_mt4_bridge_fresh() is True)
    os.environ.pop("MT4_COMMON_DIR", None)
with tempfile.TemporaryDirectory() as td:
    os.environ["MT4_COMMON_DIR"] = str(Path(td) / "missing")
    scanner._MT4_STALE_LOGGED = False
    check("missing common_dir -> fail closed (never created)",
          scanner._check_mt4_bridge_fresh() is False and not (Path(td) / "missing").exists())
    os.environ.pop("MT4_COMMON_DIR", None)

# ── D. Fills handshake (audit L3/L4/L8/L10) ─────────────────────────────────
print("\n[D] Fills handshake: dispatch -> ack -> ticket bound; stale -> no dispatch")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    write_fresh_bridge_files(d)
    os.environ["MT4_COMMON_DIR"] = td

    real_exec = MT4Executor(common_dir=d, default_volume=0.10)
    orig = {k: getattr(scanner, k) for k in (
        "_EXECUTOR", "_MT4_ACK_TIMEOUT_S", "_TICKET_COLUMN_OK", "_MT4_STALE_LOGGED",
    )}
    orig_post, orig_patch = scanner.httpx.post, scanner.httpx.patch
    posts, patches = [], []

    class FakeResp:
        status_code = 201
        text = ""

    scanner.httpx.post = lambda url, headers=None, json=None, **kw: posts.append(json) or FakeResp()
    scanner.httpx.patch = lambda url, headers=None, json=None, **kw: patches.append(json) or FakeResp()
    scanner._EXECUTOR = real_exec
    scanner._MT4_ACK_TIMEOUT_S = 3.0
    scanner._TICKET_COLUMN_OK = False     # ticket column absent -> setup_features only
    scanner._MT4_STALE_LOGGED = False

    def fake_ea(stop_after=10.0):
        """Watch for a signal file and answer with an ack — mimics EA v1.10."""
        deadline = time.time() + stop_after
        while time.time() < deadline:
            sigs = sorted(d.glob("signal_*.json"))
            if sigs:
                payload = json.loads(sigs[0].read_text())
                sid = payload["id"]
                ack = {"id": sid, "cmd": payload["cmd"], "symbol": payload["symbol"],
                       "ticket": 777001, "fill_price": 1.23456, "ok": True}
                (d / f"ack_{sid}.json").write_text(json.dumps(ack))
                return
            time.sleep(0.05)

    ea_thread = threading.Thread(target=fake_ea)
    ea_thread.start()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = scanner.open_new_trade(
            symbol="EUR/USD", direction="LONG", entry_price=1.2345,
            stop_loss=1.2300, target_price=1.2430, timeframe="1d",
            confidence=60, rr=2.0, volume=0.15, style="swing",
        )
    ea_thread.join()
    check("trade row created (POST)", ok is True and len(posts) == 1)
    sig_files = sorted(d.glob("signal_*.json"))
    check("signal written as unique signal_<id>.json", len(sig_files) == 1,
          f"{[f.name for f in sig_files]}")
    ack_patches = [p for p in patches if p and "filled_at" in p]
    check("filled_at stamped ONLY after ack", len(ack_patches) == 1)
    if ack_patches:
        sf = ack_patches[0].get("setup_features", {})
        check("ack ticket bound to the trade row", sf.get("mt4_ticket") == 777001,
              f"setup_features={sf}")
        check("fill price recorded from ack", sf.get("fill_price") == 1.23456)

    # D2: stale bridge -> dispatch skipped, nothing written, no filled_at
    posts.clear(); patches.clear()
    old = time.time() - 3600
    for f in ("mt4_positions.json", "mt4_account.json"):
        os.utime(d / f, (old, old))
    scanner._MT4_STALE_LOGGED = False
    n_signals_before = len(list(d.glob("signal_*.json")))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scanner.open_new_trade(
            symbol="EUR/USD", direction="LONG", entry_price=1.2345,
            stop_loss=1.2300, target_price=1.2430, timeframe="1d",
            confidence=60, rr=2.0, volume=0.15, style="swing",
        )
    out = buf.getvalue()
    check("stale bridge -> no new signal file", len(list(d.glob("signal_*.json"))) == n_signals_before)
    check("stale bridge -> fail-closed logged", "NOT dispatched" in out)
    check("stale bridge -> no filled_at patch", not any(p and "filled_at" in p for p in patches))

    # D3: fresh bridge but NO ack (old EA) -> dispatched, filled_at NOT stamped
    posts.clear(); patches.clear()
    write_fresh_bridge_files(d)
    scanner._MT4_STALE_LOGGED = False
    scanner._MT4_ACK_TIMEOUT_S = 1.0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scanner.open_new_trade(
            symbol="EUR/USD", direction="LONG", entry_price=1.2345,
            stop_loss=1.2300, target_price=1.2430, timeframe="1d",
            confidence=60, rr=2.0, volume=0.15, style="swing",
        )
    out = buf.getvalue()
    check("no-ack -> order still dispatched", len(list(d.glob("signal_*.json"))) == n_signals_before + 1)
    check("no-ack -> filled_at NOT stamped", not any(p and "filled_at" in p for p in patches))
    check("no-ack -> loud warning", "No fill ack" in out)

    scanner._EXECUTOR = orig["_EXECUTOR"]
    scanner._MT4_ACK_TIMEOUT_S = orig["_MT4_ACK_TIMEOUT_S"]
    scanner._TICKET_COLUMN_OK = orig["_TICKET_COLUMN_OK"]
    scanner._MT4_STALE_LOGGED = orig["_MT4_STALE_LOGGED"]
    scanner.httpx.post, scanner.httpx.patch = orig_post, orig_patch
    os.environ.pop("MT4_COMMON_DIR", None)

# ── E. Fail-closed sizing (audit L6) ─────────────────────────────────────────
print("\n[E] Fail-closed sizing: risk exception -> trade SKIPPED")

now = pd.Timestamp.now(tz="UTC").floor("15min")
n_fast = 3000
sideways = 1.1000 + 0.0003 * np.sin(np.arange(n_fast) / 3.0)
df_side = make_df(n_fast, "15min", now, sideways)


class FakeProvider:
    frame = df_side

    def get_history(self, instrument, start, end, timeframe):
        return self.frame.copy()


opened = []
orig2 = {k: getattr(scanner, k) for k in (
    "data_provider", "is_forex_market_open", "is_us_market_open",
    "apply_deepseek_structural_veto", "fetch_live_account_state",
    "fetch_resolved_trades_for_equity", "fetch_open_trades", "open_new_trade",
    "RiskManager", "_get_htf_direction",
)}

scanner.data_provider = FakeProvider()
scanner.is_forex_market_open = lambda: True
scanner.is_us_market_open = lambda: True
scanner.fetch_live_account_state = lambda *a, **kw: (100000.0, 100000.0, 100000.0)
scanner.fetch_resolved_trades_for_equity = lambda: []
scanner.fetch_open_trades = lambda: []
scanner.open_new_trade = lambda **kw: opened.append(kw) or True


class ExplodingRiskManager:
    def __init__(self, *a, **kw):
        pass

    def permit(self, sig, account, market, t=None):
        raise RuntimeError("boom — simulated sizing failure")


scanner.RiskManager = ExplodingRiskManager
scanner._get_htf_direction = lambda sym: ("LONG", 0.62, 0.60)
scanner._reset_htf_direction_cache()

item = {"instrument": "XXX/USD", "style": "scalp", "timeframe": "15m"}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
out = buf.getvalue()
check("sizing exception -> open_new_trade NEVER called", len(opened) == 0)
check("sizing exception -> FAIL-CLOSED logged", "FAIL-CLOSED" in out and "SKIPPED" in out)

# ── F. LLM structural veto gate (audit A-C1) ─────────────────────────────────
print("\n[F] LLM structural veto gate")
check("veto flag defaults to FALSE", scanner._LLM_STRUCTURAL_VETO is False)

veto_calls = []
scanner.apply_deepseek_structural_veto = lambda sym, d, df, cfg: veto_calls.append((sym, d)) or (True, "ok")


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

veto_calls.clear(); opened.clear()
scanner._reset_htf_direction_cache()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
check("flag OFF -> veto never consulted during scan", len(veto_calls) == 0)

scanner._LLM_STRUCTURAL_VETO = True
veto_calls.clear(); opened.clear()
scanner._reset_htf_direction_cache()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    scanner.scan_single_asset(item, {})
check("flag ON -> veto consulted", len(veto_calls) >= 1, f"calls={veto_calls}")
scanner._LLM_STRUCTURAL_VETO = False

for k, v in orig2.items():
    setattr(scanner, k, v)

# ── G. Ticket-scoped TMS (audit L3/L4) ───────────────────────────────────────
print("\n[G] TMS time-stop close carries the stored ticket (not first symbol match)")

calls = {"submit_order": [], "partial_close": [], "modify_sl": [], "patch": []}


class RecExecutor:
    def submit_order(self, **kw):
        calls["submit_order"].append(kw)

    def partial_close(self, symbol, ticket, volume):
        calls["partial_close"].append((symbol, ticket, volume))

    def modify_sl(self, symbol, ticket, new_sl):
        calls["modify_sl"].append((symbol, ticket, new_sl))


orig3 = {k: getattr(scanner, k) for k in ("_EXECUTOR", "_get_mt4_positions")}
orig_patch2 = scanner.httpx.patch
scanner._EXECUTOR = RecExecutor()
# Sibling position (ticket 999) deliberately listed FIRST on the same symbol —
# legacy symbol matching would manage 999; the ticket stamp must win.
scanner._get_mt4_positions = lambda: [
    {"symbol": "EURUSD-g", "ticket": 999, "volume": 5.00, "sl": 1.0900, "cmd": 0},
    {"symbol": "EURUSD-g", "ticket": 424242, "volume": 1.00, "sl": 1.0900, "cmd": 0},
]
scanner.httpx.patch = lambda url, headers=None, json=None, **kw: calls["patch"].append(json) or type(
    "R", (), {"status_code": 204})()

n = 30
flat_closes = np.full(n, 1.1010)   # +0.1R only -> stagnant
df_flat = make_df(n, "1h", "2026-07-17 10:00", flat_closes)
df_flat.iloc[:, df_flat.columns.get_loc("high")] = flat_closes + 0.0008
df_flat.iloc[:, df_flat.columns.get_loc("low")] = flat_closes - 0.0008

trade = {
    "id": "EURUSD_smoke_ticket",
    "symbol": "EUR/USD",
    "verdict": "BUY",
    "price": 1.1000,
    "stop_loss": 1.0900,
    "target_price": 1.1300,
    "timeframe": "1d",
    "style": "swing",
    "created_at": "2026-06-01T00:00:00+00:00",   # ancient -> time stop fires
    "setup_features": {"auto": True, "managed_exits": True,
                       "dispatched_volume": 1.00, "mt4_ticket": 424242},
}

scanner.apply_trade_manager_tms(trade, df_flat)
closes = [c for c in calls["submit_order"] if c.get("cmd") == "close"]
check("time-stop close dispatched", len(closes) == 1, f"calls={calls['submit_order']}")
if closes:
    check("close carries the STORED ticket 424242 (not first-match 999)",
          closes[0].get("ticket") == 424242, f"got {closes[0].get('ticket')}")

# Legacy trade (no ticket stamp) -> first symbol match, no ticket kwarg
calls["submit_order"].clear(); calls["patch"].clear()
legacy = dict(trade)
legacy["id"] = "EURUSD_smoke_legacy"
legacy["setup_features"] = {"auto": True, "managed_exits": True, "dispatched_volume": 5.00}
scanner.apply_trade_manager_tms(legacy, df_flat)
closes = [c for c in calls["submit_order"] if c.get("cmd") == "close"]
check("legacy trade still closes (symbol-matched)", len(closes) == 1)
if closes:
    check("legacy close uses the first-match ticket 999", closes[0].get("ticket") == 999,
          f"got {closes[0].get('ticket')}")
if calls["patch"]:
    sf = calls["patch"][-1].get("setup_features", {})
    check("legacy trade marked legacy_unticketed", sf.get("legacy_unticketed") is True)

scanner._EXECUTOR = orig3["_EXECUTOR"]
scanner._get_mt4_positions = orig3["_get_mt4_positions"]
scanner.httpx.patch = orig_patch2

print("\n" + "=" * 70)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
print("=" * 70)
sys.exit(1 if FAIL else 0)
