"""Pre-registered portfolio-level gate: Book E - the frozen TrendBook config on a WIDE universe.

Book D ("book_d_multiasset_252", 42 instruments) sits one DSR notch under the bar on clean
data (0.934 vs 0.95 at n=150; CPCV 14/15 positive, PBO 0.056 pass). Book E is the ONE
pre-registered breadth test (engine/data_store/book_e_prereg_2026-07-17.md): the SAME frozen
configuration - RegimeGatedMomentum + MultiTimeframeMomentum per instrument, vol 63, hold 21,
rr 1.5, rule_based regime, managed exits, vol-scaled sizing, config caps (2% per trade, 3x
gross, 1.5x corr-cluster, 6.5% portfolio risk), v5 per-asset-class costs - on a 77-instrument
universe (the existing 42 + 35 new: broad/rates/credit/commodity/sector ETFs, defensive
mega-caps, 2 more crypto). Hypothesis: same edge, more breadth -> ~1.7-2x entries, Sharpe
preserved or better; the constraint log shows whether the caps throttle the entry gain.

  * book_e_252 (headline): momentum_lookback=252  == Book D's config, universe-only change
  * book_e_126 (single variant): momentum_lookback=126  == Book C's config, universe-only change

Same three gates, same thresholds, same machinery as the multiasset gate - this is thin
orchestration over scripts/run_portfolio_gate.py (TrendBook adapter, _gate, helpers) and
run_portfolio_gate_multiasset.py (_class_breakdown, FX_MAJORS_7); per-asset-class mechanics
are exercised through PortfolioBacktester's cfg.mechanics_for() path, unchanged.

Honesty rules (same as run_portfolio_gate_multiasset.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 2 new trials are recorded in the shared TrialLedger (book_e_252, book_e_126)
    BEFORE the runs, and the ledger's full updated count deflates both DSRs.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_e.py            # full 77-instrument gate
    .venv-mac/bin/python scripts/run_portfolio_gate_book_e.py --instruments SPY,JPM,SLV,LTC/USD

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
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "portfolio_gate_book_e_2026-07-17.json"

# ── Book E universe extension (pre-registered, book_e_prereg_2026-07-17.md) ────
# Yahoo-native US tickers; equity asset class via the no-slash fallback in
# cfg.asset_class_of. Grouped by diversification intent.
WIDE_ETFS_BROAD = ["DIA", "VTI", "EFA", "EEM", "RSP", "MDY"]
WIDE_ETFS_RATES_CREDIT = ["TIP", "LQD", "HYG", "AGG"]
WIDE_ETFS_COMMODITY = ["SLV", "USO", "GSG"]
WIDE_ETFS_REAL_ESTATE = ["IYR"]
WIDE_ETFS_SECTORS = ["XLU", "XLV", "XLP", "XLI", "XLY", "XLB", "IBB", "ITA"]
WIDE_MEGACAPS = ["JPM", "JNJ", "XOM", "WMT", "PG", "KO", "V", "MA", "HD", "BA", "GS"]
WIDE_CRYPTO = ["LTC/USD", "DOT/USD"]  # generic crypto branch -> LTC-USD / DOT-USD

BOOK_E_NEW = (WIDE_ETFS_BROAD + WIDE_ETFS_RATES_CREDIT + WIDE_ETFS_COMMODITY
              + WIDE_ETFS_REAL_ESTATE + WIDE_ETFS_SECTORS + WIDE_MEGACAPS + WIDE_CRYPTO)

# ── Pre-registered configurations (the full selection set: 2 trials) ──────────
BOOKS = {
    "book_e_252": {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252},
    "book_e_126": {"carry_filter": False, **COMMON_PARAMS},
}

# Constraint families that fully BLOCK an entry vs those that only shrink it.
_VETO_FAMILIES = {"timeframe_bucket_full", "max_portfolio_risk_exceeded", "below_min_position",
                  "global_trade_cap", "drawdown_breaker", "bayesian_drawdown_breaker",
                  "drawdown_reducing_zero", "regime_zero", "invalid_stop", "no_edge"}


def _constraint_profile(constraint_log: dict) -> dict:
    """Aggregate parameterized entries into families; split vetoes vs size-scalings."""
    fam: dict[str, int] = {}
    for k, v in constraint_log.items():
        fam[k.split("=")[0]] = fam.get(k.split("=")[0], 0) + v
    vetoes = sum(v for k, v in fam.items() if k in _VETO_FAMILIES)
    return {"families": fam, "veto_events": vetoes,
            "scaling_events": sum(fam.values()) - vetoes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: Book E - frozen "
                                             "TrendBook config on the wide 77-instrument universe "
                                             "(iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: existing 42 + 35 new = 77)")
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
                   or list(cfg.data.equities) + list(cfg.data.crypto) + FX_MAJORS_7 + BOOK_E_NEW)

    panel: dict[str, pd.DataFrame] = {}
    dropped: dict[str, str] = {}
    for inst in instruments:
        df = store.load(inst, "1d")
        if df.empty:
            dropped[inst] = "no cached 1d data"
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            dropped[inst] = f"{len(df)} bars in iteration window"
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
            ledger.record({"book": name, "universe": "book_e_wide_77", "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (BOOK E, WIDE UNIVERSE) 2026-07-17 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments {n_by_class} | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    if dropped:
        print(f"dropped (pre-registered MIN_BARS={MIN_BARS} rule): {dropped}")
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
        span_days = max((res.equity.index[-1] - res.equity.index[0]).days, 1)
        freq = {"entries_per_week": round(m["n_trades"] / (span_days / 7.0), 3),
                "trades_per_year": round(m["n_trades"] / (span_days / 365.25), 1),
                "span_days": span_days}
        results[name] = {"params": params, "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "constraint_profile": _constraint_profile(res.constraint_log),
                         "frequency": freq,
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
                  f"| entries/wk={freq['entries_per_week']} ({freq['trades_per_year']}/yr)", flush=True)
            prof = results[name]["constraint_profile"]
            print(f"    constraint profile: vetoes={prof['veto_events']} scalings={prof['scaling_events']} "
                  f"| {_cap_families(res.constraint_log)}", flush=True)
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
        "dropped": dropped,
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
