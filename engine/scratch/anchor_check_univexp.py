"""Pre-gate hard-check: the certified Book H gold anchor must reproduce EXACTLY on the
current snapshot/engine before the universe-expansion gate is believed.

Builds the certified panel exactly as scripts/run_portfolio_gate_trend_ensemble.py does
(insertion order EQUITY_CORE + SGLD.L + crypto + FX_MAJORS_7, mrpt 0.01, managed exits,
warmup 250, iteration window strictly < 2025-01-01, seed 42), runs ONLY the control
[252] full-window, and compares every anchor metric in CERTIFIED_GOLD.

No ledger interaction, no CPCV. Read-only against the store.

Usage: cd engine && .venv-mac/bin/python scratch/anchor_check_univexp.py
Exit 0 iff the anchor reproduces within the ensemble gate's own tolerance.
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.data import ParquetStore, PointInTimeAccessor, clean  # noqa: E402
from apex_quant.config import get_config  # noqa: E402

from run_portfolio_gate import DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, _utc  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402
from run_portfolio_gate_trend_ensemble import (  # noqa: E402
    CERTIFIED_GOLD,
    UNIVERSE_GOLD,
    _cfg,
    _params,
    _run,
)


def main() -> int:
    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(DEFAULT_HOLDOUT_START)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
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
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    print(f"panel: {len(panel)} instruments (expect 39: 21 equity+ETC, 11 crypto, 7 FX)")
    assert list(panel.keys())[: len(EQUITY_CORE) + 1] == EQUITY_CORE + [GOLD_ETC], \
        "certified insertion order broken"

    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    t0 = time.time()
    res = _run(panel, pits, timeframes, _params([252]), _cfg())
    m = res.metrics
    print(f"control run: {time.time() - t0:.0f}s | {res.summary()}")

    mismatch = {k: (m[k], v)
                for k, v in CERTIFIED_GOLD.items()
                if abs(m[k] - v) > (0.5 if k in ("n_trades",) else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}")
        return 1
    print(f"ANCHOR EXACT: sharpe {m['sharpe']:.5f} | PF {m['profit_factor']:.5f} | "
          f"win {m['win_rate']*100:.2f}% | maxDD {m['max_drawdown']*100:.2f}% | "
          f"{m['n_trades']} trades | equity {m['final_equity']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
