"""Exit decomposition on the certified Book H gold book — MEASUREMENT ONLY, informational.

Re-runs the certified gap-aware anchor (book_h_gapaware_2026-07-22.json: Sharpe 0.86284,
1637 trades, final equity 292,551.34; mrpt 0.01, certified panel insertion order
EQUITY_CORE first, exit_mode=managed, warmup=250, seed 42, iteration window strictly
< 2025-01-01) and captures every trade's full entry state (entry price/idx, initial stop,
target, units) by spying on PortfolioBacktester._enter / ._record — the engine code path
is byte-identical to the certified run, and the run hard-fails if the certified metrics
do not reproduce to the digit.

For each of the 1637 closed trades, counterfactual P&L is then computed under alternative
exit rules using the SAME entry fill, the SAME stop distance and the SAME daily bars:

  ladder_control      certified TradeManager replayed trade-by-trade (the CONTROL —
                      per-trade P&L must match the recorded trade P&L to ~1e-6, and the
                      total must match the certified net_pnl, before any variant number
                      is believed)
  fixed_day_{1,2,3,5,10}  pure time exit: close 100% at the close of day N after entry
                      (the entry bar — filled at its open — counts as day 1); no stop,
                      no target
  flat_r_{0.50,0.75}  close 100% on the first touch of +0.5R / +0.75R (stop checked
                      first, gap-aware — the engine's conservative order), else the
                      initial stop, else a hard 21-bar cap at the close
  stop_only           no partials, no trail: initial stop (gap-aware) or the 21-bar cap
  trail_only_1R       no partials and no fixed target: breakeven stop on the +1R touch,
                      then the certified chandelier (22-bar swing, 2xATR) + squeeze
                      tighten (1xATR) + the manager time-stop (>21 bars and < +0.25R)

Costs are the engine's own fills (PortfolioBacktester._fill / _pip / mechanics), one
commission per close transaction exactly as the engine charges. This is a decomposition
on the certified trade SET — entries and sizes are the certified ones; there is no
portfolio interaction, no re-simulation of caps. It is NOT a gate: exactly ONE
measurement trial (kind "exit_decomposition") is recorded before the run, and no
DSR/PBO verdict is computed or implied.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_exit_decomposition.py                # measurement
    .venv-mac/bin/python scripts/run_exit_decomposition.py --no-ledger    # smoke
    .venv-mac/bin/python scripts/run_exit_decomposition.py --out <twin>   # determinism rerun
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.strategies.labeling import atr_series  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "exit_decomposition_2026-07-25.json"

# Certified-anchor reproduction targets (book_h_gapaware_2026-07-22.json, gold).
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

FIXED_DAYS = [1, 2, 3, 5, 10]
FLAT_RS = [0.50, 0.75]
TIME_CAP_BARS = 21          # the book's holding horizon / barrier max_hold
CHANDELIER_MULT = 2.0       # certified TradeManager defaults
SQUEEZE_MULT = 1.0
BE_BUFFER_PIPS = 3.0

# ── entry/exit spies (engine code path untouched) ─────────────────────────────
ENTRY_SNAPS: dict[int, dict] = {}
EXIT_RECS: dict[int, dict] = {}
_SEQ = [0]

_orig_enter = PortfolioBacktester._enter
_orig_record = PortfolioBacktester._record


def _spy_enter(self, pend, open_price, t, i, instrument):
    posd = _orig_enter(self, pend, open_price, t, i, instrument)
    did = _SEQ[0]
    _SEQ[0] += 1
    posd["_decomp_id"] = did
    direction = posd["direction"]
    ENTRY_SNAPS[did] = {
        "instrument": instrument,
        "direction": direction.value if hasattr(direction, "value") else str(direction),
        "units": float(posd["units"]),
        "entry_price": float(posd["entry_price"]),
        "entry_time": str(pd.Timestamp(t).date()),
        "entry_idx": int(i),
        "initial_stop": float(posd["initial_stop"]),
        "target": float(posd["target"]),
        "risk_abs": float(posd["risk_abs"]),
        "tf": posd["tf"],
    }
    return posd


def _spy_record(self, position, exit_price, t, reason, pnl, instrument=""):
    tr = _orig_record(self, position, exit_price, t, reason, pnl, instrument)
    did = position.get("_decomp_id")
    if did is not None:
        EXIT_RECS[did] = {"exit_time": str(pd.Timestamp(t).date()),
                          "exit_reason": reason, "trade_pnl": float(pnl)}
    return tr


# ── per-instrument bar packs, computed with the engine's own formulas ─────────
def _bar_pack(bt: PortfolioBacktester, df: pd.DataFrame) -> dict:
    close = df["close"]
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - close.shift(1)).abs(),
        (df["low"] - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    kc_atr = tr.rolling(20).mean()
    kc_upper = bb_mid + 1.5 * kc_atr
    kc_lower = bb_mid - 1.5 * kc_atr
    squeeze = ((bb_upper < kc_upper) & (bb_lower > kc_lower)).to_numpy()
    return {
        "open": df["open"].to_numpy(),
        "high": df["high"].to_numpy(),
        "low": df["low"].to_numpy(),
        "close": close.to_numpy(),
        "atr": atr_series(df, bt.cfg.risk.atr_window),
        "squeeze": squeeze,
    }


# ── variant simulators ────────────────────────────────────────────────────────
def _fill_for(bt, inst, tf):
    def fill(price, buying):
        return bt._fill(price, inst, buying, timeframe=tf)
    return fill


def replay_control(snap, bt, bars, hold):
    """The certified ladder, replayed through the REAL TradeManager with the same
    per-bar calls the engine makes (management starts the bar AFTER the entry bar).
    Commission accounting mirrors PortfolioBacktester.run exactly."""
    inst, tf = snap["instrument"], snap["tf"]
    comm = bt._mech(inst).commission_per_trade
    posd = {
        "symbol": inst,
        "direction": snap["direction"],
        "units": snap["units"],
        "initial_units": snap["units"],
        "entry_price": snap["entry_price"],
        "stop": snap["initial_stop"],
        "initial_stop": snap["initial_stop"],
        "target": snap["target"],
    }
    tm = TradeManager()  # certified defaults
    TradeManager.init_position_tms(tm, posd)
    fill = _fill_for(bt, inst, tf)
    pip = bt._pip(inst)
    total = -comm
    n = len(bars["close"])
    j = snap["entry_idx"] + 1
    while j < n:
        high_w = bars["high"][max(0, j - 21):j + 1]
        low_w = bars["low"][max(0, j - 21):j + 1]
        bars_history = {"high": float(high_w.max()), "low": float(low_w.min()), "len": j + 1}
        rp, reason = tm.update_position(
            position=posd, high=bars["high"][j], open_=bars["open"][j],
            low=bars["low"][j], close=bars["close"][j], atr=bars["atr"][j],
            is_squeeze=bool(bars["squeeze"][j]), bars_history=bars_history,
            timeframe=tf, pip_size=pip, fill_fn=fill, max_bars=hold)
        if rp != 0.0 or reason != "":
            total += rp - comm
        if reason != "":
            return {"pnl": total, "exit_idx": j, "exit_reason": reason}
        j += 1
    return {"pnl": total, "exit_idx": n - 1, "exit_reason": "open_at_end"}


def _sim_simple(snap, bt, bars, *, r_target=None, with_stop=True, day_n=None):
    """Shared loop for fixed-day / flat-R / stop-only counterfactuals.

    Exits are evaluated starting the bar AFTER the entry bar (engine management
    timing). Stop is checked first (gap-aware fill at the worse of stop/open),
    then the flat-R level (filled at the level, engine convention for profit
    exits), then the 21-bar cap at the close. fixed_day variants are pure time
    exits: close at the close of day N (entry bar = day 1), no stop.
    """
    inst, tf = snap["instrument"], snap["tf"]
    comm = bt._mech(inst).commission_per_trade
    fill = _fill_for(bt, inst, tf)
    is_long = snap["direction"] == "long"
    e, u = snap["entry_price"], snap["units"]
    stop = snap["initial_stop"]
    rd = abs(e - stop)
    if rd <= 1e-8:
        rd = 0.01 * e
    lvl = None
    if r_target is not None:
        lvl = (e + r_target * rd) if is_long else (e - r_target * rd)
    n = len(bars["close"])
    E = snap["entry_idx"]

    if day_n is not None:
        j = min(E + day_n - 1, n - 1)
        px = fill(bars["close"][j], not is_long)
        pnl = ((px - e) if is_long else (e - px)) * u
        return {"pnl": pnl - 2 * comm, "exit_idx": j, "exit_reason": "fixed_day"}

    j = E + 1
    while j < n:
        hi, lo, op, cl = bars["high"][j], bars["low"][j], bars["open"][j], bars["close"][j]
        if with_stop:
            if is_long and lo <= stop:
                px = fill(min(stop, op), False)
                return {"pnl": (px - e) * u - 2 * comm, "exit_idx": j, "exit_reason": "stop"}
            if not is_long and hi >= stop:
                px = fill(max(stop, op), True)
                return {"pnl": (e - px) * u - 2 * comm, "exit_idx": j, "exit_reason": "stop"}
        if r_target is not None:
            if is_long and hi >= lvl:
                px = fill(lvl, False)
                return {"pnl": (px - e) * u - 2 * comm, "exit_idx": j, "exit_reason": "r_target"}
            if not is_long and lo <= lvl:
                px = fill(lvl, True)
                return {"pnl": (e - px) * u - 2 * comm, "exit_idx": j, "exit_reason": "r_target"}
        if j - E >= TIME_CAP_BARS:
            px = fill(cl, not is_long)
            pnl = ((px - e) if is_long else (e - px)) * u
            return {"pnl": pnl - 2 * comm, "exit_idx": j, "exit_reason": "time"}
        j += 1
    px = fill(bars["close"][n - 1], not is_long)
    pnl = ((px - e) if is_long else (e - px)) * u
    return {"pnl": pnl - 2 * comm, "exit_idx": n - 1, "exit_reason": "window_end"}


def sim_trail_only(snap, bt, bars, hold):
    """No partials, no fixed target: breakeven stop on the +1R touch, then the
    certified chandelier (22-bar swing, 2xATR) + squeeze tighten (1xATR) + the
    TradeManager time-stop (>hold bars and < +0.25R -> close). Mirrors
    TradeManager.update_position with the partial sizes set to zero."""
    inst, tf = snap["instrument"], snap["tf"]
    comm = bt._mech(inst).commission_per_trade
    fill = _fill_for(bt, inst, tf)
    is_long = snap["direction"] == "long"
    e, u = snap["entry_price"], snap["units"]
    stop = snap["initial_stop"]
    rd = abs(e - stop)
    if rd <= 1e-8:
        rd = 0.01 * e
    be_buffer = BE_BUFFER_PIPS * bt._pip(inst)
    p1 = (e + 1.0 * rd) if is_long else (e - 1.0 * rd)
    be = False
    bars_open = 0
    n = len(bars["close"])
    E = snap["entry_idx"]
    j = E + 1
    while j < n:
        bars_open += 1
        hi, lo, op, cl = bars["high"][j], bars["low"][j], bars["open"][j], bars["close"][j]
        atr = bars["atr"][j]
        # full stop-out first (gap-aware), as TradeManager
        if is_long and lo <= stop:
            px = fill(min(stop, op), False)
            return {"pnl": (px - e) * u - 2 * comm, "exit_idx": j, "exit_reason": "stop"}
        if not is_long and hi >= stop:
            px = fill(max(stop, op), True)
            return {"pnl": (e - px) * u - 2 * comm, "exit_idx": j, "exit_reason": "stop"}
        # +1R touch -> breakeven stop (no partial close)
        if not be and ((is_long and hi >= p1) or (not is_long and lo <= p1)):
            be = True
            stop = (e + be_buffer) if is_long else (e - be_buffer)
        # chandelier trail (after the +1R touch; needs 22-bar history)
        if be and atr > 0 and j + 1 >= 22:
            if is_long:
                swing_max = float(bars["high"][max(0, j - 21):j + 1].max())
                chandelier = swing_max - CHANDELIER_MULT * atr
                if chandelier > stop and chandelier < cl:
                    stop = chandelier
            else:
                swing_min = float(bars["low"][max(0, j - 21):j + 1].min())
                chandelier = swing_min + CHANDELIER_MULT * atr
                if chandelier < stop and chandelier > cl:
                    stop = chandelier
        # volatility squeeze tighten (after the +1R touch)
        if be and atr > 0 and bool(bars["squeeze"][j]):
            tight = SQUEEZE_MULT * atr
            if is_long:
                tight_sl = cl - tight
                if tight_sl > stop and tight_sl < cl:
                    stop = tight_sl
            else:
                tight_sl = cl + tight
                if tight_sl < stop and tight_sl > cl:
                    stop = tight_sl
        # TradeManager time-stop: kill stagnant trades
        cur_r = ((cl - e) if is_long else (e - cl)) / rd
        if bars_open > hold and cur_r < 0.25:
            px = fill(cl, not is_long)
            pnl = ((px - e) if is_long else (e - px)) * u
            return {"pnl": pnl - 2 * comm, "exit_idx": j, "exit_reason": "time"}
        j += 1
    px = fill(bars["close"][n - 1], not is_long)
    pnl = ((px - e) if is_long else (e - px)) * u
    return {"pnl": pnl - 2 * comm, "exit_idx": n - 1, "exit_reason": "window_end"}


# ── aggregation ───────────────────────────────────────────────────────────────
def aggregate(per_trade: list[dict], n_months: int) -> dict:
    pnls = np.array([r["pnl"] for r in per_trade], dtype=float)
    rs = np.array([r["r"] for r in per_trade], dtype=float)
    holds = np.array([r["exit_idx"] - r["entry_idx"] + 1 for r in per_trade], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    reasons: dict[str, int] = {}
    for r in per_trade:
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
    return {
        "n_trades": int(len(pnls)),
        "net_pnl": round(float(pnls.sum()), 2),
        "win_rate": round(float(len(wins) / len(pnls)), 6) if len(pnls) else 0.0,
        "expectancy_pnl": round(float(pnls.mean()), 2),
        "expectancy_r": round(float(rs.mean()), 4),
        "profit_factor": (round(float(wins.sum() / abs(losses.sum())), 6)
                          if losses.size and abs(losses.sum()) > 0 else None),
        "avg_hold_days": round(float(holds.mean()), 2),
        "max_single_trade_loss": round(float(pnls.min()), 2),
        "pnl_per_month_100k": round(float(pnls.sum() / n_months), 2),
        "exit_reasons": dict(sorted(reasons.items())),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Exit decomposition on the certified Book H "
                                             "gold trade set (measurement, informational).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record the measurement trial")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)

    # Certified panel, insertion order EQUITY_CORE first (ordering artifact is known and
    # intentional — the certified numbers are ordering-sensitive).
    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        master[inst] = df
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments")
        return 1

    # Record the ONE measurement trial BEFORE running (dedup-safe on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        ledger.record({"book": "book_h_gold_252", "universe": "book_h_gold_39",
                       "timeframe": "1d", "factory": "trend_book_mtf",
                       "kind": "exit_decomposition", "max_risk_per_trade": CERTIFIED_MRPT,
                       "informational": True,
                       "params": {**GOLD_PARAMS,
                                  "variants": ["ladder_control"]
                                              + [f"fixed_day_{d}" for d in FIXED_DAYS]
                                              + [f"flat_r_{r:.2f}" for r in FLAT_RS]
                                              + ["stop_only", "trail_only_1R"]}})
        ledger.save(LEDGER_PATH)
    print("=" * 72, flush=True)
    print(f"EXIT DECOMPOSITION (BOOK H GOLD, certified anchor) 2026-07-25 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"panel: {len(panel)} instruments | ledger n_trials {n_before} -> "
          f"{ledger.n_trials if not args.no_ledger else n_before} (1 measurement trial, "
          f"informational — no gate)")
    print("=" * 72, flush=True)

    # 1. Certified run with spies installed.
    PortfolioBacktester._enter = _spy_enter
    PortfolioBacktester._record = _spy_record
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    model = TrendBook(panel, **GOLD_PARAMS)
    strategies = model.strategies()
    hold_map = {inst: int(getattr(strategies[inst], "holding_horizon", 20)) for inst in panel}
    t0 = time.time()
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, strategies, timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
    dt = time.time() - t0
    m = res.metrics
    print(f"certified run: {dt:.0f}s | {res.summary()}", flush=True)

    # Hard certified-anchor reproduction check — nothing below is believed without this.
    mismatch = {k: (m.get(k), v) for k, v in CERTIFIED_GOLD.items()
                if abs(m.get(k, float("nan")) - v) > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
        return 1
    print(f"certified-anchor reproduction: EXACT (sharpe {m['sharpe']:.5f}, "
          f"{m['n_trades']} trades, final_equity {m['final_equity']:.2f})", flush=True)

    # 2. Pair captured entries with recorded exits.
    paired = [(ENTRY_SNAPS[did], EXIT_RECS[did]) for did in EXIT_RECS if did in ENTRY_SNAPS]
    n_unclosed = len(ENTRY_SNAPS) - len(EXIT_RECS)
    print(f"trades recorded: {len(EXIT_RECS)} | entries captured: {len(ENTRY_SNAPS)} "
          f"({n_unclosed} still open at window end — excluded)", flush=True)

    # 3. Control replay — per-trade parity against the recorded P&L.
    bt = PortfolioBacktester(cfg, exit_mode="managed")
    bar_packs = {inst: _bar_pack(bt, df) for inst, df in panel.items()}
    control_trades: list[dict] = []
    parity_diffs: list[float] = []
    for snap, rec in paired:
        bars = bar_packs[snap["instrument"]]
        out = replay_control(snap, bt, bars, hold_map[snap["instrument"]])
        parity_diffs.append(abs(out["pnl"] - rec["trade_pnl"]))
        control_trades.append({**snap, **out})
    max_diff = float(max(parity_diffs)) if parity_diffs else 0.0
    control_total = float(sum(t["pnl"] for t in control_trades))
    recorded_total = float(sum(rec["trade_pnl"] for _, rec in paired))
    print(f"control replay parity: max |replay - recorded| = {max_diff:.6f} | "
          f"replay total {control_total:.2f} vs recorded {recorded_total:.2f} "
          f"(certified net_pnl {m['net_pnl']:.2f})", flush=True)
    if max_diff > 0.05 or len(control_trades) != m["n_trades"]:
        print("CONTROL PARITY FAILED — aborting before any variant number", flush=True)
        return 1

    n_months = int(res.equity.groupby(res.equity.index.to_period("M")).ngroups)

    def with_r(snap, out):
        e = snap["entry_price"]
        rd = abs(e - snap["initial_stop"])
        if rd <= 1e-8:
            rd = 0.01 * e
        risk_gbp = snap["units"] * rd
        return {**snap, **out, "r": (out["pnl"] / risk_gbp) if risk_gbp > 0 else 0.0}

    # 4. Variants on the same trade set.
    variants: dict[str, list[dict]] = {"ladder_control": [with_r(t, t) for t in control_trades]}
    for snap, _rec in paired:
        bars = bar_packs[snap["instrument"]]
        hold = hold_map[snap["instrument"]]
        for d in FIXED_DAYS:
            variants.setdefault(f"fixed_day_{d}", []).append(
                with_r(snap, _sim_simple(snap, bt, bars, day_n=d)))
        for r in FLAT_RS:
            variants.setdefault(f"flat_r_{r:.2f}", []).append(
                with_r(snap, _sim_simple(snap, bt, bars, r_target=r)))
        variants.setdefault("stop_only", []).append(
            with_r(snap, _sim_simple(snap, bt, bars)))
        variants.setdefault("trail_only_1R", []).append(
            with_r(snap, sim_trail_only(snap, bt, bars, hold)))

    table = {name: aggregate(trades, n_months) for name, trades in variants.items()}

    base = table["ladder_control"]
    print(f"\nn_months on the certified equity curve: {n_months} | "
          f"control £/mo £{base['pnl_per_month_100k']:,.2f} "
          f"(early-partial gate cross-check: £1,782.88)", flush=True)
    hdr = (f"{'variant':<16} {'net_pnl':>12} {'vs_base/mo':>11} {'win%':>6} {'exp£':>8} "
           f"{'expR':>7} {'PF':>6} {'hold':>5} {'worst':>10}")
    print(hdr, flush=True)
    for name, row in table.items():
        delta = row["pnl_per_month_100k"] - base["pnl_per_month_100k"]
        print(f"{name:<16} {row['net_pnl']:>12,.0f} {delta:>+11,.2f} "
              f"{row['win_rate']*100:>5.1f}% {row['expectancy_pnl']:>8,.2f} "
              f"{row['expectancy_r']:>7.3f} {row['profit_factor'] or 0:>6.3f} "
              f"{row['avg_hold_days']:>5.1f} {row['max_single_trade_loss']:>10,.0f}",
              flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "kind": "exit_decomposition",
        "informational": True,
        "gate": None,
        "note": ("Measurement only: counterfactual exits on the certified trade set. "
                 "No DSR/PBO verdict is computed or implied — this is not a gate."),
        "holdout_start": args.holdout_start,
        "certified_anchor": "engine/data_store/validation/book_h_gapaware_2026-07-22.json",
        "certified_reproduction": "EXACT",
        "ledger_n_trials_before": n_before,
        "ledger_n_trials_after": ledger.n_trials if not args.no_ledger else n_before,
        "n_trades": len(control_trades),
        "n_entries_open_at_window_end": n_unclosed,
        "n_months": n_months,
        "control_parity": {"max_abs_per_trade_diff": max_diff,
                           "replay_total": round(control_total, 2),
                           "recorded_total": round(recorded_total, 2),
                           "certified_net_pnl": m["net_pnl"]},
        "variant_rules": {
            "ladder_control": "certified TradeManager replayed per trade (50% @ +1R + BE, "
                              "25% @ +1.5R lock, 2xATR chandelier, squeeze tighten, 21-bar time-stop)",
            "fixed_day_N": "pure time exit at the close of day N (entry bar = day 1); no stop/target",
            "flat_r_R": "100% close on first touch of +R (stop checked first, gap-aware), "
                        "else initial stop, else 21-bar cap at close",
            "stop_only": "initial stop (gap-aware) or 21-bar cap at close; no partials/trail",
            "trail_only_1R": "no partials, no fixed target; BE stop on +1R touch, then 2xATR "
                             "chandelier + 1xATR squeeze tighten + manager time-stop",
        },
        "variants": table,
        "baseline_metrics_certified": m,
    }
    results_path = Path(args.out)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nresults written to {results_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
