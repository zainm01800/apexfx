"""Pre-registered portfolio-level gate: the diversified daily trend book, +/- carry.

The academically defensible claim (docs/research/2026-07-17_fx_edges_evidence.md:
Hurst/Ooi/Pedersen; Moskowitz/Ooi/Pedersen) is about the DIVERSIFIED vol-scaled
trend book across markets - not single pairs. The 2026-07-17 single-pair sweep
(engine/data_store/candidate_sweep_2026-07-17.md) rejected every pair but showed
the right direction of travel (EUR/USD carry-filtered: 87% positive CPCV paths).
This script runs the ONE pre-registered book-level hypothesis through the same
three gates as everything else:

  * Book A: all config forex pairs, baseline 1d stack (RegimeGatedMomentum wrapped
    in MultiTimeframeMomentum htf_rule="1w"/htf_ma_window=50 - the same wiring the
    live scanner trades, see scripts/run_live_paper_trading.py) at
    lookback 126 / vol 63 / hold 21 / rr 1.5 / rule_based regime, managed exits,
    vol-scaled sizing, per-pair v5 costs, config risk caps binding.
  * Book B: identical but each pair's signal wrapped in CarryTrendFilter
    (strategies/carry_trend_filter.py - veto when the trade direction pays
    negative carry; point-in-time policy rates).

Gate (mirrors validation/portfolio_report.run_portfolio_validation exactly):
  DSR > 0.95 (deflated by the shared TrialLedger's FULL count), PBO < 0.5
  (across the two books - the whole pre-registered selection set), CPCV median
  OOS Sharpe > 0 with > 50% of 15 paths positive.

Thin orchestration, no new math: PortfolioBacktester + run_portfolio_cpcv +
deflated_sharpe_ratio + probability_of_backtest_overfitting, composed so the two
full-window runs double as the DSR/PBO inputs AND the trade-metrics source
(run_portfolio_validation would run the same backtests again per book). The
TrendBook adapter plays the role EnsembleVote plays for validate_ensemble.py.

Honesty rules (same as run_candidate_check.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 2 new trials are recorded in the shared TrialLedger (book A, book B)
    BEFORE the runs, and the ledger's full updated count deflates both DSRs.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate.py                 # full 22-pair gate
    .venv-mac/bin/python scripts/run_portfolio_gate.py --instruments EUR/USD,USD/JPY,GBP/USD

Exit code 0 if both books pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.baseline import RegimeGatedMomentum  # noqa: E402
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter  # noqa: E402
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import (  # noqa: E402
    DSR_THRESHOLD,
    PBO_THRESHOLD,
    run_portfolio_cpcv,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "portfolio_gate_2026-07-17.json"
DEFAULT_HOLDOUT_START = "2025-01-01"
MIN_BARS = 300                 # same floor as scripts/run_backtests.py
WARMUP = 250                   # portfolio_report default; covers 1w x 50 HTF gate + lookback 126
HORIZON = 21                   # CPCV purge = holding horizon, as in the single-instrument gate

# ── Pre-registered configurations (the full selection set: 2 trials) ──────────
COMMON_PARAMS = {
    "momentum_lookback": 126,
    "vol_window": 63,
    "holding_horizon": 21,
    "reward_risk": 1.5,
    "regime_method": "rule_based",
    "timeframe": "1d",
    "htf_rule": "1w",
    "htf_ma_window": 50,
}
BOOKS = {
    "book_a_plain_trend": {"carry_filter": False, **COMMON_PARAMS},
    "book_b_carry_filtered": {"carry_filter": True, **COMMON_PARAMS},
}


class TrendBook:
    """The diversified daily trend book as one portfolio-level model.

    Mirrors the EnsembleVote interface (``.strategies()`` only): with
    ``bypass_calibration=True`` each per-pair signal is a deterministic
    point-in-time function of the data, so - exactly like the ensemble sleeve -
    there is nothing to fit and CPCV's purged train split is intentionally
    unused (see validation/portfolio_report.py's note on rule-based sleeves).
    """

    def __init__(self, panel: dict, *, carry_filter: bool = False, **params) -> None:
        self.instruments = list(panel.keys())
        self.params = {"carry_filter": carry_filter, **params}
        self._strategies = {}
        for inst in self.instruments:
            if carry_filter:
                # the wrapper builds its own RegimeGatedMomentum base internally;
                # instrument= is REQUIRED here - it scopes the base strategy's
                # class-level Bollinger cache and picks the forex asset class
                base = CarryTrendFilter(
                    momentum_lookback=params["momentum_lookback"],
                    vol_window=params["vol_window"],
                    holding_horizon=params["holding_horizon"],
                    reward_risk=params["reward_risk"],
                    regime_method=params["regime_method"],
                    timeframe=params["timeframe"],
                    instrument=inst,
                )
            else:
                base = RegimeGatedMomentum(
                    momentum_lookback=params["momentum_lookback"],
                    vol_window=params["vol_window"],
                    holding_horizon=params["holding_horizon"],
                    reward_risk=params["reward_risk"],
                    regime_method=params["regime_method"],
                    timeframe=params["timeframe"],
                    instrument=inst,
                )
            self._strategies[inst] = MultiTimeframeMomentum(
                base_strategy=base,
                htf_rule=params["htf_rule"],
                htf_ma_window=params["htf_ma_window"],
                instrument=inst,
            )

    def strategies(self) -> dict:
        return dict(self._strategies)


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _max_gross_leverage(res) -> float:
    """Approx peak gross leverage (sum of |notional| of overlapping trades / equity),
    reconstructed from the trade list. Quote-currency conversion is ignored, so
    treat as an approximation - the constraint_log is the authoritative cap record.
    (Same helper as scripts/run_candidate_check.py.)"""
    eq = res.equity
    if eq.empty or not res.trades:
        return 0.0
    gross = pd.Series(0.0, index=eq.index)
    for tr in res.trades:
        t0, t1 = _utc(tr.entry_time), _utc(tr.exit_time)
        gross[(gross.index >= t0) & (gross.index < t1)] += abs(tr.entry_price * tr.units)
    lev = (gross / eq.replace(0.0, np.nan)).max()
    return float(lev) if np.isfinite(lev) else 0.0


def _cap_families(constraint_log: dict) -> str:
    """Aggregate parameterized entries (e.g. "regime_scale=0.13") into their family."""
    fam: dict[str, int] = {}
    for k, v in constraint_log.items():
        fam[k.split("=")[0]] = fam.get(k.split("=")[0], 0) + v
    return ", ".join(f"{k}x{v}" for k, v in sorted(fam.items())) or "none"


def _gate(name: str, rets: pd.Series, trial_sharpes: list[float], pbo: dict,
          cpcv: dict, n_trials: int) -> dict:
    """The three gates, identical to portfolio_report.run_portfolio_validation's."""
    dsr = deflated_sharpe_ratio(rets.to_numpy(), trial_sharpes, 252, n_trials=n_trials)
    dsr_pass = dsr.get("dsr", 0.0) > DSR_THRESHOLD
    pbo_val = pbo.get("pbo")
    pbo_pass = pbo_val is not None and pbo_val < PBO_THRESHOLD
    cpcv_pass = cpcv.get("oos_sharpe_median", 0.0) > 0 and cpcv.get("frac_positive", 0.0) > 0.5
    passed = bool(dsr_pass and pbo_pass and cpcv_pass)
    return {
        "book": name,
        "passed": passed,
        "dsr_pass": dsr_pass,
        "pbo_pass": pbo_pass,
        "cpcv_pass": cpcv_pass,
        "dsr": dsr,
        "pbo": pbo,
        "cpcv": cpcv,
        "reasons": [
            f"DSR {dsr.get('dsr', 0):.3f} {'>' if dsr_pass else '<='} {DSR_THRESHOLD} "
            f"(deflated by {dsr.get('n_trials')} trials)",
            f"PBO {pbo_val if pbo_val is not None else 'n/a'} "
            f"{'<' if pbo_pass else '>='} {PBO_THRESHOLD} (book-selection overfit probability)",
            f"CPCV median OOS Sharpe {cpcv.get('oos_sharpe_median', 0):.3f}, "
            f"{cpcv.get('frac_positive', 0)*100:.0f}% of {cpcv.get('n_paths', 0)} paths positive",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: diversified daily "
                                             "trend book +/- carry filter (iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: all config forex pairs)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + 2)")
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    instruments = ([s.strip() for s in args.instruments.split(",") if s.strip()]
                   or list(cfg.data.instruments))

    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        panel[inst] = df
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 2 pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in BOOKS.items():
            ledger.record({"book": name, "universe": "config_forex_22", "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE 2026-07-17 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} pairs | window: {min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"books: {list(BOOKS)} | ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per book -> returns (DSR/PBO) + trade metrics, one shared
    #    equity curve with config risk caps binding.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, params in BOOKS.items():
        t_start = time.time()
        model = TrendBook(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": params, "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"maxDD={m['max_drawdown']*100:.1f}% lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

    # 2. PBO across the two books - the whole pre-registered selection set.
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} books: {pbo}", flush=True)

    # 3. CPCV OOS distribution per book (the same 15 paths as the single-pair gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, params in BOOKS.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if all(v["passed"] for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
