"""Harvest full-window (iteration window, <2025-01-01) trade stats for the
majors re-gate headline configs: expectancy, profit factor, trade count,
entries/month. Mirrors run_candidate_check.py's loading and run_validation's
full-backtest call exactly (same Backtester, exit_mode="managed", warmup=250,
same factory construction with timeframe injected, no `instrument` kwarg).

Adds NO trials to the ledger — these are the same already-recorded configs.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:
    pass

import pandas as pd  # noqa: E402

from apex_quant.backtest.engine import Backtester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.baseline import RegimeGatedMomentum  # noqa: E402
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter  # noqa: E402

HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
HEADLINE = {"timeframe": "1d", "momentum_lookback": 126, "vol_window": 126,
            "holding_horizon": 10, "reward_risk": 1.5, "regime_method": "rule_based"}


def window_pit(store: ParquetStore, inst: str) -> PointInTimeAccessor:
    df = clean(store.load(inst, "1d"))
    df = df[df.index < HOLDOUT_START]
    return PointInTimeAccessor(df)


def stats_for(bt: Backtester, pit: PointInTimeAccessor, inst: str, factory, params: dict) -> dict:
    strat = factory(**params)
    strat.fit(pit, pit.as_of(pit.end).index)
    res = bt.run(pit, strat, inst, warmup=250)
    m = res.metrics
    months = (pit.end - pit.start).days / 30.4375
    n = m.get("n_trades", 0)
    return {
        "instrument": inst,
        "strategy": getattr(strat, "name", "strategy"),
        "window": f"{pit.start.date()} -> {pit.end.date()}",
        "n_trades": n,
        "entries_per_month": round(n / months, 2) if months > 0 else None,
        "expectancy_pnl": m.get("expectancy_pnl"),
        "expectancy_pct": m.get("expectancy_pct"),
        "profit_factor": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "sharpe": m.get("sharpe"),
        "total_return": m.get("total_return"),
        "max_drawdown": m.get("max_drawdown"),
        "net_pnl": m.get("net_pnl"),
    }


def main() -> None:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    bt = Backtester(cfg, exit_mode="managed")
    out = []
    for inst in MAJORS:
        pit = window_pit(store, inst)
        out.append(stats_for(bt, pit, inst, RegimeGatedMomentum, HEADLINE))
        print(f"{inst}: {out[-1]['n_trades']} trades, "
              f"PF={out[-1]['profit_factor']}, exp={out[-1]['expectancy_pnl']:.2f}")
    pit = window_pit(store, "EUR/USD")
    out.append(stats_for(bt, pit, "EUR/USD", CarryTrendFilter, HEADLINE))
    print(f"EUR/USD carry: {out[-1]['n_trades']} trades, "
          f"PF={out[-1]['profit_factor']}, exp={out[-1]['expectancy_pnl']:.2f}")

    dest = ENGINE_DIR / "scratch" / "majors_regate_metrics.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"written: {dest}")


if __name__ == "__main__":
    main()
