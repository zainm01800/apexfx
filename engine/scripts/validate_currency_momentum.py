#!/usr/bin/env python3
"""Gate-test monthly currency-leg cross-sectional momentum on the 22-pair daily panel.

Same three gates as everything else (DSR / PBO / CPCV via
``run_portfolio_validation``), same honesty rules as ``run_candidate_check.py``:

  * ITERATION window only: panel is sliced strictly before 2025-01-01; the
    2025+ holdout is never loaded here.
  * Trials are recorded in the SHARED ledger
    (``data_store/validation/trial_ledger.json``) and the DSR is deflated by the
    ledger's full count — not by this script's grid alone.
  * Per-pair realized costs from config v5 apply via PortfolioBacktester.

Grid is the monthly-rotation evidence candidate (docs/research/2026-07-17,
sec.3): 1/3/6-month formation, ~1-month holding, top/bottom-k currencies.
Headline: 3-month formation, k=2, 21-bar holding.

Exit code 0 on PASS, 1 on FAIL, so shells can branch on the verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, clean
from apex_quant.data.store import ParquetStore
from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum
from apex_quant.validation import TrialLedger, run_portfolio_validation

LEDGER_PATH = Path(__file__).resolve().parent.parent / "data_store/validation/trial_ledger.json"
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
MIN_BARS = 300


def ccm_factory(panel, **params):
    return CurrencyCrossSectionalMomentum(panel, **params)


def ccm_grid():
    """Monthly cross-sectional momentum grid; grid[0] is the headline."""
    return [
        {"lookback": 63, "k": 2, "holding_horizon": 21},   # headline: 3-mo formation, monthly rotation
        {"lookback": 21, "k": 2, "holding_horizon": 21},   # 1-mo formation
        {"lookback": 126, "k": 2, "holding_horizon": 21},  # 6-mo formation
        {"lookback": 63, "k": 1, "holding_horizon": 21},   # concentration variant
        {"lookback": 63, "k": 3, "holding_horizon": 21},   # breadth variant
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate currency-leg XS momentum (shared ledger, "
                                             "iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: all config forex pairs)")
    ap.add_argument("--grid", default="",
                    help="JSON list of param dicts (default: the 5-config monthly grid); "
                         "grid[0] is the headline")
    ap.add_argument("--universe-tag", default="",
                    help="ledger universe label (default: FX<n>_PORTFOLIO from the universe size)")
    args = ap.parse_args()

    cfg = get_config()
    store = ParquetStore(cfg.store_path)

    instruments = ([s.strip() for s in args.instruments.split(",") if s.strip()]
                   or list(cfg.data.instruments))
    panel = {}
    for inst in instruments:
        df = clean(store.load(inst, "1d"))
        df = df[df.index < HOLDOUT_START]  # strict iteration window
        if len(df) >= MIN_BARS:
            panel[inst] = df
        else:
            print(f"Skipping {inst} (insufficient bars in window: {len(df)})")
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    generated_for = str(max(df.index[-1] for df in panel.values()).date())
    print(f"--- Currency-leg XS momentum validation on {len(panel)} FX pairs (daily, "
          f"iteration window -> {generated_for}) ---", flush=True)

    # Shared ledger: record this grid BEFORE validating so this script's own
    # trials count toward the deflation denominator (dedup is canonical-JSON).
    ledger = TrialLedger.load(LEDGER_PATH)
    grid = json.loads(args.grid) if args.grid else ccm_grid()
    universe_tag = args.universe_tag or f"FX{len(panel)}_PORTFOLIO"
    for params in grid:
        ledger.record({"instrument": universe_tag, "timeframe": "1d",
                       "factory": "currency_xs_momentum", "params": params})
    ledger.save(LEDGER_PATH)
    n_trials = ledger.n_trials
    print(f"shared ledger: {LEDGER_PATH} (n_trials={n_trials} used for DSR deflation)")

    rep = run_portfolio_validation(
        panel, pits, ccm_factory, grid,
        strategy_name="currency_cross_sectional_momentum",
        timeframes={k: "1d" for k in panel},
        warmup=250, horizon=21, periods_per_year=252,
        generated_for=generated_for,
        n_trials=n_trials,
    )

    print("\n" + "=" * 60)
    print("VALIDATION REPORT:")
    print("=" * 60)
    print(rep.summary())
    for r in rep.verdict["reasons"]:
        print("  -", r)
    print(f"  CPCV paths: {rep.cpcv.get('oos_sharpe_paths')}")
    print("=" * 60)
    return 0 if rep.verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
