"""Candidate validation loop: improve -> blind backtest -> repeat, honestly.

Thin orchestration over the existing machinery (run_validation + CPCV/DSR/PBO,
TrialLedger, PortfolioBacktester). No new math lives here.

Honesty rules enforced by this script:
  * ITERATION window is strictly BEFORE --holdout-start (default 2025-01-01).
    The 2025+ holdout is only ever touched by an explicit --final run.
  * Every distinct config evaluated is recorded in a persistent TrialLedger and
    the DSR is deflated by the ledger's FULL trial count - not just this run's
    grid - so repeated tinkering makes the bar higher, not the p-value lower.
  * Every --final run appends to holdout_looks.log. If that log already has
    more than a few looks, the "holdout" is no longer blind and this script
    says so loudly.

Usage:
    cd engine
    # iterate (never touches 2025+ data)
    .venv-mac/bin/python scripts/run_candidate_check.py \
        --instrument EUR/USD --label mom63-candidate \
        --params '{"momentum_lookback":63,"vol_window":63,"holding_horizon":10,"reward_risk":1.5,"regime_method":"rule_based"}' \
        --params '{"momentum_lookback":126,"vol_window":63,"holding_horizon":10,"reward_risk":1.5,"regime_method":"rule_based"}'

    # iterate + book-level check if >=2 instruments pass the gate
    .venv-mac/bin/python scripts/run_candidate_check.py \
        --instrument EUR/USD --instrument GBP/USD --label mom63-book \
        --params '{"momentum_lookback":63,"vol_window":63}' --portfolio-check

    # final confirmation on the 2025+ holdout (LOGGED - each look costs blindness)
    .venv-mac/bin/python scripts/run_candidate_check.py \
        --instrument EUR/USD --label mom63-final --final \
        --params '{"momentum_lookback":63,"vol_window":63}'

Exit code 0 if every instrument passes the gate, 1 if any is rejected.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:  # scripts in this repo load engine/.env so Supabase/Oanda creds are present
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:  # pragma: no cover - dotenv is a repo dep; degrade gracefully
    pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean, get_adapter, normalize_day_bars  # noqa: E402
from apex_quant.storage import post_backtest  # noqa: E402
from apex_quant.validation.report import (  # noqa: E402
    PBO_THRESHOLD,
    DSR_THRESHOLD,
    default_factory,
    meta_factory,
    ml_factory,
    run_validation,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
HOLDOUT_LOG = ENGINE_DIR / "data_store" / "validation" / "holdout_looks.log"
HOLDOUT_LOOK_WARN = 5          # more prior looks than this => holdout is going stale
DEFAULT_START = "2014-01-01"   # matches scripts/run_backtests.py
DEFAULT_HOLDOUT_START = "2025-01-01"
MIN_BARS = 300                 # same floor as scripts/run_backtests.py

def _carry_trend_factory(**params):
    from apex_quant.strategies.carry_trend_filter import CarryTrendFilter
    return CarryTrendFilter(**params)


FACTORIES = {"default": default_factory, "ml": ml_factory,
             "meta": meta_factory, "carry_trend": _carry_trend_factory}


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _daily_dedup(df: pd.DataFrame, inst: str) -> pd.DataFrame:
    """Collapse rows sharing a calendar date (keep last). Safety net only: the
    store was migrated 2026-07-17 to a single day-bar convention (00:00 UTC —
    see ``normalize_day_bars`` in data/store.py and scripts/dedup_store_day_bars.py),
    so this should now find nothing; it stays in case a frame bypasses the
    store's write path. Single-convention frames are unaffected."""
    dup = df.index.normalize().duplicated(keep="last")
    if dup.any():
        print(f"  note: {inst}: collapsed {int(dup.sum())} duplicate-calendar-date row(s) "
              f"(mixed day-boundary conventions in the store)")
        df = df[~dup]
    return df


def _load_history(store: ParquetStore, adapter, inst: str, start: str, end: str,
                  timeframe: str) -> pd.DataFrame:
    """OHLCV covering [start, end]. Cache-first; fill gaps via the normal adapter
    layer in memory only (never write merged frames back to the parquet store -
    other processes read those files concurrently). Fresh adapter output is
    normalised to the store's day-bar convention before merging (see
    ``normalize_day_bars``) so raw OANDA 21:00/22:00 bars cannot re-introduce
    the duplicate-calendar-date contamination the store migration removed."""
    cached = store.load(inst, timeframe)
    if not cached.empty and cached.index[0] <= _utc(start) and cached.index[-1] >= _utc(end):
        return _daily_dedup(cached, inst)
    try:
        fetched = adapter.get_history(inst, start, end, timeframe)
        merged = pd.concat([cached, fetched])
        merged = normalize_day_bars(merged, timeframe)
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        return _daily_dedup(merged, inst)
    except Exception as e:  # noqa: BLE001 - offline/creds: degrade to the cache
        if cached.empty:
            raise
        print(f"  note: adapter fetch failed ({type(e).__name__}: {e}); "
              f"using cached {cached.index[0].date()} -> {cached.index[-1].date()}")
        return _daily_dedup(cached, inst)


def _parse_grid(raw: list[str]) -> list[dict]:
    grid = []
    for r in raw:
        try:
            d = json.loads(r)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--params is not valid JSON: {e}\n  got: {r}")
        if not isinstance(d, dict):
            raise SystemExit(f"--params must be a JSON object, got: {type(d).__name__}")
        grid.append(d)
    return grid


def _print_gate(rep) -> bool:
    v = rep.verdict
    dsr_val = rep.dsr.get("dsr", 0.0)
    pbo_val = rep.pbo.get("pbo")
    med_oos = rep.cpcv.get("oos_sharpe_median", 0.0)
    frac_pos = rep.cpcv.get("frac_positive", 0.0)
    n_paths = rep.cpcv.get("n_paths", 0)
    tag = lambda ok: "pass" if ok else "FAIL"
    print(f"  VERDICT: {'PASS' if v['passed'] else 'REJECT'}")
    print(f"    [{tag(v['dsr_pass'])}] DSR {dsr_val:.3f} vs > {DSR_THRESHOLD} "
          f"(deflated by n_trials={rep.dsr.get('n_trials')})")
    print(f"    [{tag(v['pbo_pass'])}] PBO {pbo_val if pbo_val is not None else 'n/a'} "
          f"vs < {PBO_THRESHOLD} (n/a with <2 configs => gate fails closed)")
    print(f"    [{tag(v['cpcv_pass'])}] CPCV median OOS Sharpe {med_oos:.3f} vs > 0; "
          f"{frac_pos*100:.0f}% of {n_paths} paths positive vs > 50%")
    return v["passed"]


def _save_results(service: EngineService, rep, label: str, timeframe: str) -> None:
    """Persist like run_backtests.py does; a Supabase outage must not kill the run."""
    d = rep.model_dump()
    try:
        path = service.save_validation(d, rep.strategy, rep.instrument)
        print(f"  saved local cache: {path.name}")
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: local save failed: {type(e).__name__}: {e}")
    try:
        posted = post_backtest(d, config_label=label, timeframe=timeframe)
        print(f"  supabase post_backtest[{label}]: {'ok' if posted else 'rejected by API'}")
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: supabase unreachable ({type(e).__name__}: {e}); continuing")


def _log_holdout_look(inst: str, label: str, passed: bool) -> None:
    HOLDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    verdict = "PASS" if passed else "REJECT"
    with open(HOLDOUT_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{ts},{inst},{label.replace(',', ';')},{verdict}\n")


def _prior_holdout_looks() -> int:
    if not HOLDOUT_LOG.exists():
        return 0
    return sum(1 for line in HOLDOUT_LOG.read_text(encoding="utf-8").splitlines() if line.strip())


def _max_gross_leverage(res) -> float:
    """Approx peak gross leverage (sum of |notional| of overlapping trades / equity),
    reconstructed from the trade list. Quote-currency conversion is ignored, so
    treat as an approximation - the constraint_log is the authoritative cap record."""
    eq = res.equity
    if eq.empty or not res.trades:
        return 0.0
    gross = pd.Series(0.0, index=eq.index)
    for tr in res.trades:
        t0, t1 = _utc(tr.entry_time), _utc(tr.exit_time)
        gross[(gross.index >= t0) & (gross.index < t1)] += abs(tr.entry_price * tr.units)
    lev = (gross / eq.replace(0.0, np.nan)).max()
    return float(lev) if np.isfinite(lev) else 0.0


def _portfolio_check(passed: list[tuple[str, dict]], pits: dict, factory,
                     timeframe: str, cfg) -> None:
    """Run the gate-PASSING headline configs together on one shared equity curve so
    the book-level risk caps from config.yaml actually bind (PortfolioBacktester)."""
    if len(passed) < 2:
        print(f"\nportfolio-check: skipped - need >=2 gate-PASS instruments, have {len(passed)}")
        return
    from apex_quant.backtest.portfolio import PortfolioBacktester

    print(f"\nportfolio-check: {len(passed)} passing instruments on ONE shared book "
          f"(iteration window, config.yaml risk caps binding)")
    sub_pits, strats = {}, {}
    for inst, params in passed:
        pit = pits[inst]
        strat = factory(**params)
        strat.fit(pit, pit.as_of(pit.end).index)
        sub_pits[inst], strats[inst] = pit, strat
    res = PortfolioBacktester(cfg).run(
        sub_pits, strats, timeframes={inst: timeframe for inst in sub_pits}
    )
    m = res.metrics
    if m.get("insufficient_data"):
        print(f"  portfolio: insufficient data ({m.get('n_trades', 0)} trades)")
        return
    # aggregate parameterized entries (e.g. "regime_scale=0.13") into their family
    fam: dict[str, int] = {}
    for k, v in res.constraint_log.items():
        fam[k.split("=")[0]] = fam.get(k.split("=")[0], 0) + v
    caps = ", ".join(f"{k}x{v}" for k, v in sorted(fam.items())) or "none"
    print(f"  trades={m['n_trades']}  total_return={m['total_return']*100:.1f}%  "
          f"sharpe={m['sharpe']:.2f}")
    print(f"  expectancy={m['expectancy_pnl']:.2f} pnl/trade ({m['expectancy_pct']*100:.3f}%/trade)  "
          f"profit_factor={m.get('profit_factor')}  max_drawdown={m['max_drawdown']*100:.1f}%")
    print(f"  max gross leverage ~{_max_gross_leverage(res):.2f}x (approx, see docstring)")
    print(f"  risk caps bound: {caps}")
    for inst, stats in sorted(res.per_instrument.items()):
        print(f"    {inst}: {stats['n_trades']} trades, net {stats['net_pnl']:.0f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Honest candidate validation: iteration window only, ledger-deflated DSR, "
                    "logged holdout looks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  python scripts/run_candidate_check.py --instrument EUR/USD --label mom63 \\\n"
               "    --params '{\"momentum_lookback\":63,\"vol_window\":63}'\n",
    )
    ap.add_argument("--instrument", action="append", required=True,
                    help="repeatable, e.g. --instrument EUR/USD --instrument GBP/USD")
    ap.add_argument("--label", required=True, help="candidate label (Supabase config_label)")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--params", action="append", required=True,
                    help="JSON dict of strategy params; repeat for the multiple-testing grid "
                         "(first is the headline config)")
    ap.add_argument("--factory", choices=sorted(FACTORIES), default="default",
                    help="strategy factory, mirroring run_backtests.py (default=RegimeGatedMomentum)")
    ap.add_argument("--start", default=DEFAULT_START,
                    help=f"window start where data allows (default {DEFAULT_START})")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--final", action="store_true",
                    help="run on the holdout window [--holdout-start, latest] instead; every look is logged")
    ap.add_argument("--portfolio-check", dest="portfolio_check", action="store_true",
                    help="after >=2 gate-PASSes, run the passing configs together through "
                         "PortfolioBacktester on the iteration window")
    args = ap.parse_args(argv)

    grid = _parse_grid(args.params)
    factory = FACTORIES[args.factory]
    if args.factory in ("default", "carry_trend"):
        # data timeframe and strategy timeframe must agree; explicit param wins
        grid = [{"timeframe": args.timeframe, **p} for p in grid]

    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    store = ParquetStore(cfg.store_path)
    service = EngineService(cfg)
    ledger = TrialLedger.load(LEDGER_PATH)

    holdout_start = _utc(args.holdout_start)
    mode = "HOLDOUT (--final)" if args.final else "ITERATION"
    fetch_end = str(pd.Timestamp.utcnow().date()) if args.final else args.holdout_start

    print("=" * 72)
    print(f"CANDIDATE CHECK '{args.label}' | mode={mode} | factory={args.factory} "
          f"| timeframe={args.timeframe} | grid={len(grid)} config(s)")
    print(f"window: {args.start} -> {'latest' if args.final else f'<{args.holdout_start} (strict)'}")
    print(f"ledger: {LEDGER_PATH} (n_trials={ledger.n_trials} before this run)")
    if args.final:
        prior = _prior_holdout_looks()
        if prior > HOLDOUT_LOOK_WARN:
            print("!" * 72)
            print(f"!! WARNING: {HOLDOUT_LOG.name} already has {prior} prior looks (> {HOLDOUT_LOOK_WARN}).")
            print("!! The holdout is going stale - treat any PASS below with suspicion.")
            print("!" * 72)
        print(f"holdout look #{prior + 1} will be logged to {HOLDOUT_LOG}")
    print("=" * 72)

    pits: dict[str, PointInTimeAccessor] = {}
    passed_cfgs: list[tuple[str, dict]] = []
    n_reject = n_error = 0

    for inst in args.instrument:
        klass = cfg.asset_class_of(inst)
        try:
            df = clean(_load_history(store, adapter, inst, args.start, fetch_end, args.timeframe))
            df = df[df.index >= holdout_start] if args.final else df[df.index < holdout_start]
            if len(df) < MIN_BARS:
                print(f"skip {inst}: {len(df)} bars in window"); n_error += 1; continue
            pit = PointInTimeAccessor(df)
        except Exception as e:  # noqa: BLE001
            print(f"skip {inst}: {type(e).__name__}: {e}"); n_error += 1; continue
        print(f"\n[{klass}] {inst}: {len(df)} bars ({pit.start.date()} -> {pit.end.date()})")

        # record this run's configs BEFORE validating, so this run's own trials
        # count toward the deflation denominator; identical configs never
        # double-count (canonical-JSON dedup inside TrialLedger).
        for params in grid:
            ledger.record({"instrument": inst, "timeframe": args.timeframe,
                           "factory": args.factory, "params": params})
        n_trials = ledger.n_trials
        ledger.save(LEDGER_PATH)

        try:
            rep = run_validation(pit, inst, strategy_factory=factory, param_grid=grid,
                                 cfg=cfg, generated_for=str(pit.end.date()), n_trials=n_trials)
        except Exception as e:  # noqa: BLE001
            print(f"  {inst} {args.label}: ERROR {type(e).__name__}: {e}")
            n_error += 1
            continue

        print(f"  ledger n_trials={n_trials} used for DSR deflation "
              f"(grid contributes {len(grid)})")
        ok = _print_gate(rep)
        _save_results(service, rep, args.label, args.timeframe)
        if args.final:
            _log_holdout_look(inst, args.label, ok)
        if ok:
            passed_cfgs.append((inst, grid[0]))
        else:
            n_reject += 1
        pits[inst] = pit

    if args.portfolio_check:
        if args.final:
            print("\nportfolio-check: skipped in --final mode (it is an iteration-window tool)")
        else:
            _portfolio_check(passed_cfgs, pits, factory, args.timeframe, cfg)

    print("\n" + "=" * 72)
    print(f"DONE '{args.label}': {len(passed_cfgs)} PASS, {n_reject} REJECT, {n_error} skipped/errored")
    print(f"ledger now holds n_trials={ledger.n_trials} at {LEDGER_PATH}")
    if args.final:
        print(f"holdout_looks.log now has {_prior_holdout_looks()} look(s)")
    print("=" * 72)
    return 1 if n_reject else 0


if __name__ == "__main__":
    sys.exit(main())
