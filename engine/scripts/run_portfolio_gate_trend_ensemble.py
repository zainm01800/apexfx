"""Pre-registered portfolio-level gate: MULTI-HORIZON TREND ENSEMBLE on Book H gold.

Pre-registration: engine/data_store/trend_ensemble_prereg.md (2026-07-27, written BEFORE
any challenger run; the 3 trials are recorded before execution, dedup-safe). Hypothesis:
blending the vol-scaled momentum score across lookback horizons beats the certified
single-252 score on net OOS Sharpe via stability (Moskowitz 2012 JFE; Hurst 2017 JPM;
Benhamou 2025 63/252 barbell). Book H gold universe, certified params, certified risk
anchor (max_risk_per_trade 0.01 — the 2026-07-22 gap-aware certified state; passed
explicitly as in every post-2026-07-23 gate).

Exactly 3 pre-registered configs (the full selection set; ONLY the lookback set differs):
  trend_ens_control_252       control ([252] — certified behaviour; anchor hard-check)
  trend_ens_blend_63_126_252  challenger B (equal-weight 3-horizon ensemble)
  trend_ens_barbell_63_252    challenger C (63/252 barbell)

Same three gates, same thresholds, same machinery as every prior gate. Iteration window
only: strictly < 2025-01-01. Seed 42. Determinism: run twice, byte-identical except
timestamps (verified in the gate report via a twin --out run). Adoption rule (prereg §5):
CPCV head-to-head > 7/15 paths AND DSR > 0.95 at the full ledger count AND zero-cost-twin
cost drag < 1%/yr vs control. Any leg fails => that challenger is REJECTED.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_trend_ensemble.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_trend_ensemble.py --instruments AAPL,MSFT,NVDA --no-ledger
    .venv-mac/bin/python scripts/run_portfolio_gate_trend_ensemble.py --out <twin>    # determinism rerun

Exit code 0 iff at least one challenger is ADOPTED under the pre-registered rule.
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
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "trend_ensemble_gate_2026-07-27.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control MUST
# reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

# The full pre-registered selection set (3 trials): ONLY the lookback set differs.
BOOKS = {
    "trend_ens_control_252": [252],
    "trend_ens_blend_63_126_252": [63, 126, 252],
    "trend_ens_barbell_63_252": [63, 252],
}
CONTROL = "trend_ens_control_252"
CHALLENGERS = ["trend_ens_blend_63_126_252", "trend_ens_barbell_63_252"]

# Pre-registered adoption thresholds (prereg section 5).
CPCV_PATHS_REQUIRED = 7        # challenger must beat control on > 7 of 15 paths (strictly)
DSR_REQUIRED = 0.95            # at the full updated ledger count
MAX_ADDED_DRAG_PER_YEAR = 0.01  # zero-cost-twin drag difference, absolute annualized return


def _params(lookbacks: list[int]) -> dict:
    return {**GOLD_PARAMS, "momentum_lookbacks": list(lookbacks)}


def _cfg(zero_cost: bool = False):
    """Certified-anchor cfg; optionally with every transaction cost zeroed (prereg §5
    cost-drag twin: same trades, the whole-run return difference IS the cost drag)."""
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    if zero_cost:
        for cls in vars(cfg.asset_classes):
            m = getattr(cfg.asset_classes, cls)
            if not hasattr(m, "spread_bps"):
                continue
            m.spread_pips = 0.0
            m.spread_bps = 0.0
            m.slippage_bps = 0.0
            m.commission_per_trade = 0.0
            if hasattr(m, "cross_rt_cost_pips"):
                m.cross_rt_cost_pips = None
            if hasattr(m, "pair_rt_cost_pips"):
                m.pair_rt_cost_pips = {}
            if hasattr(m, "pair_tf_rt_cost_pips"):
                m.pair_tf_rt_cost_pips = {}
            if hasattr(m, "short_borrow_bps_annual"):
                m.short_borrow_bps_annual = 0.0
    return cfg


def _run(panel, pits, timeframes, params, cfg):
    model = TrendBook(panel, **params)
    return PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes=timeframes,
        warmup=WARMUP, periods_per_year=252)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: multi-horizon trend "
                                             "ensemble on Book H gold (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 3)")
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

    # Record the 3 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, lbs in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "trend_ensemble_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": _params(lbs)})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"MULTI-HORIZON TREND ENSEMBLE GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-27 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config, NET and ZERO-COST twin (prereg §5 drag measure).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, lbs in BOOKS.items():
        params = _params(lbs)
        t_start = time.time()
        res = _run(panel, pits, timeframes, params, _cfg())
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": params,
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
              f"avg {mt['avg_monthly_pnl']:+,.0f}/mo", flush=True)

        t_start = time.time()
        res_zero = _run(panel, pits, timeframes, params, _cfg(zero_cost=True))
        results[name]["metrics_zero_cost"] = res_zero.metrics
        results[name]["cost_drag_per_year"] = round(
            res_zero.metrics["ann_return"] - m["ann_return"], 6)
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] zero-cost twin {name}: "
              f"{time.time() - t_start:.0f}s | ann_return net {m['ann_return']*100:.2f}% "
              f"-> zero-cost {res_zero.metrics['ann_return']*100:.2f}% "
              f"(drag {results[name]['cost_drag_per_year']*100:.2f}%/yr)", flush=True)

    # Certified-anchor reproduction: the control must reproduce
    # book_h_gapaware_2026-07-22.json (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[CONTROL]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",)
                                        else 1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # 2. PBO across the 3-config selection set (standing overlap caveat, prereg §4).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 paths as every prior gate; the lookback set flows
    #    through the model factory params into every fold).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, lbs in BOOKS.items():
        params = _params(lbs)
        t_start = time.time()
        if args.skip_cpcv:
            cpcv = {"n_paths": 0, "oos_sharpe_mean": 0.0, "oos_sharpe_std": 0.0,
                    "oos_sharpe_median": 0.0, "frac_positive": 0.0, "oos_sharpe_paths": []}
        else:
            cpcv = run_portfolio_cpcv(
                panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
                cfg=_cfg(), timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
                periods_per_year=252, exit_mode="managed",
            )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. Pre-registered adoption rule (prereg section 5): CPCV head-to-head > 7/15 AND
    #    DSR > 0.95 at the full ledger count AND added cost drag < 1%/yr. Kill: any leg.
    adoption: dict[str, dict] = {}
    ctrl_paths = results[CONTROL]["cpcv"]["oos_sharpe_paths"]
    for name in CHALLENGERS:
        chal_paths = results[name]["cpcv"]["oos_sharpe_paths"]
        paths_won = (sum(1 for b, a in zip(chal_paths, ctrl_paths) if b > a)
                     if len(chal_paths) == len(ctrl_paths) and chal_paths else None)
        cpcv_leg = bool(paths_won is not None and paths_won > CPCV_PATHS_REQUIRED)
        dsr_val = verdicts[name]["dsr"].get("dsr", 0.0)
        dsr_leg = bool(dsr_val > DSR_REQUIRED)
        added_drag = (results[name]["cost_drag_per_year"] - results[CONTROL]["cost_drag_per_year"])
        drag_leg = bool(added_drag < MAX_ADDED_DRAG_PER_YEAR)
        adopted = bool(cpcv_leg and dsr_leg and drag_leg)
        adoption[name] = {
            "adopted": adopted,
            "cpcv_paths_won_vs_control": paths_won,
            "cpcv_leg_gt_7_of_15": cpcv_leg,
            "dsr": dsr_val,
            "dsr_leg_gt_0.95": dsr_leg,
            "added_cost_drag_per_year": round(added_drag, 6),
            "drag_leg_lt_1pct": drag_leg,
        }
        print(f"ADOPTION {name}: CPCV paths won {paths_won}/15 (>7? {cpcv_leg}) | "
              f"DSR {dsr_val:.3f} (>0.95? {dsr_leg}) | added drag {added_drag*100:+.2f}%/yr "
              f"(<1%? {drag_leg}) => {'ADOPTED' if adopted else 'REJECTED'}", flush=True)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    any_adopted = any(a["adopted"] for a in adoption.values())
    print(f"  PRE-REGISTERED RULE => {'AT LEAST ONE CHALLENGER ADOPTED' if any_adopted else 'ALL CHALLENGERS REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/trend_ensemble_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "adoption": adoption,
        "verdict_rule": "ADOPTED" if any_adopted else "REJECTED",
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if any_adopted else 1


if __name__ == "__main__":
    sys.exit(main())
