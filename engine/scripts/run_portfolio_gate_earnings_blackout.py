"""Pre-registered portfolio-level gate (W4): the ±1-trading-day earnings blackout.

Pre-registration: engine/data_store/earnings_blackout_prereg.md (2026-07-24, written BEFORE
any blackout run; the 2 trials are recorded before execution, dedup-safe). Hypothesis:
blocking NEW entries within ±1 trading day of a stock's earnings date reduces idiosyncratic
gap-through-stop losses without losing trend entries. Equity sleeve of Book H gold (21
instruments), certified params, certified risk anchor (max_risk_per_trade 0.01), certified
panel insertion order. Earnings dates: engine/data_store/earnings_calendar/*.json (SEC EDGAR
8-K Item 2.02 filing dates — point-in-time valid for an entry block).

Exactly 2 pre-registered configs (the full selection set):
  book_h_gold_equity              control (no blackout)
  book_h_gold_equity_blackout1d   challenger (new entries suppressed on the trading day
                                  before/of/after each covered stock's earnings date;
                                  open positions never touched)

Implementation: EarningsBlackout strategy wrapper (below) around each per-instrument
MultiTimeframeMomentum — no engine change, flows through CPCV's model factory identically.
Same three gates, same thresholds, same machinery as every prior gate. Iteration window only:
strictly < 2025-01-01. Seed 42. Determinism: run twice, byte-identical (verified in the
gate report).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_earnings_blackout.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_earnings_blackout.py --instruments AAPL,MSFT,NVDA --no-ledger

Exit code 0 if the challenger passes the gates AND the pre-registered H1/H2 rule confirms.
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
from apex_quant.risk.types import Direction, Signal  # noqa: E402
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
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "earnings_blackout_gate_2026-07-24.json"
EARNINGS_DIR = ENGINE_DIR / "data_store" / "earnings_calendar"

EQUITY_SLEEVE = EQUITY_CORE + [GOLD_ETC]   # 21 instruments (12 stocks, 8 ETFs, SGLD.L)
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)
WINDOW_DAYS = 1        # pre-registered ±1 trading day

# The full pre-registered selection set (2 trials): ONLY the blackout differs.
BOOKS = {
    "book_h_gold_equity": {"params": GOLD_PARAMS, "earnings_blackout_days": 0},
    "book_h_gold_equity_blackout1d": {"params": GOLD_PARAMS, "earnings_blackout_days": WINDOW_DAYS},
}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _load_earnings_events(instrument: str) -> list[str]:
    p = EARNINGS_DIR / f"{instrument}.json"
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh).get("events", [])


def _blocked_set(df: pd.DataFrame, events: list[str], window: int) -> set:
    """Bar timestamps to suppress: [loc-window, ..., loc, ..., loc+window] around each
    event date on the instrument's OWN bar calendar (loc = first bar >= the date)."""
    idx = df.index
    blocked: set = set()
    for e in events:
        loc = idx.searchsorted(pd.Timestamp(e, tz="UTC"))
        for j in range(loc - window, loc + window + 1):
            if 0 <= j < len(idx):
                blocked.add(idx[j])
    return blocked


class EarningsBlackout:
    """Strategy wrapper: FLAT inside the blocked set, base signal otherwise.

    Suppresses NEW entries only — the backtester manages open positions through the
    base strategy's exits, which are unaffected. Every attribute the engine reads
    (holding_horizon, name, ...) proxies to the wrapped strategy.
    """

    def __init__(self, base, blocked: set, instrument: str) -> None:
        self._base = base
        self._blocked = blocked
        self._instrument = instrument

    def __getattr__(self, name):
        if name == "_base":
            raise AttributeError(name)
        return getattr(self._base, name)

    def generate(self, pit, t, instrument: str = "") -> Signal:
        if pd.Timestamp(t) in self._blocked:
            return Signal(instrument=instrument or self._instrument, direction=Direction.FLAT,
                          probability=0.50, reward_risk=1.5, timeframe="1d",
                          rationale="earnings blackout ±1 trading day")
        return self._base.generate(pit, t, instrument)


class _Model:
    """TrendBook + optional earnings-blackout wrapping, with the EnsembleVote-style
    interface CPCV expects (``.strategies()`` only)."""

    def __init__(self, panel: dict, *, blackout_days: int = 0, blocked: dict | None = None,
                 **params) -> None:
        self._tb = TrendBook(panel, **params)
        self._blackout_days = blackout_days
        self._blocked = blocked or {}

    def strategies(self) -> dict:
        strats = self._tb.strategies()
        if self._blackout_days > 0:
            for inst in list(strats):
                if inst in self._blocked:
                    strats[inst] = EarningsBlackout(strats[inst], self._blocked[inst], inst)
        return strats


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: ±1d earnings blackout on the "
                                             "Book H gold equity sleeve (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the equity sleeve (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}

    wanted = sorted(EQUITY_SLEEVE)
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        if subset and inst not in subset:
            continue
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
    # Certified panel insertion order (equity sleeve order as in the certified book).
    panel = {inst: master[inst] for inst in EQUITY_SLEEVE if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Blocked sets per covered instrument (TSM has no cache and trades unblocked — prereg §7).
    blocked: dict[str, set] = {}
    coverage = {}
    for inst in panel:
        events = _load_earnings_events(inst)
        events = [e for e in events if "1900" < e < args.holdout_start]
        if events:
            blocked[inst] = _blocked_set(panel[inst], events, WINDOW_DAYS)
            coverage[inst] = {"n_events": len(events), "n_blocked_bars": len(blocked[inst])}
    print(f"earnings coverage: {len(blocked)}/{len(panel)} instruments "
          f"({sum(v['n_events'] for v in coverage.values())} events, "
          f"{sum(v['n_blocked_bars'] for v in coverage.values())} blocked bars)", flush=True)

    # Record the 2 pre-registered trials BEFORE running (dedup-safe on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_equity_21", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "earnings_blackout_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"],
                                      "earnings_blackout_days": spec["earnings_blackout_days"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"EARNINGS-BLACKOUT GATE (BOOK H GOLD EQUITY SLEEVE, mrpt={CERTIFIED_MRPT}) 2026-07-24 | "
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
        model = _Model(panel, blackout_days=spec["earnings_blackout_days"],
                       blocked=blocked, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"],
                                    "earnings_blackout_days": spec["earnings_blackout_days"]},
                         "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "tail": _tail_stats(res),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
              f"| tail: {results[name]['tail']}", flush=True)

    # Pre-registered H1/H2 evaluation (prereg §5).
    a = results["book_h_gold_equity"]
    b = results["book_h_gold_equity_blackout1d"]
    h1_worst_day = abs(b["tail"]["worst_daily_return"]) <= 0.9 * abs(a["tail"]["worst_daily_return"])
    h1_maxdd = b["tail"]["max_drawdown"] <= a["tail"]["max_drawdown"] - 0.01
    h1 = bool(h1_worst_day or h1_maxdd)
    h2 = bool(b["metrics"]["sharpe"] >= a["metrics"]["sharpe"] - 0.10
              and b["metrics"]["profit_factor"] >= a["metrics"]["profit_factor"] - 0.10)
    deltas = {k: round(b["metrics"][k] - a["metrics"][k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "expectancy_pct", "win_rate",
               "max_drawdown", "total_return", "ann_return", "n_trades")}
    deltas["worst_daily_return"] = round(b["tail"]["worst_daily_return"]
                                         - a["tail"]["worst_daily_return"], 6)
    print(f"H1 (tail): worst-day {a['tail']['worst_daily_return']:.4f} -> "
          f"{b['tail']['worst_daily_return']:.4f} (10% smaller? {h1_worst_day}) | "
          f"maxDD {a['tail']['max_drawdown']:.4f} -> {b['tail']['max_drawdown']:.4f} "
          f"(>=1pt smaller? {h1_maxdd}) => H1 {'HOLDS' if h1 else 'FAILS'}", flush=True)
    print(f"H2 (no lost trend entries): dSharpe {deltas['sharpe']:+.4f} (>= -0.10?), "
          f"dPF {deltas['profit_factor']:+.4f} (>= -0.10?) => H2 {'HOLDS' if h2 else 'FAILS'}",
          flush=True)

    # PBO across the 2-config selection set (reported as computed; prereg §4 caveat).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # CPCV per config (the same 15 paths as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, _bd=spec["earnings_blackout_days"], **kw: _Model(
                p, blackout_days=_bd, blocked=blocked, **kw),
            spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    challenger_pass = verdicts["book_h_gold_equity_blackout1d"]["passed"]
    confirmed = bool(challenger_pass and h1 and h2)
    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  PRE-REGISTERED RULE: H1 {'HOLDS' if h1 else 'FAILS'}, H2 {'HOLDS' if h2 else 'FAILS'}, "
          f"challenger gates {'PASS' if challenger_pass else 'FAIL'} => "
          f"{'CONFIRMED' if confirmed else 'REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/earnings_blackout_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "earnings_coverage": coverage,
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "h1_tail_improvement": h1,
        "h2_no_degradation": h2,
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
        "deltas_challenger_minus_control": deltas,
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
