"""Pre-registered gate: exit-side earnings de-risk on the Book H gold equity sleeve.

Prereg: engine/data_store/earnings_derisk_gate_prereg.md (2026-08-08, BEFORE this run).

On the last bar BEFORE a covered earnings date, exit a fraction of any open position in that
single-name stock at the bar's close (before TradeManager management). Control = certified
equity sleeve (reproduction hard-check: Sharpe ~0.9955, 1546 trades per the 2026-07-24
blackout-gate control).
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

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _gate,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC, STOCKS_12  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "earnings_derisk_gate_2026-08-08.json"
EARNINGS_DIR = ENGINE_DIR / "data_store" / "earnings_calendar"

EQUITY_SLEEVE = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

CONTROL_TARGET = {"sharpe": 0.9955, "n_trades": 1546}

BOOKS = {
    "book_h_gold_equity": {"params": GOLD_PARAMS, "frac": 0.0},
    "book_h_gold_equity_derisk_flat": {"params": GOLD_PARAMS, "frac": 1.0},
    "book_h_gold_equity_derisk_half": {"params": GOLD_PARAMS, "frac": 0.5},
}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _load_events(instrument: str) -> list[str]:
    p = EARNINGS_DIR / f"{instrument}.json"
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh).get("events", [])


def _derisk_sets(panel: dict, holdout_start: str) -> dict[str, set[int]]:
    """Per single-name stock: the set of bar indices that are the LAST BAR BEFORE an
    earnings event bar (instrument's own calendar). Only stocks with EDGAR coverage."""
    sets: dict[str, set[int]] = {}
    for inst in panel:
        if inst not in STOCKS_12:
            continue
        events = [e for e in _load_events(inst) if "1900" < e < holdout_start]
        if not events:
            continue
        idx = panel[inst].index
        bars: set[int] = set()
        for e in events:
            loc = idx.searchsorted(pd.Timestamp(e, tz="UTC"))
            if 1 <= loc < len(idx):
                bars.add(int(loc) - 1)
        if bars:
            sets[inst] = bars
    return sets


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def _event_adjacent_stops(res, panel, holdout_start) -> dict:
    """Diagnostic: stop exits within ±1 bar of a covered event — count + total £."""
    n = 0
    total = 0.0
    for tr in res.trades:
        if getattr(tr, "exit_reason", "") != "stop":
            continue
        inst = getattr(tr, "instrument", "")
        if inst not in STOCKS_12:
            continue
        events = [e for e in _load_events(inst) if "1900" < e < holdout_start]
        if not events or inst not in panel:
            continue
        idx = panel[inst].index
        exit_t = pd.Timestamp(getattr(tr, "exit_time")).tz_convert("UTC") if pd.Timestamp(
            getattr(tr, "exit_time")).tzinfo else pd.Timestamp(getattr(tr, "exit_time"), tz="UTC")
        loc = idx.searchsorted(exit_t)
        for e in events:
            eloc = idx.searchsorted(pd.Timestamp(e, tz="UTC"))
            if abs(loc - eloc) <= 1:
                n += 1
                total += float(getattr(tr, "pnl", 0.0))
                break
    return {"n": n, "total_pnl": round(total, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: exit-side earnings de-risk, "
                                             "Book H gold equity sleeve (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)

    master: dict[str, pd.DataFrame] = {}
    for inst in sorted(EQUITY_SLEEVE):
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            continue
        master[inst] = df
    panel = {inst: master[inst] for inst in EQUITY_SLEEVE if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    derisk_sets = _derisk_sets(panel, args.holdout_start)
    print(f"de-risk coverage: {len(derisk_sets)}/{len(STOCKS_12)} stocks, "
          f"{sum(len(s) for s in derisk_sets.values())} de-risk bars", flush=True)

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_equity_21", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "earnings_derisk_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"], "derisk_frac": spec["frac"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"EARNINGS DE-RISK GATE (EXIT-SIDE, BOOK H GOLD EQUITY SLEEVE) 2026-08-08 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **spec["params"])
        frac = spec["frac"]
        res = PortfolioBacktester(cfg, exit_mode="managed",
                                  earnings_derisk=derisk_sets if frac > 0 else None,
                                  earnings_derisk_frac=frac if frac > 0 else 1.0).run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"], "derisk_frac": frac},
                         "metrics": m,
                         "tail": _tail_stats(res),
                         "derisk_fires": res.constraint_log.get("earnings_derisk", 0),
                         "event_adjacent_stops": _event_adjacent_stops(res, panel, args.holdout_start),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()} | derisk fires: "
              f"{results[name]['derisk_fires']}", flush=True)

    cm = results["book_h_gold_equity"]["metrics"]
    if abs(cm["sharpe"] - CONTROL_TARGET["sharpe"]) > 0.01 or \
            abs(cm["n_trades"] - CONTROL_TARGET["n_trades"]) > 5:
        print(f"CONTROL REPRODUCTION FAILED: {cm['sharpe']} vs {CONTROL_TARGET}", flush=True)
        return 1
    print(f"control reproduction: OK (sharpe {cm['sharpe']:.4f}, trades {cm['n_trades']})",
          flush=True)

    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        frac = spec["frac"]
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, **kw: TrendBook(p, **kw),
            spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
            earnings_derisk=derisk_sets if frac > 0 else None,
            earnings_derisk_frac=frac if frac > 0 else 1.0,
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    a = results["book_h_gold_equity"]
    # Best challenger = higher Sharpe of the two.
    chal_names = ["book_h_gold_equity_derisk_flat", "book_h_gold_equity_derisk_half"]
    best = max(chal_names, key=lambda n: results[n]["metrics"]["sharpe"])
    b = results[best]
    d_sharpe = b["metrics"]["sharpe"] - a["metrics"]["sharpe"]
    l1 = bool(d_sharpe >= -0.02)
    l2 = bool(verdicts[best]["dsr"].get("dsr", 0.0) >= 0.95)
    l3 = bool(pbo.get("pbo") is not None and pbo["pbo"] < 0.5)
    l4 = bool(abs(b["tail"]["worst_daily_return"]) <= 0.9 * abs(a["tail"]["worst_daily_return"])
              or b["tail"]["max_drawdown"] <= a["tail"]["max_drawdown"] - 0.01)
    confirmed = bool(l1 and l2 and l3 and l4)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  best challenger: {best}")
    print(f"  LEGS: L1 dSharpe {d_sharpe:+.4f} >= -0.02? {l1} | "
          f"L2 DSR {verdicts[best]['dsr'].get('dsr', 0):.4f} >= 0.95? {l2} | "
          f"L3 PBO {pbo.get('pbo')} < 0.5? {l3} | "
          f"L4 tail better? {l4} (worst day {a['tail']['worst_daily_return']:.4f} -> "
          f"{b['tail']['worst_daily_return']:.4f}, maxDD {a['tail']['max_drawdown']:.4f} -> "
          f"{b['tail']['max_drawdown']:.4f})")
    print(f"  event-adjacent stop losses: control {a['event_adjacent_stops']} | "
          f"challenger {b['event_adjacent_stops']}")
    print(f"  PRE-REGISTERED RULE => {'CONFIRMED (adoptable)' if confirmed else 'REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/earnings_derisk_gate_prereg.md",
        "universe": list(panel.keys()),
        "derisk_coverage": {k: len(v) for k, v in derisk_sets.items()},
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "best_challenger": best,
        "legs": {"l1_sharpe": l1, "l2_dsr": l2, "l3_pbo": l3, "l4_tail": l4},
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
