"""Precompute + cache validation reports (CPCV/DSR/PBO are too slow per-request).

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_validation.py                 # default pairs
    .venv\\Scripts\\python.exe scripts/run_validation.py EUR/USD GBP/USD  # specific
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")  # quiet sklearn/lightgbm chatter across many folds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, clean, get_adapter  # noqa: E402
from apex_quant.validation import run_validation  # noqa: E402
from apex_quant.validation.report import STRATEGY_SPECS  # noqa: E402


def main(instruments: list[str], strategies: list[str]) -> None:
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    service = EngineService(cfg)
    end = "2024-12-31"
    start = "2014-01-01"

    for inst in instruments:
        df = clean(adapter.get_history(inst, start, end))
        if len(df) < 300:
            print(f"skip {inst}: only {len(df)} bars")
            continue
        pit = PointInTimeAccessor(df)
        for strat_name in strategies:
            factory, grid = STRATEGY_SPECS[strat_name]
            print(f"\n=== validating {strat_name} on {inst} ({start}..{end}) ===")
            try:
                report = run_validation(
                    pit, inst, strategy_factory=factory, param_grid=grid(), generated_for=end
                )
                path = service.save_validation(report.model_dump(), report.strategy, inst)
                print(" ", report.summary())
                print("  saved ->", path)
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR {strat_name}/{inst}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    strategies = [a for a in args if a in STRATEGY_SPECS] or list(STRATEGY_SPECS)
    instruments = [a for a in args if a not in STRATEGY_SPECS] or ["EUR/USD", "GBP/USD"]
    main(instruments, strategies)
