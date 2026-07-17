"""Pre-registered follow-up: the multi-asset trend book over a 12-config grid.

The 2-config multi-asset gate (engine/data_store/portfolio_gate_multiasset_2026-07-17.md)
REJECTED both books on PBO alone (0.8115): DSR passed decisively (0.995/0.996,
deflated by 108 trials) and CPCV passed (13/15 and 15/15 positive OOS paths).
With exactly 2 near-identical configs PBO only asks "does the IS-better book stay
better OOS?" - coarse by construction. The pre-committed honest next step is to
re-run with a PBO-meaningful selection set, so this script runs the SAME gate
(same universe, same thresholds, same machinery) over a 12-config grid:

    momentum_lookback in {63, 126, 189, 252}  x  holding_horizon in {10, 15, 21}
    (reward_risk 1.5, vol_window 63, rule_based regime, HTF 1w x 50 gate,
    managed exits, vol-scaled sizing, config risk caps binding)

Universe unchanged: 24 equities/ETFs + 12 crypto + the 7 FX majors (MATIC/USD
drops out via the standard MIN_BARS skip -> 42 instruments). The (126, 21) cell
is parameter-identical to Book C of the 2-config run and must reproduce it
exactly - that is the determinism check.

Gate (identical to validation/portfolio_report.run_portfolio_validation):
  DSR > 0.95 (deflated by the shared TrialLedger's FULL count), PBO < 0.5
  across the TWELVE books (the whole pre-registered selection set), CPCV median
  OOS Sharpe > 0 with > 50% of 15 paths positive. CPCV purge = each config's own
  holding horizon, as in the single-instrument gate.

Honesty rules (same as run_portfolio_gate.py / run_portfolio_gate_multiasset.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 12 new trials are recorded in the shared TrialLedger BEFORE the
    runs, and the ledger's full updated count (108 -> 120) deflates every DSR.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_multiasset_grid.py            # full 12-config gate
    .venv-mac/bin/python scripts/run_portfolio_gate_multiasset_grid.py --configs ma_grid_l126_h21,ma_grid_l063_h10

Exit code 0 if all configs pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
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
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _cap_families,
    _gate,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "portfolio_gate_multiasset_grid_2026-07-17.json"

# ── Pre-registered selection set (exactly 12 trials) ──────────────────────────
# Ordered lookback-major, holding_horizon inner. The (126, 21) cell - Book C's
# parameters - is grid #6 (1-based; #5 0-indexed).
LOOKBACKS = (63, 126, 189, 252)
HORIZONS = (10, 15, 21)
BOOKS = {
    f"ma_grid_l{lb}_h{hh}": {"carry_filter": False, **COMMON_PARAMS,
                             "momentum_lookback": lb, "holding_horizon": hh}
    for lb in LOOKBACKS for hh in HORIZONS
}
HEADLINE = "ma_grid_l126_h21"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: multi-asset trend book, "
                                             "12-config lookback x horizon grid (iteration window only).")
    ap.add_argument("--configs", default="",
                    help="comma-separated subset of grid names (default: all 12)")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: 24 equities + 12 crypto + 7 FX majors)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + n_selected)")
    args = ap.parse_args(argv)

    books = BOOKS
    if args.configs:
        keep = [s.strip() for s in args.configs.split(",") if s.strip()]
        unknown = [k for k in keep if k not in BOOKS]
        if unknown:
            print(f"unknown configs: {unknown} (choices: {list(BOOKS)})")
            return 1
        books = {k: BOOKS[k] for k in keep}

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    instruments = ([s.strip() for s in args.instruments.split(",") if s.strip()]
                   or list(cfg.data.equities) + list(cfg.data.crypto) + FX_MAJORS_7)

    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        panel[inst] = df
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    n_by_class = {}
    for inst in panel:
        cls = cfg.asset_class_of(inst)
        n_by_class[cls] = n_by_class.get(cls, 0) + 1

    # Record the pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in books.items():
            ledger.record({"book": name, "universe": "multiasset_43", "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(books)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (MULTI-ASSET 12-CONFIG GRID) 2026-07-17 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments {n_by_class} | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"configs: {len(books)} {list(books)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config -> returns (DSR/PBO) + trade metrics, one shared
    #    equity curve with config risk caps binding.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, params in books.items():
        t_start = time.time()
        model = TrendBook(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": params, "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "per_asset_class": _class_breakdown(res.per_instrument, cfg),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        tag = " [HEADLINE]" if name == HEADLINE else ""
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}{tag}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% "
                  f"maxDD={m['max_drawdown']*100:.1f}% lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)
            for cls, agg in sorted(results[name]["per_asset_class"].items()):
                print(f"    {cls:7s}: {agg['n_trades']:5d} trades, net {agg['net_pnl']:+12.2f} "
                      f"across {len(agg['instruments'])} instruments", flush=True)

    # 2. PBO across the whole pre-registered selection set (12 configs).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV OOS distribution per config (15 paths; purge = the config's own horizon).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in books]
    verdicts: dict[str, dict] = {}
    for name, params in books.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=params["holding_horizon"],
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        tag = " [HEADLINE]" if name == HEADLINE else ""
        print(f"  {name}{tag}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "universe": list(panel.keys()),
        "grid": {"momentum_lookback": list(LOOKBACKS), "holding_horizon": list(HORIZONS)},
        "headline": HEADLINE,
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if all(v["passed"] for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
