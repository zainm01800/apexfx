"""Gate-test the ensemble vote sleeve on the real 22-pair daily FX panel.

The last honest hypothesis inside the current dataset: individually-rejected
sleeves (TS momentum, CS-pairs, CS-currency, carry) may pass TOGETHER where none
passed alone. Same three gates as everything else. ``n_trials=40`` deliberately
over-charges the DSR for the sweeps already spent on the component sleeves
(cs 7 + currency 4 + carry 4 + ts 1 + ensemble thresholds 2 = 18 kept configs,
plus the unrecorded variants tried along the way) — deflating by less would
flatter the result.

Exit code 0 on PASS, 1 on FAIL, so shells can branch on the verdict.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.data.store import ParquetStore
from apex_quant.strategies.ensemble import EnsembleVote
from apex_quant.validation import run_portfolio_validation

N_TRIALS_HONEST = 40


def factory(panel, **params):
    return EnsembleVote(panel, **params)


def main() -> int:
    cfg = get_config()
    store = ParquetStore()
    panel = {}
    for inst in cfg.data.instruments:
        df = store.load(inst, "1d")
        if len(df) >= 400:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    print(f"--- Ensemble vote validation on {len(panel)} FX pairs (daily) ---", flush=True)

    grid = [
        {"min_votes": 2},   # headline
        {"min_votes": 3},   # stricter-agreement variant (part of the trial set)
    ]
    rep = run_portfolio_validation(
        panel, pits, factory, grid,
        strategy_name="ensemble_vote",
        timeframes={k: "1d" for k in panel},
        warmup=250, horizon=21, periods_per_year=252,
        generated_for="2026-07-17",
        n_trials=N_TRIALS_HONEST,
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
