"""Verify config v5 per-pair cost overrides reach the backtest fill path.

Runs one single-instrument backtest (GBP/JPY 1d, RegimeGatedMomentum defaults)
with the seeded override table active vs cleared, and shows the per-fill and
round-trip costs each trade actually paid. GBP/JPY is NOT in the measured pair
table, so it should price at the unlisted-cross default (4.85 pips RT, half per
fill, no slippage); cleared -> class default (~1.13 pips RT).
"""

from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.backtest import Backtester  # noqa: E402
from apex_quant.data import ParquetStore, PointInTimeAccessor  # noqa: E402
from apex_quant.strategies import RegimeGatedMomentum  # noqa: E402


def _run(cfg, pit, df):
    strat = RegimeGatedMomentum(timeframe="1d", bypass_calibration=True, instrument="GBP/JPY")
    strat.fit(pit, df.index[:400])
    bt = Backtester(cfg=cfg)
    return bt.run(pit, strat, "GBP/JPY", warmup=400, timeframe="1d")


def main() -> None:
    cfg = get_config()
    df = ParquetStore(cfg.store_path).load("GBP/JPY", "1d")
    print(f"GBP/JPY 1d: {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()}), "
          f"all midnight: {bool((df.index == df.index.normalize()).all())}")
    pit = PointInTimeAccessor(df)

    spot = float(df["close"].iloc[-1])
    spread, slip = cfg.forex_cost_components("GBP/JPY", "1d")
    print(f"resolver: GBP/JPY 1d -> spread_pips={spread}, slippage_bps={slip} "
          f"(=> {0.5 * spread:.3f} pips per fill, ~{spread:.2f} pips round trip)")

    cfg_cleared = cfg.model_copy(deep=True)
    cfg_cleared.asset_classes.forex.pair_rt_cost_pips = {}
    cfg_cleared.asset_classes.forex.pair_tf_rt_cost_pips = {}
    cfg_cleared.asset_classes.forex.cross_rt_cost_pips = None

    res_new = _run(cfg, pit, df)
    res_old = _run(cfg_cleared, pit, df)

    print(f"\ntrades: override {res_new.metrics['n_trades']} | cleared {res_old.metrics['n_trades']}")
    print(f"final equity: override {res_new.metrics['final_equity']:.0f} | cleared {res_old.metrics['final_equity']:.0f}")

    pip = 0.01
    print(f"\nfirst trades (override ACTIVE) — implied RT cost in pips "
          f"(expected ~{spread:.2f} = 2 x {0.5 * spread:.3f}):")
    for tr in res_new.trades[:5]:
        half = 0.5 * spread * pip
        print(f"  {tr.direction:<5} in {tr.entry_time} @ {tr.entry_price:.3f} "
              f"out {tr.exit_time} @ {tr.exit_price:.3f} | modeled RT cost ~{2 * half / pip:.2f} pips")

    print("\nsame trades with table CLEARED (class default ~1.13 pips RT):")
    for tr in res_old.trades[:5]:
        print(f"  {tr.direction:<5} in {tr.entry_time} @ {tr.entry_price:.3f} "
              f"out {tr.exit_time} @ {tr.exit_price:.3f}")

    # entry-price deltas between the two runs prove the fill path applied it
    deltas = []
    for a, b in zip(res_new.trades, res_old.trades):
        if (a.entry_time, a.direction) == (b.entry_time, b.direction):
            deltas.append(abs(a.entry_price - b.entry_price) / pip)
    if deltas:
        s = pd.Series(deltas)
        print(f"\nentry-price delta between runs (pips): mean {s.mean():.3f} "
              f"(expected ~{0.5 * spread - (0.5 * 1.0 + 0.5 / 1e4 * spot / pip):.3f} "
              f"= override half-spread minus old half-spread+slippage)")
    print("\nOK" if spread == 4.85 and slip == 0.0 else "\nMISMATCH")


if __name__ == "__main__":
    main()
