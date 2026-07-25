"""Pre-registered portfolio-level gate (W1): order-invariant risk allocation.

Pre-registration: engine/data_store/order_invariant_prereg.md (2026-07-25, written BEFORE
any challenger run; the 2 trials are recorded before execution, dedup-safe). The defect
being fixed is measured in engine/data_store/ordering_sensitivity_audit.md (2026-07-22):
same-bar candidates are evaluated in panel dict-insertion order and the scarce resources
(10-slot swing bucket, 12-position global cap, 6.5% portfolio-risk budget) are consumed
first-come-first-served — shuffling the panel alone moves Sharpe 0.217 <-> 0.863.

Exactly 2 pre-registered configs (the full selection set), Book H gold universe,
certified risk anchor (max_risk_per_trade 0.01 — the 2026-07-22 gap-aware state):
  book_h_gold_252_seq     control: certified sequential allocation
  book_h_gold_252_simul   challenger: EV-ranked selection + simultaneous-gamma risk cap

Shuffle protocol (prereg section 4): 3 seeded permutations of the certified panel
(RandomState 101/202/303) plus the certified order, run through BOTH configs. Challenger
must be order-INVARIANT (C1); control must DIFFER (C2, re-demonstrates the artifact);
challenger Sharpe/PF must hold the control shuffle-median within noise and pass the
three gates (C3). Shuffle runs are the SAME configs on permuted inputs — no ledger charge.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_order_invariant.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_order_invariant.py --no-ledger     # smoke
    .venv-mac/bin/python scripts/run_portfolio_gate_order_invariant.py --out <twin>    # determinism rerun

Exit code 0 iff the pre-registered rule returns ADOPT.
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
    _cap_families,
    _gate,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "order_invariant_gate_2026-07-25.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control at the
# certified panel order MUST reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

# The full pre-registered selection set (2 trials): ONLY the allocation rule differs.
BOOKS = {
    "book_h_gold_252_seq": {"slot_allocation": "order", "portfolio_risk_cap_mode": "sequential"},
    "book_h_gold_252_simul": {"slot_allocation": "expected_value",
                              "portfolio_risk_cap_mode": "simultaneous"},
}
CONTROL, CHALLENGER = "book_h_gold_252_seq", "book_h_gold_252_simul"

# Pre-registered shuffle seeds (prereg section 4) — fixed so the test is reproducible.
SHUFFLE_SEEDS = [101, 202, 303]

# Pre-registered tolerances / decision thresholds (prereg section 4).
TOL_SHARPE = 1e-9
TOL_EQUITY_REL = 1e-9
C3_SHARPE_SLACK = 0.05
C3_PF_SLACK = 0.10


def _cfg(flags: dict):
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    cfg.risk.slot_allocation = flags["slot_allocation"]
    cfg.risk.portfolio_risk_cap_mode = flags["portfolio_risk_cap_mode"]
    return cfg


def _monthly_tail_stats(res, initial_equity: float = 100000.0) -> dict:
    """In-window monthly-profit and tail figures on the 100k book (prereg section 4)."""
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


def _full_run(panel: dict, flags: dict):
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    cfg = _cfg(flags)
    model = TrendBook(panel, **GOLD_PARAMS)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252,
    )
    return res


def _metric_row(res) -> dict:
    m = res.metrics
    return {"sharpe": m["sharpe"], "profit_factor": m["profit_factor"],
            "n_trades": m["n_trades"], "final_equity": m["final_equity"],
            "max_drawdown": m["max_drawdown"], "win_rate": m["win_rate"],
            "expectancy_pnl": m["expectancy_pnl"], "total_return": m["total_return"]}


def _invariance_check(rows: list[dict]) -> dict:
    """C1 test: max spread of Sharpe / n_trades / final_equity across orderings."""
    sharpes = [r["sharpe"] for r in rows]
    equities = [r["final_equity"] for r in rows]
    trades = [r["n_trades"] for r in rows]
    eq_ref = abs(equities[0]) if equities[0] else 1.0
    return {
        "sharpe_spread": float(max(sharpes) - min(sharpes)),
        "equity_rel_spread": float((max(equities) - min(equities)) / eq_ref),
        "n_trades_distinct": len(set(trades)),
        "invariant": bool(
            (max(sharpes) - min(sharpes)) <= TOL_SHARPE
            and len(set(trades)) == 1
            and (max(equities) - min(equities)) / eq_ref <= TOL_EQUITY_REL
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: order-invariant risk "
                                             "allocation on Book H gold (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
    ap.add_argument("--skip-shuffles", action="store_true",
                    help="smoke-test mode: skip the 6 shuffle runs (C1/C2 unevaluated)")
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
    certified_order = list(panel.keys())
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 2 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, flags in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "order_invariant_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**GOLD_PARAMS, **flags}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"ORDER-INVARIANT ALLOCATION GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-25 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config at the CERTIFIED panel order.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, flags in BOOKS.items():
        t_start = time.time()
        res = _full_run(panel, flags)
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**GOLD_PARAMS, **flags},
                         "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "monthly_tail": _monthly_tail_stats(res, base_cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        mt = results[name]["monthly_tail"]
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
              f"worst_trade={mt['worst_trade_pnl']:+,.0f} avg {mt['avg_monthly_pnl']:+,.0f}/mo "
              f"| gamma binds: {sum(v for k, v in res.constraint_log.items() if k.startswith('portfolio_risk_gamma='))} "
              f"trims: {res.constraint_log.get('portfolio_risk_gamma_trim', 0)}", flush=True)

    # Certified-anchor reproduction: the control at the certified order must reproduce
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

    # 2. Shuffle protocol (prereg section 4): 3 seeded permutations through BOTH configs.
    shuffle_rows: dict[str, list[dict]] = {
        name: [dict(order="certified", **_metric_row_from_metrics(results[name]["metrics"]))]
        for name in BOOKS
    }
    if not args.skip_shuffles:
        for seed in SHUFFLE_SEEDS:
            perm = list(np.random.RandomState(seed).permutation(certified_order))
            panel_p = {inst: panel[inst] for inst in perm}
            for name, flags in BOOKS.items():
                t_start = time.time()
                res = _full_run(panel_p, flags)
                row = dict(order=f"shuffle_{seed}", **_metric_row(res))
                shuffle_rows[name].append(row)
                print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                      f"shuffle {seed} {name}: {time.time() - t_start:.0f}s | "
                      f"sharpe={row['sharpe']:.5f} trades={row['n_trades']} "
                      f"equity={row['final_equity']:.2f}", flush=True)

    inv = {name: _invariance_check(rows) for name, rows in shuffle_rows.items()}
    c1 = inv[CHALLENGER]["invariant"]
    c2 = not inv[CONTROL]["invariant"]
    print(f"C1 (challenger order-invariant): {c1} {inv[CHALLENGER]}", flush=True)
    print(f"C2 (control order-dependent):    {c2} {inv[CONTROL]}", flush=True)

    # 3. PBO across the 2-config selection set (standing overlap caveat, prereg section 3).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 4. CPCV per config (the same 15 paths as every prior gate; challenger flags flow
    #    through cfg.risk into every fold).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, flags in BOOKS.items():
        cfg = _cfg(flags)
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

    # 5. Pre-registered decision rule (prereg section 4).
    ctrl_shuffles = [r for r in shuffle_rows[CONTROL] if r["order"] != "certified"]
    ctrl_med_sharpe = float(np.median([r["sharpe"] for r in ctrl_shuffles])) if ctrl_shuffles else float("nan")
    ctrl_med_pf = float(np.median([r["profit_factor"] for r in ctrl_shuffles])) if ctrl_shuffles else float("nan")
    chal = results[CHALLENGER]["metrics"]
    c3 = bool(
        ctrl_shuffles
        and chal["sharpe"] >= ctrl_med_sharpe - C3_SHARPE_SLACK
        and chal["profit_factor"] >= ctrl_med_pf - C3_PF_SLACK
        and verdicts[CHALLENGER]["passed"]
    )
    adopt = bool(c1 and c2 and c3)
    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  control shuffle-median sharpe {ctrl_med_sharpe:.4f} PF {ctrl_med_pf:.3f} | "
          f"challenger sharpe {chal['sharpe']:.4f} PF {chal['profit_factor']:.3f}")
    print(f"  PRE-REGISTERED RULE: C1 {'HOLDS' if c1 else 'FAILS'}, C2 {'HOLDS' if c2 else 'FAILS'}, "
          f"C3 {'HOLDS' if c3 else 'FAILS'} => {'ADOPT' if adopt else 'REJECT'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/order_invariant_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": certified_order,
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "shuffle_protocol": {"seeds": SHUFFLE_SEEDS, "tolerances": {
            "sharpe": TOL_SHARPE, "equity_rel": TOL_EQUITY_REL}},
        "shuffle_rows": shuffle_rows,
        "invariance": inv,
        "c1_challenger_order_invariant": c1,
        "c2_control_order_dependent": c2,
        "c3_performance_parity": c3,
        "control_shuffle_median": {"sharpe": ctrl_med_sharpe, "profit_factor": ctrl_med_pf},
        "verdict_rule": "ADOPT" if adopt else "REJECT",
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if adopt else 1


def _metric_row_from_metrics(m: dict) -> dict:
    return {"sharpe": m["sharpe"], "profit_factor": m["profit_factor"],
            "n_trades": m["n_trades"], "final_equity": m["final_equity"],
            "max_drawdown": m["max_drawdown"], "win_rate": m["win_rate"],
            "expectancy_pnl": m["expectancy_pnl"], "total_return": m["total_return"]}


if __name__ == "__main__":
    sys.exit(main())
