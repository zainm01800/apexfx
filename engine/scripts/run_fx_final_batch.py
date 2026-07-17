#!/usr/bin/env python3
"""FX final batch runner (2026-07-17) — pre-registered:
``data_store/fx_final_batch_2026-07-17.md``.

Thin glue over the run_candidate_check machinery (same data loading, same
``run_validation`` gate, same TrialLedger). Two additions only:

  * ``--record-plan``: records EVERY config of the pre-registered batch plan in
    the shared TrialLedger BEFORE any run, so each run can be deflated by the
    FINAL count (n=150), per the pre-reg. Idempotent (canonical-JSON dedup).
  * Per-run in-memory cost override: ``pair_rt_cost_pips[instrument] = X`` on a
    deep copy of the config (the override IS the full round-trip cost in pips,
    applied half per fill, slippage 0 — see AppConfig.forex_cost_components).
    config.yaml is never touched.

Q1 (cost sensitivity): carry_trend grid on EUR/USD and USD/CHF 1d @ 0.6 pip RT.
Q2 (vol-managed overlay): vol_managed grid on EUR/USD 1d @ current costs (None)
and @ 0.6 pip RT.

Honesty rules (unchanged): iteration window strictly < 2025-01-01 (no --final,
holdout never loaded); seed 42 from config; DSR > 0.95 deflated by the ledger's
FULL count, PBO < 0.5, CPCV median OOS > 0 with > 50% of 15 paths positive.
Results persist to ONE local JSON (no Supabase posts in this batch, no
overwrites of existing per-pair validation caches). The --diag backtests
re-evaluate already-ledgered headline configs for observability only (dedup =>
no new trials) and double as the determinism check.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_fx_final_batch.py --record-plan   # FIRST, once
    .venv-mac/bin/python scripts/run_fx_final_batch.py --only 0        # Q1 EUR/USD @0.6
    .venv-mac/bin/python scripts/run_fx_final_batch.py --only 1        # Q1 USD/CHF @0.6
    .venv-mac/bin/python scripts/run_fx_final_batch.py --only 2        # Q2 overlay @current
    .venv-mac/bin/python scripts/run_fx_final_batch.py --only 3        # Q2 overlay @0.6
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:  # scripts in this repo load engine/.env so Supabase/Oanda creds are present
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.validation.report import run_validation  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from scripts.run_candidate_check import (  # noqa: E402
    DEFAULT_HOLDOUT_START,
    DEFAULT_START,
    LEDGER_PATH,
    MIN_BARS,
    _carry_trend_factory,
    _load_history,
    _print_gate,
    _utc,
)

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "fx_final_batch_2026-07-17.json"
WARMUP = 250  # same as run_candidate_check / run_validation trial-matrix backtests


def _vol_managed_factory(**params):
    from apex_quant.strategies.vol_managed_overlay import VolManagedCarryTrend

    return VolManagedCarryTrend(**params)


FACTORIES = {"carry_trend": _carry_trend_factory, "vol_managed": _vol_managed_factory}

# -- the pre-registered batch plan (mirrors data_store/fx_final_batch_2026-07-17.md)
BASE = {"momentum_lookback": 126, "vol_window": 63, "holding_horizon": 21,
        "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": "1d"}
GRID_Q1 = [
    dict(BASE),                                            # headline 126/63/21/1.5
    {**BASE, "reward_risk": 2.0},                          # mate
    {**BASE, "holding_horizon": 10, "reward_risk": 2.0},   # mate
]
GRID_Q2 = [
    {**BASE, "stand_down": True},                          # headline: damp + stand-down
    {**BASE, "stand_down": False},                         # damp only
    {**BASE, "reward_risk": 2.0, "stand_down": True},      # mate
]
PLAN = [
    {"key": "q1_eur_usd_raw", "instrument": "EUR/USD", "factory": "carry_trend",
     "grid": GRID_Q1, "cost": 0.6},
    {"key": "q1_usd_chf_raw", "instrument": "USD/CHF", "factory": "carry_trend",
     "grid": GRID_Q1, "cost": 0.6},
    {"key": "q2_overlay_current", "instrument": "EUR/USD", "factory": "vol_managed",
     "grid": GRID_Q2, "cost": None},
    {"key": "q2_overlay_raw", "instrument": "EUR/USD", "factory": "vol_managed",
     "grid": GRID_Q2, "cost": 0.6},
]


def record_plan() -> int:
    """Record every planned config in the shared ledger BEFORE any run."""
    ledger = TrialLedger.load(LEDGER_PATH)  # fresh
    n_before = ledger.n_trials
    for entry in PLAN:
        for params in entry["grid"]:
            ledger.record({"instrument": entry["instrument"], "timeframe": "1d",
                           "factory": entry["factory"], "params": params})
    ledger.save(LEDGER_PATH)
    print(f"record-plan: ledger {n_before} -> {ledger.n_trials} "
          f"(+{ledger.n_trials - n_before} new; budget <= 6) at {LEDGER_PATH}")
    return 0


def _save_slice(key: str, payload: dict) -> None:
    data = {}
    if RESULTS_PATH.exists():
        data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    data[key] = payload
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RESULTS_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"  results slice '{key}' -> {RESULTS_PATH.name}")


def _diag_backtest(pit, instrument: str, factory, headline: dict, cfg) -> dict:
    """Headline config through the plain Backtester, twice: trade stats + overlay
    counters + an exact-equity determinism check. Same already-ledgered config —
    observability only, no new trial."""
    from apex_quant.backtest.engine import Backtester

    eqs, stats = [], {}
    for rep_i in range(2):
        strat = factory(**headline)
        strat.fit(pit, pit.as_of(pit.end).index)
        res = Backtester(cfg, exit_mode="managed").run(pit, strat, instrument, warmup=WARMUP)
        eqs.append(res.equity)
        if rep_i == 0:
            m = res.metrics
            stats = {
                "n_trades": m.get("n_trades"), "sharpe": m.get("sharpe"),
                "total_return": m.get("total_return"), "max_drawdown": m.get("max_drawdown"),
                "expectancy_pnl": m.get("expectancy_pnl"), "profit_factor": m.get("profit_factor"),
                "win_rate": m.get("win_rate"),
                "n_signals": getattr(strat, "n_signals", None),
                "n_scaled": getattr(strat, "n_scaled", None),
                "n_standdowns": getattr(strat, "n_standdowns", None),
                "n_vetoes": getattr(strat, "n_vetoes", getattr(getattr(strat, "base", None), "n_vetoes", None)),
            }
    stats["determinism_equity_identical"] = bool(eqs[0].equals(eqs[1]))
    return stats


def run_slice(idx: int, *, diag: bool, tag: str | None = None) -> int:
    entry = PLAN[idx]
    inst, factory_name, grid, cost = entry["instrument"], entry["factory"], entry["grid"], entry["cost"]
    slice_key = entry["key"] + (f"__{tag}" if tag else "")
    factory = FACTORIES[factory_name]

    ledger = TrialLedger.load(LEDGER_PATH)  # fresh, per the rules
    for params in grid:  # idempotent; --record-plan already recorded these
        ledger.record({"instrument": inst, "timeframe": "1d", "factory": factory_name,
                       "params": params})
    n_trials = ledger.n_trials
    ledger.save(LEDGER_PATH)

    cfg = get_config().model_copy(deep=True)
    if cost is not None:
        cfg.asset_classes.forex.pair_rt_cost_pips[inst] = float(cost)
    eff_spread, eff_slip = cfg.forex_cost_components(inst, "1d")

    print("=" * 72)
    print(f"FX FINAL BATCH '{slice_key}' | {inst} | factory={factory_name} | grid={len(grid)}")
    print(f"cost override: {cost!r} -> effective (rt_pips={eff_spread}, slippage_bps={eff_slip})")
    print(f"ledger n_trials={n_trials} (DSR deflation denominator; final-count rule)")
    print("=" * 72)

    adapter = get_adapter(cfg.data.provider)
    store = ParquetStore(cfg.store_path)
    df = clean(_load_history(store, adapter, inst, DEFAULT_START, DEFAULT_HOLDOUT_START, "1d"))
    df = df[df.index < _utc(DEFAULT_HOLDOUT_START)]
    # The rebuilt parquet store has phantom weekend (Sunday-stub) bars removed, but
    # _load_history's adapter gap-fill re-injects them for any range the cache does
    # not fully cover (fetched adapter bars are distinct Sunday dates, so the
    # date-dedup cannot drop them). Match the rebuilt store's convention for the
    # whole merged frame: no weekend bars.
    df = df[df.index.dayofweek < 5]
    if len(df) < MIN_BARS:
        print(f"skip {inst}: {len(df)} bars in window")
        return 1
    pit = PointInTimeAccessor(df)
    print(f"{inst}: {len(df)} bars ({pit.start.date()} -> {pit.end.date()}) [iteration only]")

    rep = run_validation(pit, inst, strategy_factory=factory, param_grid=grid,
                         cfg=cfg, generated_for=str(pit.end.date()), n_trials=n_trials)
    ok = _print_gate(rep)

    payload = {
        "instrument": inst, "factory": factory_name, "timeframe": "1d",
        "cost_rt_pips_override": cost,
        "effective_cost_components": {"rt_pips": eff_spread, "slippage_bps": eff_slip},
        "grid": grid, "n_trials": n_trials,
        "dsr": rep.dsr, "pbo": rep.pbo, "cpcv": rep.cpcv, "verdict": rep.verdict,
    }
    if diag:
        print("  diag backtest (headline config, already ledgered — no new trial):")
        d = _diag_backtest(pit, inst, factory, grid[0], cfg)
        payload["diag"] = d
        print(f"    {json.dumps(d, default=str)}")
    _save_slice(slice_key, payload)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FX final batch runner (pre-registered 2026-07-17).")
    ap.add_argument("--record-plan", action="store_true",
                    help="record ALL planned configs in the ledger, then exit (run FIRST)")
    ap.add_argument("--only", type=int, choices=range(len(PLAN)), default=None,
                    help="run only this plan slice (see PLAN in the source)")
    ap.add_argument("--diag", action="store_true",
                    help="also run the headline-config diagnostic/determinism backtest")
    ap.add_argument("--tag", default=None,
                    help="suffix appended to the results slice key (e.g. a re-run tag); "
                         "leaves the original slices untouched")
    args = ap.parse_args(argv)

    if args.record_plan:
        return record_plan()
    if args.only is None:
        for i, e in enumerate(PLAN):
            print(f"  --only {i}: {e['key']} ({e['instrument']}, {e['factory']}, cost={e['cost']})")
        print("nothing to do: pass --record-plan first, then --only N per slice")
        return 0
    return run_slice(args.only, diag=args.diag, tag=args.tag)


if __name__ == "__main__":
    sys.exit(main())
