"""Pre-registered deep audit and improvement gate for the promoted Book C.

Pre-registration:
    data_store/book_c_deep_audit_prereg_2026-08-19.md

The script deliberately keeps the production paper state and config.yaml untouched.  It
reproduces the legacy 252-day headline, reports calendar-consistent metrics for the mixed
equity/FX/crypto union calendar, tests four isolated candidates, and performs paired/CPCV,
post-2025 verification, cost, concentration and prop-rule diagnostics.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_book_c_deep_audit.py
    .venv-mac/bin/python scripts/run_book_c_deep_audit.py --skip-cpcv --no-ledger
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.backtest.result import compute_metrics  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, PointInTimeAccessor, clean  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _max_gross_leverage,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC, STOCKS_12  # noqa: E402
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402


PREREG = "engine/data_store/book_c_deep_audit_prereg_2026-08-19.md"
DEFAULT_OUT = ENGINE_DIR / "data_store" / "validation" / "book_c_deep_audit_2026-08-19.json"
ITERATION_END = pd.Timestamp("2025-01-01", tz="UTC")
VERIFY_START = pd.Timestamp("2025-01-01", tz="UTC")
CALENDAR_PPY = 365
LEGACY_PPY = 252
BASELINE = "book_c_control"

PARAMS = {
    "carry_filter": False,
    **COMMON_PARAMS,
    "momentum_lookback": 252,
    "momentum_lookbacks": [63, 126, 252],
}

# Exactly the five Stage-1 cells in the pre-registration.  `changes` is also the trial key.
SPECS = {
    BASELINE: {"changes": {}},
    "book_c_runner": {"changes": {"runner_mode": True}},
    "book_c_notional15": {"changes": {"max_position_notional_pct": 0.15}},
    "book_c_portcap045": {"changes": {"max_portfolio_risk": 0.045}},
    "book_c_risk075": {"changes": {"max_risk_per_trade": 0.0075}},
}

LEGACY_ANCHOR = {
    "sharpe": 0.9237660179784476,
    "max_drawdown": 0.15921488068143141,
    "n_trades": 1654,
    "final_equity": 316181.0796516242,
}


def _cfg(changes: dict, cost_mult: float = 1.0):
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = 0.01
    for key, value in changes.items():
        if key != "runner_mode":
            setattr(cfg.risk, key, value)
    if cost_mult != 1.0:
        for cls_name in ("forex", "equity", "crypto"):
            m = getattr(cfg.asset_classes, cls_name)
            m.spread_pips *= cost_mult
            m.spread_bps *= cost_mult
            m.slippage_bps *= cost_mult
            m.commission_per_trade *= cost_mult
            if cls_name == "forex":
                if m.cross_rt_cost_pips is not None:
                    m.cross_rt_cost_pips *= cost_mult
                m.pair_rt_cost_pips = {
                    k: v * cost_mult for k, v in m.pair_rt_cost_pips.items()
                }
                m.pair_tf_rt_cost_pips = {
                    k: {tf: v * cost_mult for tf, v in by_tf.items()}
                    for k, by_tf in m.pair_tf_rt_cost_pips.items()
                }
    return cfg


def _tm(spec: dict):
    return TradeManager(runner_mode=bool(spec["changes"].get("runner_mode", False)))


def _load_panel(include_post_2025: bool = False) -> dict[str, pd.DataFrame]:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    wanted = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7
    panel: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data", flush=True)
            continue
        df = clean(df)
        if not include_post_2025:
            df = df[df.index < ITERATION_END]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} usable bars", flush=True)
            continue
        panel[inst] = df
    return panel


def _run(panel: dict[str, pd.DataFrame], name: str, *, cost_mult: float = 1.0):
    spec = SPECS[name]
    cfg = _cfg(spec["changes"], cost_mult=cost_mult)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    model = TrendBook(panel, **PARAMS)
    # 365 is correct for the union timeline because crypto creates weekend observations.
    return PortfolioBacktester(cfg, exit_mode="managed", trade_manager=_tm(spec)).run(
        pits,
        model.strategies(),
        timeframes={k: "1d" for k in panel},
        warmup=WARMUP,
        periods_per_year=CALENDAR_PPY,
    )


def _subset_metrics(res, start: pd.Timestamp) -> dict:
    eq = res.equity[res.equity.index >= start]
    trades = [t for t in res.trades if pd.Timestamp(t.exit_time, tz="UTC") >= start]
    return compute_metrics(eq, trades, periods_per_year=CALENDAR_PPY)


def _year_metrics(res) -> dict[str, dict]:
    out = {}
    for year, eq in res.equity.groupby(res.equity.index.year):
        if len(eq) < 2:
            continue
        trades = [t for t in res.trades if pd.Timestamp(t.exit_time).year == int(year)]
        out[str(year)] = compute_metrics(eq, trades, periods_per_year=CALENDAR_PPY)
    return out


def _attribution(res, cfg) -> dict:
    by_direction: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    by_class: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    by_instrument: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for tr in res.trades:
        for bucket, key in (
            (by_direction, tr.direction),
            (by_class, cfg.asset_class_of(tr.instrument)),
            (by_instrument, tr.instrument),
        ):
            bucket[key]["n"] += 1
            bucket[key]["pnl"] += float(tr.pnl)
    def finish(d):
        return {
            k: {"n": v["n"], "pnl": round(v["pnl"], 2),
                "expectancy": round(v["pnl"] / v["n"], 2) if v["n"] else 0.0}
            for k, v in d.items()
        }
    return {"direction": finish(by_direction), "asset_class": finish(by_class),
            "instrument": finish(by_instrument)}


def _prop_close_diagnostics(res, initial_equity: float = 100_000.0) -> dict:
    """Approximate current FTMO limits from daily closing equity.

    The production rules are monitored intraday and reset at 00:00 CE(S)T.  A daily
    backtest cannot reproduce either detail, so these are deliberately labelled
    lower-bound diagnostics rather than a claim that an account would have passed.
    """
    eq = res.equity.dropna()
    if eq.empty:
        return {}

    prior_balance = eq.shift(1)
    prior_balance.iloc[0] = initial_equity
    prior_peak = pd.concat([
        pd.Series(initial_equity, index=eq.index, dtype=float),
        eq.shift(1),
    ], axis=1).max(axis=1).cummax()
    daily_pnl_proxy = eq.diff()
    daily_pnl_proxy.iloc[0] = eq.iloc[0] - initial_equity

    one_step_daily = eq <= prior_balance - 0.03 * initial_equity
    two_step_daily = eq <= prior_balance - 0.05 * initial_equity
    one_step_trailing = eq <= prior_peak - 0.10 * initial_equity
    two_step_static = eq <= 0.90 * initial_equity
    positive_days = daily_pnl_proxy[daily_pnl_proxy > 0]
    best_day_share = (
        float(positive_days.max() / positive_days.sum()) if not positive_days.empty else None
    )

    def breach_summary(mask: pd.Series) -> dict:
        dates = eq.index[mask]
        return {
            "breach_days": int(mask.sum()),
            "first_breach": str(dates[0].date()) if len(dates) else None,
        }

    return {
        "initial_equity": float(initial_equity),
        "one_step_3pct_daily": breach_summary(one_step_daily),
        "one_step_10pct_eod_trailing": breach_summary(one_step_trailing),
        "two_step_5pct_daily": breach_summary(two_step_daily),
        "two_step_10pct_static": breach_summary(two_step_static),
        "best_day_share_of_positive_daily_equity_changes": best_day_share,
        "best_day_50pct_proxy_pass": bool(best_day_share is not None and best_day_share <= 0.50),
        "limitation": (
            "Lower bound/proxy only: intraday equity, CE(S)T reset timing, closed-profit "
            "Best Day accounting, swaps and commissions are not fully observable."
        ),
    }


def _result_payload(res) -> dict:
    cfg = get_config()
    legacy = compute_metrics(res.equity, res.trades, periods_per_year=LEGACY_PPY)
    return {
        "metrics_calendar_365": res.metrics,
        "metrics_legacy_252": legacy,
        "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
        "max_gross_leverage": _max_gross_leverage(res),
        "constraint_log": res.constraint_log,
        "attribution": _attribution(res, cfg),
        "year_metrics": _year_metrics(res),
        "prop_close_diagnostics": _prop_close_diagnostics(
            res, initial_equity=cfg.backtest.initial_equity
        ),
        "n_equity_points": int(len(res.equity)),
    }


def _anchor_check(payload: dict) -> dict:
    got = payload["metrics_legacy_252"]
    mismatch = {}
    for key, expected in LEGACY_ANCHOR.items():
        tol = 0.5 if key == "n_trades" else 1e-8 * max(1.0, abs(expected))
        if abs(float(got[key]) - float(expected)) > tol:
            mismatch[key] = {"got": got[key], "expected": expected}
    return {"passed": not mismatch, "mismatch": mismatch}


def _cpcv(panel, name):
    spec = SPECS[name]
    cfg = _cfg(spec["changes"])
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    return run_portfolio_cpcv(
        panel,
        pits,
        lambda p, **kw: TrendBook(p, **kw),
        PARAMS,
        cfg=cfg,
        timeframes={k: "1d" for k in panel},
        warmup=WARMUP,
        horizon=HORIZON,
        periods_per_year=CALENDAR_PPY,
        exit_mode="managed",
        trade_manager=_tm(spec),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Book C deep audit and isolated improvement gate")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--skip-cpcv", action="store_true")
    ap.add_argument("--skip-verification", action="store_true")
    ap.add_argument("--skip-cost-stress", action="store_true")
    args = ap.parse_args(argv)

    panel = _load_panel(include_post_2025=False)
    if len(panel) != 39:
        print(f"expected 39 instruments, got {len(panel)}; abort", flush=True)
        return 1

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in SPECS.items():
            ledger.record({
                "book": name,
                "kind": "book_c_deep_audit_2026_08_19",
                "universe": "book_h_gold_39",
                "timeframe": "1d",
                "params": PARAMS,
                "changes": spec["changes"],
            })
        ledger.save(LEDGER_PATH)
    n_trials = ledger.n_trials if not args.no_ledger else n_before + len(SPECS)

    print("=" * 78, flush=True)
    print("BOOK C DEEP AUDIT | pre-2025 iteration | 39 instruments | calendar PPY=365", flush=True)
    print(f"ledger {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}; "
          f"DSR count={n_trials}", flush=True)
    print("=" * 78, flush=True)

    raw = {}
    books = {}
    returns = {}
    for name in SPECS:
        t0 = time.time()
        res = _run(panel, name)
        raw[name] = res
        returns[name] = res.returns
        books[name] = _result_payload(res)
        m = res.metrics
        mt = books[name]["monthly_tail"]
        print(f"{name}: {time.time()-t0:.1f}s | Sharpe {m['sharpe']:.4f} | "
              f"PF {m['profit_factor']:.4f} | DD {m['max_drawdown']:.2%} | "
              f"avg/mo {mt['avg_monthly_pnl']:+,.0f} | trades {m['n_trades']}", flush=True)

    anchor = _anchor_check(books[BASELINE])
    if not anchor["passed"]:
        print(f"LEGACY ANCHOR FAILED: {anchor['mismatch']}", flush=True)
        return 1
    print("legacy Book C anchor: EXACT", flush=True)

    aligned = pd.concat([returns[n].rename(n) for n in SPECS], axis=1).dropna()
    pbo = probability_of_backtest_overfitting(
        aligned.to_numpy(), n_splits=get_config().validation.pbo.n_splits,
        seed=get_config().seed,
    )
    trial_sharpes = [sharpe_ratio(returns[n], periods_per_year=1) for n in SPECS]

    control = books[BASELINE]
    ctrl_m = control["metrics_calendar_365"]
    ctrl_mt = control["monthly_tail"]
    ctrl_paths = []
    for name in SPECS:
        paired = paired_block_bootstrap(
            returns[BASELINE], returns[name], block_size=21, n_bootstraps=10000,
            seed=get_config().seed, periods_per_year=CALENDAR_PPY,
        )
        dsr = deflated_sharpe_ratio(
            returns[name].to_numpy(), trial_sharpes, periods_per_year=CALENDAR_PPY,
            n_trials=n_trials,
        )
        cpcv = None
        if not args.skip_cpcv:
            t0 = time.time()
            cpcv = _cpcv(panel, name)
            print(f"CPCV {name}: {time.time()-t0:.1f}s | {cpcv['oos_sharpe_paths']}", flush=True)
            if name == BASELINE:
                ctrl_paths = cpcv["oos_sharpe_paths"]
        books[name]["paired_vs_control"] = paired
        books[name]["dsr"] = dsr
        books[name]["cpcv"] = cpcv

    # Head-to-head paths require the control to have run; calculate after every CPCV exists.
    eligibility = {}
    for name in SPECS:
        m = books[name]["metrics_calendar_365"]
        mt = books[name]["monthly_tail"]
        paired = books[name]["paired_vs_control"]
        cpcv = books[name]["cpcv"]
        paths_won = None
        cpcv_leg = None
        if cpcv is not None:
            paths = cpcv["oos_sharpe_paths"]
            paths_won = sum(float(b) > float(a) for b, a in zip(paths, ctrl_paths))
            cpcv_leg = bool(
                cpcv["oos_sharpe_median"] > 0
                and sum(float(x) > 0 for x in paths) >= 12
                and paths_won >= 8
            )
        legs = {
            "sharpe_ge_control": m["sharpe"] >= ctrl_m["sharpe"] - 1e-12,
            "profit_factor_ge_control": (m.get("profit_factor") or 0) >= (ctrl_m.get("profit_factor") or 0) - 1e-12,
            "max_drawdown_le_control": m["max_drawdown"] <= ctrl_m["max_drawdown"] + 1e-12,
            "worst_day_ge_control": mt["worst_daily_return"] >= ctrl_mt["worst_daily_return"] - 1e-12,
            "monthly_pnl_ge_95pct_control": mt["avg_monthly_pnl"] >= 0.95 * ctrl_mt["avg_monthly_pnl"],
            "paired_bootstrap_p_lt_010": paired.get("p_value_one_sided", 1.0) < 0.10,
            "dsr_gt_095": books[name]["dsr"].get("dsr", 0.0) > 0.95,
            "cpcv_binding_leg": cpcv_leg,
        }
        eligible = bool(name != BASELINE and all(v is True for v in legs.values()))
        eligibility[name] = {"eligible": eligible, "legs": legs, "cpcv_paths_won": paths_won}
        books[name]["eligibility"] = eligibility[name]

    verification = {}
    if not args.skip_verification:
        full_panel = _load_panel(include_post_2025=True)
        for name in SPECS:
            t0 = time.time()
            res = _run(full_panel, name)
            verification[name] = {
                "metrics_2025_plus": _subset_metrics(res, VERIFY_START),
                "latest_bar": str(res.equity.index.max().date()),
            }
            print(f"verify {name}: {time.time()-t0:.1f}s | "
                  f"Sharpe {verification[name]['metrics_2025_plus'].get('sharpe', 0):.3f}", flush=True)

    cost_stress = {}
    if not args.skip_cost_stress:
        for name in SPECS:
            t0 = time.time()
            res = _run(panel, name, cost_mult=2.0)
            cost_stress[name] = _result_payload(res)
            print(f"2x costs {name}: {time.time()-t0:.1f}s | "
                  f"Sharpe {res.metrics.get('sharpe', 0):.3f}", flush=True)

    # Current-universe-bias diagnostic: remove the 12 hand-selected single stocks.
    robust_panel = {k: v for k, v in panel.items() if k not in set(STOCKS_12)}
    t0 = time.time()
    robust_res = _run(robust_panel, BASELINE)
    universe_bias_stress = {
        "removed": STOCKS_12,
        "n_instruments": len(robust_panel),
        "result": _result_payload(robust_res),
        "interpretation": "Diagnostic only; does not remove ETF constituent or listing-history bias.",
    }
    print(f"no-selected-stocks stress: {time.time()-t0:.1f}s | "
          f"Sharpe {robust_res.metrics.get('sharpe', 0):.3f}", flush=True)

    eligible_names = [n for n, v in eligibility.items() if v["eligible"]]
    overall_pass = bool(eligible_names)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prereg": PREREG,
        "mode": "iteration_plus_nonblind_verification",
        "currency_note": "Cash P&L is account-currency units; no historical quote-to-GBP conversion exists.",
        "annualization_note": "365 used because crypto weekends create union-calendar observations; legacy 252 shown for anchor only.",
        "iteration_end_exclusive": str(ITERATION_END.date()),
        "verification_start": str(VERIFY_START.date()),
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": n_trials,
        "ledger_recorded": not args.no_ledger,
        "legacy_anchor": anchor,
        "pbo_nonbinding": pbo,
        "books": books,
        "eligibility": eligibility,
        "eligible_candidates": eligible_names,
        "verification": verification,
        "cost_stress_2x": cost_stress,
        "universe_bias_stress": universe_bias_stress,
        "stage2": {
            "run": False,
            "reason": ("Stage 2 requires a separately materialized combination of all eligible mechanisms."
                       if len(eligible_names) >= 2 else
                       "Fewer than two isolated mechanisms qualified; no interaction run required."),
        },
        "verdict": "PASS_ISOLATED_CANDIDATE" if overall_pass else "CONTROL_REMAINS_CHAMPION",
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print("=" * 78, flush=True)
    print(f"VERDICT: {out['verdict']} | eligible={eligible_names}", flush=True)
    print(f"results: {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
