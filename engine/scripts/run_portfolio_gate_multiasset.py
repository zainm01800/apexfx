"""Pre-registered portfolio-level gate: the DIVERSIFIED MULTI-ASSET trend book.

The FX-only diversified book was rejected at this gate today
(engine/data_store/portfolio_gate_2026-07-17.md: 0/15 CPCV paths positive - 22 FX
pairs are ~8 correlated currency factors at 1-10 pip round-trip costs). The
literature the hypothesis rests on (docs/research/2026-07-17_fx_edges_evidence.md:
Hurst/Ooi/Pedersen; Moskowitz/Ooi/Pedersen) is explicit that the trend edge lives
in a book diversified ACROSS asset classes - equities, bonds, commodities proxies,
currencies; the AQR century study runs 67 markets. This script gives that claim
its one pre-registered shot with the universes the engine already carries:

  * Book C ("multi-asset trend 126"): 24 equities/ETFs (SPY/QQQ/IWM/GLD/TLT/XLE/
    XLF ... equity/bond/gold/sector exposure) + 12 crypto + the 7 FX majors
    (cheapest per-pair v5 costs), daily bars, momentum_lookback=126, vol 63,
    hold 21, rr 1.5, rule_based regime, managed exits, vol-scaled sizing,
    config risk caps binding.
  * Book D ("multi-asset trend 252"): identical but momentum_lookback=252.

Same three gates, same thresholds, same machinery as the FX gate - this is thin
orchestration over scripts/run_portfolio_gate.py (TrendBook adapter, _gate,
helpers); per-asset-class mechanics (forex pips vs equity/crypto bps costs, 252
vs 365 vol annualization) are exercised through PortfolioBacktester's
cfg.mechanics_for() path, unchanged. MATIC/USD has no cached 1d data and drops
out via the standard MIN_BARS skip (43 -> 42 instruments).

Honesty rules (same as run_portfolio_gate.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 2 new trials are recorded in the shared TrialLedger (books C and D)
    BEFORE the runs, and the ledger's full updated count deflates both DSRs.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_multiasset.py            # full 43-instrument gate
    .venv-mac/bin/python scripts/run_portfolio_gate_multiasset.py --instruments SPY,BTC/USD,USD/JPY

Exit code 0 if both books pass, 1 otherwise.
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
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _cap_families,
    _gate,
    _max_gross_leverage,
    _utc,
)

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "portfolio_gate_multiasset_2026-07-17.json"

# Cheapest-cost FX per config v5 pair_rt_cost_pips; bonds/gold/sectors ride along
# via the ETF sleeve (TLT/GLD/XLE/XLF/...). Universe label recorded in the ledger.
FX_MAJORS_7 = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]

# ── Pre-registered configurations (the full selection set: 2 trials) ──────────
BOOKS = {
    "book_c_multiasset_126": {"carry_filter": False, **COMMON_PARAMS},
    "book_d_multiasset_252": {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252},
}


def _class_breakdown(per_instrument: dict, cfg) -> dict:
    """Net P&L / trade count aggregated per asset class (is FX still the bleeder?)."""
    out: dict[str, dict] = {}
    for inst, d in per_instrument.items():
        cls = cfg.asset_class_of(inst)
        agg = out.setdefault(cls, {"instruments": [], "n_trades": 0, "net_pnl": 0.0})
        agg["instruments"].append(inst)
        agg["n_trades"] += d["n_trades"]
        agg["net_pnl"] += d["net_pnl"]
    for agg in out.values():
        agg["net_pnl"] = round(agg["net_pnl"], 2)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: diversified multi-asset "
                                             "trend book, lookback 126 vs 252 (iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: 24 equities + 12 crypto + 7 FX majors)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + 2)")
    args = ap.parse_args(argv)

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

    # Record the 2 pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in BOOKS.items():
            ledger.record({"book": name, "universe": "multiasset_43", "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (MULTI-ASSET) 2026-07-17 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments {n_by_class} | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"books: {list(BOOKS)} | ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per book -> returns (DSR/PBO) + trade metrics, one shared
    #    equity curve with config risk caps binding.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, params in BOOKS.items():
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
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
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

    # 2. PBO across the two books - the whole pre-registered selection set.
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} books: {pbo}", flush=True)

    # 3. CPCV OOS distribution per book (the same 15 paths as the single-pair gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, params in BOOKS.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
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
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "universe": list(panel.keys()),
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
