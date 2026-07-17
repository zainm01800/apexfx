#!/usr/bin/env python3
"""Harvest trade-level stats for the FX Majors Stack verdicts doc.

Re-runs ONLY the identical pre-registered headline configurations
(data_store/fx_majors_stack_prereg_2026-07-17.md) to extract expectancy / profit
factor / maxDD / per-pair P&L, which the gate scripts do not persist (Sleeve A
single-instrument reports) or persist only partially. NO ledger interaction:
identical configs, already recorded; this is a reporting aid, not a new trial.

Sleeve A window mirrors run_candidate_check.py (2014-01-01 -> <2025-01-01,
adapter-filled); Sleeves C window mirrors the book gates (store cache,
2016 -> <2025-01-01). Both use the same engines as the gates:
Backtester(managed, warmup=250) for A, PortfolioBacktester(managed, warmup=250)
for C.

Output: data_store/validation/fx_majors_stack_metrics_harvest.json
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.engine import Backtester  # noqa: E402
from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter  # noqa: E402
from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum  # noqa: E402
from run_candidate_check import _load_history  # noqa: E402  (same loader as the gate)

MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
CTF_PARAMS = {"momentum_lookback": 126, "vol_window": 63, "holding_horizon": 21,
              "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": "1d"}
MOM_PARAMS = {"lookback": 63, "k": 2, "holding_horizon": 21}
OUT = ENGINE_DIR / "data_store" / "validation" / "fx_majors_stack_metrics_harvest.json"


def main() -> None:
    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    store = ParquetStore(cfg.store_path)
    out: dict = {"sleeve_a_per_pair": {}, "sleeve_c_book": {}}

    # ── Sleeve A: carry-filtered trend, per-pair single-instrument runs ──────
    for inst in MAJORS:
        df = clean(_load_history(store, adapter, inst, "2014-01-01", "2025-01-01", "1d"))
        df = df[df.index < HOLDOUT_START]
        pit = PointInTimeAccessor(df)
        strat = CarryTrendFilter(instrument=inst, **CTF_PARAMS)
        strat.fit(pit, pit.as_of(pit.end).index)
        res = Backtester(cfg, exit_mode="managed").run(pit, strat, inst, warmup=250)
        m = res.metrics
        out["sleeve_a_per_pair"][inst] = {
            "n_bars": len(df), "window": f"{df.index[0].date()} -> {df.index[-1].date()}",
            "n_trades": m.get("n_trades"), "total_return": m.get("total_return"),
            "sharpe": m.get("sharpe"), "expectancy_pnl": m.get("expectancy_pnl"),
            "expectancy_pct": m.get("expectancy_pct"), "profit_factor": m.get("profit_factor"),
            "max_drawdown": m.get("max_drawdown"), "win_rate": m.get("win_rate"),
            "net_pnl": round(sum(t.pnl for t in res.trades), 2),
            "carry_vetoes": strat.n_vetoes, "carry_signals": strat.n_signals,
        }
        print(f"A {inst}: trades={m.get('n_trades')} sharpe={m.get('sharpe'):.2f} "
              f"net={out['sleeve_a_per_pair'][inst]['net_pnl']:.0f} "
              f"vetoes={strat.n_vetoes}/{strat.n_signals}", flush=True)

    # ── Sleeve C: XS momentum majors-only book, headline config ─────────────
    panel, pits = {}, {}
    for inst in MAJORS:
        df = clean(store.load(inst, "1d"))
        df = df[df.index < HOLDOUT_START]
        panel[inst], pits[inst] = df, PointInTimeAccessor(df)
    model = CurrencyCrossSectionalMomentum(panel, **MOM_PARAMS)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes={k: "1d" for k in panel},
        warmup=250, periods_per_year=252)
    m = res.metrics
    out["sleeve_c_book"] = {"params": MOM_PARAMS, "metrics": m,
                            "per_instrument": res.per_instrument,
                            "constraint_log": res.constraint_log}
    print(f"C book: {res.summary()}", flush=True)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
