"""Per-config trading stats for the intraday candidate report.

run_intraday_candidates.py reports headline-config diagnostics; the MD
deliverable also wants one line per GRID config (trades/yr, net bps/trade) so
the reader can see the variants are all the same (non-)story. Read-only: no
ledger writes, no store writes, no Supabase. Uses the same data loaders as the
campaign so numbers are on identical series.

    cd engine && .venv-mac/bin/python scripts/intraday_config_stats.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

import numpy as np  # noqa: E402

from apex_quant.backtest.engine import Backtester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, get_adapter  # noqa: E402
from run_intraday_candidates import (  # noqa: E402
    CLOSE_MOM_GRID,
    CRYPTO_INSTRUMENTS,
    EXIT_MODE,
    FIX_FLOW_GRID,
    FX_INSTRUMENTS,
    _close_mom_factory,
    _fix_flow_factory,
    _load_binance,
    _load_fx,
    _stressed_crypto_cfg,
)


def stats(pit, inst, factory, params, cfg, years):
    strat = factory(**params)
    strat.fit(pit, pit.as_of(pit.end).index)
    res = Backtester(cfg, exit_mode=EXIT_MODE).run(pit, strat, inst, warmup=250)
    rets = [t.return_pct for t in res.trades]
    return (len(res.trades),
            round(len(res.trades) / years, 1),
            round(float(np.mean(rets)) * 1e4, 2) if rets else None,
            round(res.metrics.get("win_rate", 0.0), 3))


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    adapter = get_adapter(cfg.data.provider)

    print("== close_momentum (BTC/ETH, Binance 1h 2018-2024) ==")
    for inst, fname in CRYPTO_INSTRUMENTS.items():
        df = _load_binance(inst, fname)
        years = (df.index[-1] - df.index[0]).days / 365.25
        pit = PointInTimeAccessor(df)
        for tag, run_cfg in [("rt2.5", cfg), ("rt10", _stressed_crypto_cfg(cfg))]:
            for params in CLOSE_MOM_GRID:
                n, per_yr, bps, win = stats(pit, inst, _close_mom_factory, params, run_cfg, years)
                print(f"  {inst} [{tag}] h={params['holding_horizon']} vf={int(params['vol_filter'])}: "
                      f"{n} trades ({per_yr}/yr), net {bps} bps/trade, win {win * 100:.0f}%")

    print("== fix_flow (EUR/USD, USD/JPY, OANDA 1h 2021-03->2024) ==")
    for inst in FX_INSTRUMENTS:
        df, note = _load_fx(store, adapter, inst)
        years = (df.index[-1] - df.index[0]).days / 365.25
        pit = PointInTimeAccessor(df)
        for params in FIX_FLOW_GRID:
            n, per_yr, bps, win = stats(pit, inst, _fix_flow_factory, params, cfg, years)
            print(f"  {inst} h={params['holding_horizon']} cond={int(params['condition_on_premove'])}: "
                  f"{n} trades ({per_yr}/yr), net {bps} bps/trade, win {win * 100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
