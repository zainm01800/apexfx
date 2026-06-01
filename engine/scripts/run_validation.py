"""Precompute + cache validation reports (CPCV/DSR/PBO are too slow per-request).

Usage:
    cd engine
    .venv\\Scripts\\python.exe scripts/run_validation.py                 # default pairs
    .venv\\Scripts\\python.exe scripts/run_validation.py EUR/USD GBP/USD  # specific
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, clean, get_adapter  # noqa: E402
from apex_quant.validation import run_validation  # noqa: E402


def main(instruments: list[str]) -> None:
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    service = EngineService(cfg)
    end = "2024-12-31"
    start = "2014-01-01"

    for inst in instruments:
        print(f"\n=== validating {inst} ({start}..{end}) ===")
        try:
            df = clean(adapter.get_history(inst, start, end))
            if len(df) < 300:
                print(f"  skip {inst}: only {len(df)} bars")
                continue
            pit = PointInTimeAccessor(df)
            report = run_validation(pit, inst, generated_for=end)
            path = service.save_validation(report.model_dump(), report.strategy, inst)
            print(" ", report.summary())
            print("  saved ->", path)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {inst}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    default = ["EUR/USD", "GBP/USD"]
    main(args or default)
