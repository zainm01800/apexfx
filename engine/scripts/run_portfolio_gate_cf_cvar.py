"""Pre-registered portfolio-level gate (W2): Cornish-Fisher CVaR tail sizing.

Pre-registration: engine/data_store/cf_cvar_prereg.md (2026-07-25, written BEFORE any
challenger run; the 2 trials are recorded before execution, dedup-safe). Hypothesis:
sizing positions by tail-adjusted volatility (direction-aware Cornish-Fisher multiplier
on the per-unit risk measure, rolling 60-day skew/excess-kurtosis, one-sided 99%,
tau in [1, 2]) contracts allocation on heavy-tailed names before shocks — improving
worst-day/worst-trade tail losses — for <= 5% of monthly profit. Book H gold universe,
certified params, certified risk anchor (max_risk_per_trade 0.01 — the 2026-07-22
gap-aware certified state; passed explicitly as in every post-2026-07-23 gate).

Exactly 2 pre-registered configs (the full selection set):
  book_h_gold_252          control (cf_cvar_enabled=false, certified behaviour)
  book_h_gold_252_cf_cvar  challenger (RiskManager step 6a, tau from rolling moments)

Same three gates, same thresholds, same machinery as every prior gate. Iteration window
only: strictly < 2025-01-01. Seed 42. Determinism: run twice, metrics byte-identical
(verified in the gate report via a twin --out run).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_cf_cvar.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_cf_cvar.py --instruments AAPL,MSFT,NVDA --no-ledger
    .venv-mac/bin/python scripts/run_portfolio_gate_cf_cvar.py --out <twin>    # determinism rerun

Exit code 0 iff the pre-registered rule returns CONFIRMED.
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
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "cf_cvar_gate_2026-07-25.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control MUST
# reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

# The full pre-registered selection set (2 trials): ONLY the sizing vol differs.
BOOKS = {
    "book_h_gold_252": False,
    "book_h_gold_252_cf_cvar": True,
}
CONTROL, CHALLENGER = "book_h_gold_252", "book_h_gold_252_cf_cvar"

# Pre-registered decision thresholds (prereg section 5).
TAIL_IMPROVE_FRAC = 0.05      # a tail metric must be >= 5% smaller in magnitude
MAXDD_IMPROVE_PTS = 0.01      # or maxDD >= 1.0 percentage point smaller
MAX_MONTHLY_COST_FRAC = 0.05  # avg monthly-profit cost vs control must be <= 5%


def _cfg(cf_enabled: bool):
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    cfg.risk.cf_cvar_enabled = cf_enabled
    return cfg


def _monthly_tail_stats(res, initial_equity: float = 100000.0) -> dict:
    """In-window monthly-profit and tail figures on the 100k book (prereg section 5)."""
    eq = res.equity
    if eq.empty:
        return {}
    month_last = eq.groupby(eq.index.to_period("M")).last()
    prev = pd.Series([initial_equity, *month_last.iloc[:-1]], index=month_last.index)
    monthly_pnl = month_last - prev
    rets = res.returns
    worst_trade = min((t.pnl for t in res.trades), default=0.0)
    return {
        "n_months": int(len(monthly_pnl)),
        "avg_monthly_pnl": round(float(monthly_pnl.mean()), 2),
        "median_monthly_pnl": round(float(monthly_pnl.median()), 2),
        "worst_month_pnl": round(float(monthly_pnl.min()), 2),
        "worst_daily_return": round(float(rets.min()), 6),
        "worst_daily_pnl": round(float(eq.diff().dropna().min()), 2),
        "worst_trade_pnl": round(float(worst_trade), 2),
    }


def _tau_stats(res) -> dict:
    binds = {k: v for k, v in res.constraint_log.items() if k.startswith("cf_cvar_tau=")}
    taus = [float(k.split("=", 1)[1]) for k in binds]
    return {"n_positions_scaled": int(sum(binds.values())),
            "n_distinct_tau": len(binds),
            "tau_min": min(taus) if taus else None,
            "tau_max": max(taus) if taus else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: Cornish-Fisher CVaR tail "
                                             "sizing on Book H gold (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
    ap.add_argument("--skip-cpcv", action="store_true",
                    help="smoke-test mode: skip CPCV (gate verdicts unevaluated)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first), NOT load
    # order — the certified numbers are ordering-sensitive (ordering_sensitivity_audit.md).
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 2 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, cf_on in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "cf_cvar_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**GOLD_PARAMS, "cf_cvar_enabled": cf_on}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"CF-CVaR TAIL-SIZING GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-25 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config (cf_cvar_enabled is the ONLY difference).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, cf_on in BOOKS.items():
        cfg = _cfg(cf_on)
        t_start = time.time()
        model = TrendBook(panel, **GOLD_PARAMS)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**GOLD_PARAMS, "cf_cvar_enabled": cf_on},
                         "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
                         "tau": _tau_stats(res),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        mt = results[name]["monthly_tail"]
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
              f"worst_trade={mt['worst_trade_pnl']:+,.0f} avg {mt['avg_monthly_pnl']:+,.0f}/mo "
              f"| tau: {results[name]['tau']}", flush=True)

    # Certified-anchor reproduction: the control must reproduce
    # book_h_gapaware_2026-07-22.json (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[CONTROL]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",) else
                                          1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # 2. Pre-registered H1/H2 evaluation (prereg section 5).
    a = results[CONTROL]
    b = results[CHALLENGER]
    h1_day = abs(b["monthly_tail"]["worst_daily_return"]) <= (1 - TAIL_IMPROVE_FRAC) * abs(a["monthly_tail"]["worst_daily_return"])
    h1_trade = abs(b["monthly_tail"]["worst_trade_pnl"]) <= (1 - TAIL_IMPROVE_FRAC) * abs(a["monthly_tail"]["worst_trade_pnl"])
    h1_dd = b["metrics"]["max_drawdown"] <= a["metrics"]["max_drawdown"] - MAXDD_IMPROVE_PTS
    h1 = bool(h1_day or h1_trade or h1_dd)
    h2 = bool(b["monthly_tail"]["avg_monthly_pnl"] >= (1 - MAX_MONTHLY_COST_FRAC) * a["monthly_tail"]["avg_monthly_pnl"])
    deltas = {k: round(b["metrics"][k] - a["metrics"][k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "expectancy_pct", "win_rate",
               "max_drawdown", "total_return", "ann_return")}
    deltas["worst_daily_return"] = round(b["monthly_tail"]["worst_daily_return"]
                                         - a["monthly_tail"]["worst_daily_return"], 6)
    deltas["worst_trade_pnl"] = round(b["monthly_tail"]["worst_trade_pnl"]
                                      - a["monthly_tail"]["worst_trade_pnl"], 2)
    deltas["avg_monthly_pnl"] = round(b["monthly_tail"]["avg_monthly_pnl"]
                                      - a["monthly_tail"]["avg_monthly_pnl"], 2)
    print(f"H1 (tail): worst-day {a['monthly_tail']['worst_daily_return']:.4f} -> "
          f"{b['monthly_tail']['worst_daily_return']:.4f} (5% smaller? {h1_day}) | "
          f"worst-trade {a['monthly_tail']['worst_trade_pnl']:+,.0f} -> "
          f"{b['monthly_tail']['worst_trade_pnl']:+,.0f} (5% smaller? {h1_trade}) | "
          f"maxDD {a['metrics']['max_drawdown']:.4f} -> {b['metrics']['max_drawdown']:.4f} "
          f"(>=1pt smaller? {h1_dd}) => H1 {'HOLDS' if h1 else 'FAILS'}", flush=True)
    print(f"H2 (cost <= 5%): avg monthly {a['monthly_tail']['avg_monthly_pnl']:+,.0f} -> "
          f"{b['monthly_tail']['avg_monthly_pnl']:+,.0f} "
          f"({deltas['avg_monthly_pnl']:+,.0f}) => H2 {'HOLDS' if h2 else 'FAILS'}", flush=True)

    # 3. PBO across the 2-config selection set (standing overlap caveat, prereg section 4).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 4. CPCV per config (the same 15 paths as every prior gate; the flag flows through
    #    cfg.risk into every fold).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, cf_on in BOOKS.items():
        cfg = _cfg(cf_on)
        t_start = time.time()
        if args.skip_cpcv:
            cpcv = {"n_paths": 0, "oos_sharpe_mean": 0.0, "oos_sharpe_std": 0.0,
                    "oos_sharpe_median": 0.0, "frac_positive": 0.0, "oos_sharpe_paths": []}
        else:
            cpcv = run_portfolio_cpcv(
                panel, pits, lambda p, **kw: TrendBook(p, **kw), GOLD_PARAMS,
                cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
                periods_per_year=252, exit_mode="managed",
            )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    challenger_pass = verdicts[CHALLENGER]["passed"]
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
        "prereg": "engine/data_store/cf_cvar_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "h1_tail_improvement": h1,
        "h1_components": {"worst_day_5pct": h1_day, "worst_trade_5pct": h1_trade,
                          "maxdd_1pt": h1_dd},
        "h2_monthly_cost_within_5pct": h2,
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
        "deltas_challenger_minus_control": deltas,
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
