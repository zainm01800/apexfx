"""Scratch: reproduce the certified Book H gold gap-aware anchor exactly.

Mirrors run_portfolio_gate_early_partial.py's baseline path (mrpt=0.01, certified
panel insertion order EQUITY_CORE first, exit_mode=managed, warmup=250, seed 42,
iteration window strictly < 2025-01-01). Hard-checks against
book_h_gapaware_2026-07-22.json (gold).
"""

from __future__ import annotations

import copy
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402

from run_portfolio_gate import COMMON_PARAMS, MIN_BARS, WARMUP, TrendBook, _utc  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}


def main() -> int:
    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc("2025-01-01")

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
    print(f"panel: {len(panel)} instruments")
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = 0.01
    t0 = time.time()
    model = TrendBook(panel, **GOLD_PARAMS)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
    dt = time.time() - t0
    m = res.metrics
    print(f"full run: {dt:.0f}s | {res.summary()}")
    mismatch = {k: (m.get(k), v) for k, v in CERTIFIED_GOLD.items()
                if abs(m.get(k, float("nan")) - v) > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}")
        return 1
    print(f"ANCHOR EXACT: sharpe {m['sharpe']:.5f} trades {m['n_trades']} "
          f"equity {m['final_equity']:.2f} | elapsed {dt:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
