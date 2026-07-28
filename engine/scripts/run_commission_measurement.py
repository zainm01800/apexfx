"""Measurement trial (2026-07-28): the missing equity commission leg.

The v5 equity cost model charges 2.0 bps spread + 1.0 bps slippage per side on
fills and ``commission_per_trade = 0.0`` (engine/config.yaml asset_classes.equity).
The IBKR paper mirror (account DUQ278370, fills of 2026-07-17/21 in
data_store/ibkr_mirror/) measured the REAL commissions on this book's equity
orders: mean **$1.0902 per order** over the 6 filled equity orders (per-order
$1.00-$1.21; mean 1.43 bps/side on $4.9k-$14.7k notionals — IBKR's $1 minimum
dominates at the book's order sizes, so the honest shape is a FLAT per-order
fee, not a bps rate).

This script measures what that omission costs the certified book: Book H gold
(book_h_gold_252 — identical pre-registered params, identical universe,
identical certified panel insertion order, EQUITY_CORE first) re-run with and
without a 1.09 flat commission per side on equity trades
(``AssetClassConfig.commission_per_trade``, charged by PortfolioBacktester at
entry and at every close/trim — i.e. once per ORDER, exactly the IBKR
structure; a round trip pays 2 x 1.09).

This is a COST-MODEL CORRECTION measurement, not a strategy change and not a
selection: the selection set was fixed in book_h_prereg.md. Exactly 1 NEW trial
is recorded in the shared TrialLedger BEFORE the runs (kind
"commission_measurement"; the baseline's canonical key already exists in the
ledger and dedups). Iteration window only: strictly < 2025-01-01. Seed 42.

THE FLAG: the commission override lives ONLY in this harness — a deepcopy of
the live config with ``asset_classes.equity.commission_per_trade`` set.
engine/config.yaml is NOT touched: the frozen Book D paper test's cost model
and the funded runner's live config keep commission 0.0.

Certified-anchor reproduction (book_h_gapaware_2026-07-22.json, risk 1.00%
era): the commission-OFF config MUST reproduce those numbers when run at
--max-risk-per-trade 0.01 (the default); the run hard-fails otherwise.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_commission_measurement.py     # certified-anchor measurement
    .venv-mac/bin/python scripts/run_commission_measurement.py \
        --no-ledger --out data_store/validation/commission_measurement_2026-07-28_twin.json

Exit code 0 on success, 1 if the certified anchor does not reproduce.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.metrics import sharpe_ratio  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "commission_measurement_2026-07-28.json"

#: Measured on the IBKR paper mirror (DUQ278370), fills of 2026-07-17/21:
#: 6 equity orders, mean $1.0902/order ($1.00-$1.21), USD. Charged per SIDE
#: (entry and each close/trim), exactly how PortfolioBacktester applies
#: commission_per_trade. Kept unconverted (USD ≈ account unit): at GBPUSD ~1.3
#: the sterling-honest figure would be ~£0.84/side, so 1.09 is mildly
#: CONSERVATIVE (overstates the cost by ~25-30%).
COMMISSION_PER_SIDE = 1.09

MIRROR_SAMPLE = {
    "source": "engine/data_store/ibkr_mirror/2026-07-16.json, 2026-07-17.json, 2026-07-21.json",
    "account": "DUQ278370 (IBKR paper)",
    "n_equity_orders": 6,
    "mean_commission_usd_per_order": 1.0902,
    "per_order_usd": [1.0001, 1.0000, 1.2076, 1.1411, 1.1920, 1.0001],
    "mean_bps_per_side": 1.43,
    "note": "IBKR $1/order minimum dominates at the book's ~$5-15k order sizes "
            "-> flat per-order fee is the honest shape, not a bps rate.",
}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01     # the certified 2026-07-22 gap-aware anchor is the risk-1.00% era

# The full measurement set: identical params; ONLY the equity commission leg differs.
BOOKS = {
    "book_h_gold_252": {"params": GOLD_PARAMS, "equity_commission_per_trade": 0.0},
    "book_h_gold_252_commission109": {"params": GOLD_PARAMS,
                                      "equity_commission_per_trade": COMMISSION_PER_SIDE},
}

#: The ONE new trial this measurement adds to the shared ledger (recorded BEFORE
#: the runs; the baseline's canonical key already exists and dedups). Module-level
#: so the ledger-first commit step records the byte-identical key.
COMMISSION_TRIAL = {
    "book": "book_h_gold_252_commission109",
    "universe": "book_h_gold_39",
    "timeframe": "1d",
    "factory": "trend_book_mtf",
    "kind": "commission_measurement",
    "max_risk_per_trade": CERTIFIED_MRPT,
    "params": {**GOLD_PARAMS, "equity_commission_per_trade": COMMISSION_PER_SIDE},
}

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json, risk 1.00% era):
# the commission-OFF config MUST reproduce these numbers at --max-risk-per-trade 0.01.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}


def _cfg_with_commission(per_side: float, max_risk_per_trade: float):
    """THE FLAG: harness-local deepcopy override — config.yaml stays untouched."""
    cfg = copy.deepcopy(get_config())
    cfg.asset_classes.equity.commission_per_trade = per_side
    cfg.risk.max_risk_per_trade = max_risk_per_trade
    return cfg


def _monthly_tail_stats(res, initial_equity: float = 100000.0) -> dict:
    """In-window monthly-profit figures on the 100k book (same convention as the
    order-invariant / cf-cvar / defensive-sleeve gates: month-end equity diffs)."""
    eq = res.equity
    if eq.empty:
        return {}
    month_last = eq.groupby(eq.index.to_period("M")).last()
    prev = pd.Series([initial_equity, *month_last.iloc[:-1]], index=month_last.index)
    monthly_pnl = month_last - prev
    return {
        "n_months": int(len(monthly_pnl)),
        "avg_monthly_pnl": round(float(monthly_pnl.mean()), 2),
        "median_monthly_pnl": round(float(monthly_pnl.median()), 2),
        "worst_month_pnl": round(float(monthly_pnl.min()), 2),
    }


def _commission_stats(res, cfg, per_side: float) -> dict:
    eq_insts = {inst for inst in res.instruments if cfg.asset_class_of(inst) == "equity"}
    eq_trades = [tr for tr in res.trades if tr.instrument in eq_insts]
    return {
        "n_equity_trades": len(eq_trades),
        "equity_net_pnl": round(sum(tr.pnl for tr in eq_trades), 2),
        # Lower bound only: entry + final close per trade. Managed-exit partials
        # and gamma trims each pay one more per-side fee and are not separable
        # from the outside; the realised drag (net_pnl delta vs baseline,
        # reported in the output) is the all-in figure.
        "commission_floor": round(2 * per_side * len(eq_trades), 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measurement trial: equity commission on Book H gold "
                                             "(iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--max-risk-per-trade", type=float, default=CERTIFIED_MRPT,
                    help="default 0.01 reproduces the certified 2026-07-22 gap-aware anchor "
                         "(config.yaml has drifted to 0.0075 by owner decision 2026-07-23)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke/twin mode: do NOT record the trial")
    ap.add_argument("--out", default=str(RESULTS_PATH), help="results JSON path")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        if subset and inst not in subset:
            continue
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first),
    # NOT load order — the certified numbers are ordering-sensitive
    # (ordering_sensitivity_audit.md). Reproduce that exactly.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio run")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the measurement trial BEFORE running (exact key dedups on re-runs).
    mrpt = args.max_risk_per_trade
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        ledger.record(COMMISSION_TRIAL)
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + 1

    print("=" * 72, flush=True)
    print(f"COMMISSION MEASUREMENT (BOOK H GOLD) 2026-07-28 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)} | max_risk_per_trade={mrpt}")
    print(f"commission per side (equity only): {COMMISSION_PER_SIDE} account units/order "
          f"(IBKR mirror mean $1.0902, 6 orders)")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | run uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        per_side = spec["equity_commission_per_trade"]
        cfg = _cfg_with_commission(per_side, mrpt)
        t_start = time.time()
        model = TrendBook(panel, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        m = res.metrics
        results[name] = {
            "params": {**spec["params"], "equity_commission_per_trade": per_side},
            "metrics": m,
            "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
            "equity_commission": _commission_stats(res, cfg, per_side),
            "constraint_log": res.constraint_log,
            "per_instrument": res.per_instrument,
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
        }
        mt = results[name]["monthly_tail"]
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade ({m['expectancy_pct']*100:.3f}%/trade) "
              f"profit_factor={m.get('profit_factor')} win_rate={m['win_rate']*100:.1f}% "
              f"maxDD={m['max_drawdown']*100:.1f}% | avg {mt['avg_monthly_pnl']:+,.0f}/mo over "
              f"{mt['n_months']}m | {results[name]['equity_commission']}", flush=True)

    # Deltas (commission-on minus baseline)
    a, b = results["book_h_gold_252"]["metrics"], results["book_h_gold_252_commission109"]["metrics"]
    deltas = {k: round(b[k] - a[k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "expectancy_pct", "win_rate",
               "max_drawdown", "total_return", "final_equity", "net_pnl")}
    deltas["avg_monthly_pnl"] = round(
        results["book_h_gold_252_commission109"]["monthly_tail"]["avg_monthly_pnl"]
        - results["book_h_gold_252"]["monthly_tail"]["avg_monthly_pnl"], 2)
    print(f"\ndeltas (commission109 - baseline): {deltas}", flush=True)

    # Certified-anchor reproduction check: commission OFF at risk 1.00% must
    # reproduce book_h_gapaware_2026-07-22.json (gold) — hard-fail if it does not.
    if mrpt == CERTIFIED_MRPT and not args.instruments:
        m0 = results["book_h_gold_252"]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",) else
                                          1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "kind": "commission_measurement",
        "max_risk_per_trade": mrpt,
        "commission_per_side": COMMISSION_PER_SIDE,
        "mirror_sample": MIRROR_SAMPLE,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "deltas_commission_minus_baseline": deltas,
        "books": results,
    }
    results_path = Path(args.out)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
