"""Pre-registered portfolio-level gate (W3): the 15% per-position notional cap.

Pre-registration: engine/data_store/notional_cap_prereg.md (2026-07-24, written BEFORE
any capped run; the 2 trials are recorded before execution, dedup-safe). Hypothesis:
capping per-position notional at 15% of equity reduces gap-tail losses on low-vol
names (AAPL-type, which reach ~15%+ notional for the same 1% risk) without degrading
book performance. Book H gold universe, certified params, certified risk anchor
(max_risk_per_trade 0.01 — the 2026-07-22 gap-aware certified state; config.yaml has
since moved to 0.0075 by owner decision, so the anchor is passed explicitly).

Exactly 2 pre-registered configs (the full selection set — 2 to keep PBO meaningful):
  book_h_gold_252                 control (cap flag 0.0 = off, certified behaviour)
  book_h_gold_252_notional_cap15  challenger (RiskManager step 8.5, cap 0.15)

Same three gates, same thresholds, same machinery as every prior gate (thin
orchestration over run_portfolio_gate.py / run_portfolio_gate_book_h.py helpers).
Iteration window only: strictly < 2025-01-01. Seed 42. Determinism: run twice,
byte-identical (verified in the gate report).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_notional_cap.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_notional_cap.py --instruments AAPL,MSFT,NVDA --no-ledger

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

import numpy as np  # noqa: E402
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
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "notional_cap_gate_2026-07-24.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

# The full pre-registered selection set (2 trials): ONLY the cap differs.
BOOKS = {
    "book_h_gold_252": {"params": GOLD_PARAMS, "max_position_notional_pct": 0.0},
    "book_h_gold_252_notional_cap15": {"params": GOLD_PARAMS, "max_position_notional_pct": 0.15},
}


def _cfg_with_cap(pct: float):
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    cfg.risk.max_position_notional_pct = pct
    return cfg


def _notional_share_stats(res) -> dict:
    """Per-trade notional as a fraction of the decision-day equity (what the
    RiskManager actually saw). The tail the cap is aimed at."""
    eq = res.equity
    if eq.empty or not res.trades:
        return {}
    shares = []
    for tr in res.trades:
        t0 = pd.Timestamp(tr.entry_time, tz="UTC")
        e0 = eq.asof(t0)
        if e0 and np.isfinite(e0) and e0 > 0:
            shares.append(abs(tr.entry_price * tr.units) / e0)
    if not shares:
        return {}
    s = np.asarray(shares)
    return {"n": int(len(s)), "max": round(float(s.max()), 4),
            "p95": round(float(np.percentile(s, 95)), 4),
            "median": round(float(np.median(s)), 4),
            "frac_over_15pct": round(float((s > 0.15).mean()), 4)}


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: 15% per-position notional cap "
                                             "on Book H gold (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first),
    # NOT load order — see run_portfolio_gate_book_h.py and the 2026-07-22 ordering
    # audit. The certified numbers are ordering-sensitive; reproduce that exactly.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 2 pre-registered trials BEFORE running (dedup-safe on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "notional_cap_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"],
                                      "max_position_notional_pct": spec["max_position_notional_pct"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"NOTIONAL-CAP GATE (BOOK H GOLD, certified anchor mrpt={CERTIFIED_MRPT}) 2026-07-24 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg_with_cap(spec["max_position_notional_pct"])
        t_start = time.time()
        model = TrendBook(panel, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"],
                                    "max_position_notional_pct": spec["max_position_notional_pct"]},
                         "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "notional_share": _notional_share_stats(res),
                         "tail": _tail_stats(res),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
              f"| cap binds: {res.constraint_log.get('max_position_notional', 0)} "
              f"| notional share: {results[name]['notional_share']} | tail: {results[name]['tail']}",
              flush=True)

    # Pre-registered H1/H2 evaluation (prereg §4).
    a = results["book_h_gold_252"]
    b = results["book_h_gold_252_notional_cap15"]
    h1_worst_day = abs(b["tail"]["worst_daily_return"]) <= 0.9 * abs(a["tail"]["worst_daily_return"])
    h1_maxdd = b["tail"]["max_drawdown"] <= a["tail"]["max_drawdown"] - 0.01
    h1 = bool(h1_worst_day or h1_maxdd)
    h2 = bool(b["metrics"]["sharpe"] >= a["metrics"]["sharpe"] - 0.10
              and b["metrics"]["profit_factor"] >= a["metrics"]["profit_factor"] - 0.10)
    deltas = {k: round(b["metrics"][k] - a["metrics"][k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "expectancy_pct", "win_rate",
               "max_drawdown", "total_return", "ann_return")}
    deltas["worst_daily_return"] = round(b["tail"]["worst_daily_return"]
                                         - a["tail"]["worst_daily_return"], 6)
    print(f"H1 (tail): worst-day {a['tail']['worst_daily_return']:.4f} -> "
          f"{b['tail']['worst_daily_return']:.4f} (10% smaller? {h1_worst_day}) | "
          f"maxDD {a['tail']['max_drawdown']:.4f} -> {b['tail']['max_drawdown']:.4f} "
          f"(>=1pt smaller? {h1_maxdd}) => H1 {'HOLDS' if h1 else 'FAILS'}", flush=True)
    print(f"H2 (no degradation): dSharpe {deltas['sharpe']:+.4f} (>= -0.10?), "
          f"dPF {deltas['profit_factor']:+.4f} (>= -0.10?) => H2 {'HOLDS' if h2 else 'FAILS'}",
          flush=True)

    # PBO across the 2-config selection set (reported as computed; prereg §3 caveat).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # CPCV per config (the same 15 paths as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg_with_cap(spec["max_position_notional_pct"])
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    challenger_pass = verdicts["book_h_gold_252_notional_cap15"]["passed"]
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
        "prereg": "engine/data_store/notional_cap_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "h1_tail_improvement": h1,
        "h2_no_degradation": h2,
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
        "deltas_capped_minus_control": deltas,
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
