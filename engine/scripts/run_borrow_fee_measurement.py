"""Measurement trial (W2, 2026-07-24): the missing short-side financing leg.

The v5 equity cost model (2.0 bps spread + 1.0 bps slippage per side) has NO
short-side borrow fee. This script measures what that omission costs the
certified book: Book H gold (book_h_gold_252, identical pre-registered params,
identical universe) re-run with and without a 50 bps/yr borrow charge on short
equity notional (``AssetClassConfig.short_borrow_bps_annual``, easy-to-borrow
large-cap assumption — IBKR easy-to-borrow large caps run ~25-75 bps/yr).

This is a COST-MODEL CORRECTION, not a strategy change and not a selection: the
selection set was fixed in book_h_prereg.md. Exactly 1 NEW trial is recorded in
the shared TrialLedger BEFORE the runs (the borrow-on measurement config; the
baseline's canonical key already exists and dedups), so the full updated count
deflates every DSR. Iteration window only: strictly < 2025-01-01. Seed 42.

The verdict question: does book_h_gold_252 still pass its gates (DSR > 0.95 at
the full ledger count, CPCV median > 0 with > 50% of 15 paths positive) once
shorts pay to be short? PBO is reported as computed across the 2-run measurement
set (near-degenerate by construction — the two runs differ only by a cost leg;
same caveat as every prior overlapping-family PBO).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_borrow_fee_measurement.py             # full measurement
    .venv-mac/bin/python scripts/run_borrow_fee_measurement.py --no-ledger # smoke mode

Exit code 0 if the borrow-on config still passes the gates, 1 otherwise.
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
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _gate,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "borrow_fee_measurement_2026-07-24.json"
RESULTS_PATH_CERTIFIED = ENGINE_DIR / "data_store" / "validation" / "borrow_fee_measurement_certified_2026-07-24.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

# The full measurement set: identical params; ONLY the equity short-borrow leg differs.
BOOKS = {
    "book_h_gold_252": {"params": GOLD_PARAMS, "equity_short_borrow_bps_annual": 0.0},
    "book_h_gold_252_borrow50bps": {"params": GOLD_PARAMS, "equity_short_borrow_bps_annual": 50.0},
}

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json, risk 1.00% era):
# the borrow-OFF config MUST reproduce these numbers when --max-risk-per-trade 0.01.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}


def _cfg_with_borrow(bps: float, max_risk_per_trade: float | None = None):
    cfg = copy.deepcopy(get_config())
    cfg.asset_classes.equity.short_borrow_bps_annual = bps
    if max_risk_per_trade is not None:
        cfg.risk.max_risk_per_trade = max_risk_per_trade
    return cfg


def _short_equity_stats(res, cfg) -> dict:
    eq_insts = {inst for inst in res.instruments if cfg.asset_class_of(inst) == "equity"}
    shorts = [tr for tr in res.trades if tr.direction == "short" and tr.instrument in eq_insts]
    return {
        "n_short_equity_trades": len(shorts),
        "short_equity_net_pnl": round(sum(tr.pnl for tr in shorts), 2),
        "short_borrow_fees_total": res.metrics.get("short_borrow_fees_total", 0.0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measurement trial: short-borrow fee on Book H gold "
                                             "(iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--max-risk-per-trade", type=float, default=None,
                    help="override cfg.risk.max_risk_per_trade for BOTH configs — use 0.01 to "
                         "reproduce the certified 2026-07-22 gap-aware anchor (the current "
                         "config.yaml has drifted to 0.0075 by owner decision 2026-07-23)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record the trial; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
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
    # NOT load order — run_portfolio_gate_book_h.py builds per-book panels as
    # `universe + crypto + FX` out of a sorted master, and the certified numbers are
    # ordering-sensitive (ordering_sensitivity_audit.md). Reproduce that exactly.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio run")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the measurement trials BEFORE running (exact keys dedup on re-runs).
    mrpt = (args.max_risk_per_trade if args.max_risk_per_trade is not None
            else base_cfg.risk.max_risk_per_trade)
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "cost_model_measurement",
                           "max_risk_per_trade": mrpt,
                           "params": {**spec["params"],
                                      "equity_short_borrow_bps_annual": spec["equity_short_borrow_bps_annual"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"BORROW-FEE MEASUREMENT (BOOK H GOLD) 2026-07-24 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)} | max_risk_per_trade={mrpt}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg_with_borrow(spec["equity_short_borrow_bps_annual"], args.max_risk_per_trade)
        t_start = time.time()
        model = TrendBook(panel, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"],
                                    "equity_short_borrow_bps_annual": spec["equity_short_borrow_bps_annual"]},
                         "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "short_equity": _short_equity_stats(res, cfg),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade ({m['expectancy_pct']*100:.3f}%/trade) "
              f"profit_factor={m.get('profit_factor')} win_rate={m['win_rate']*100:.1f}% "
              f"maxDD={m['max_drawdown']*100:.1f}% | shorts: {results[name]['short_equity']}", flush=True)

    # Deltas (borrow-on minus baseline)
    a, b = results["book_h_gold_252"]["metrics"], results["book_h_gold_252_borrow50bps"]["metrics"]
    deltas = {k: round(b[k] - a[k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "expectancy_pct", "win_rate",
               "max_drawdown", "total_return", "ann_return")}

    # PBO across the 2-run measurement set (reported as computed; near-degenerate by construction).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} measurement runs: {pbo}", flush=True)

    # CPCV per config (the same 15 paths as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg_with_borrow(spec["equity_short_borrow_bps_annual"], args.max_risk_per_trade)
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  deltas (borrow50 - baseline): {deltas}")
    print("=" * 72, flush=True)

    # Certified-anchor reproduction check: borrow OFF at risk 1.00% must reproduce
    # book_h_gapaware_2026-07-22.json (gold) — hard-fail the run if it does not.
    if args.max_risk_per_trade is not None and not args.instruments:
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
        "kind": "cost_model_measurement",
        "max_risk_per_trade": mrpt,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "deltas_borrow_minus_baseline": deltas,
        "books": results,
        "verdicts": verdicts,
    }
    results_path = RESULTS_PATH_CERTIFIED if args.max_risk_per_trade is not None else RESULTS_PATH
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if verdicts["book_h_gold_252_borrow50bps"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
