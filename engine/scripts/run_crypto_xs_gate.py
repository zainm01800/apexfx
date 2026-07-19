"""Pre-registered portfolio-level gate: the crypto cross-sectional momentum sleeve.

Implements data_store/crypto_xs_prereg.md exactly (Sleeve E of
docs/research/2026-07-18_beating_sharpe_1_2.md): weekly-rebalanced, top-3 long-only,
vol-scaled cross-sectional momentum over the liquid crypto universe, with an
optional BTC-63d regime filter (the documented crypto-momentum-crash protection).

Thin orchestration, no new math: CryptoXsMomentum (strategies/crypto_xs_momentum.py,
the cross_sectional.py pattern) + PortfolioBacktester + run_portfolio_cpcv +
deflated_sharpe_ratio + probability_of_backtest_overfitting, composed exactly like
scripts/run_portfolio_gate_multiasset_grid.py. Differences from that gate, all
pre-registered:

  * Universe: the config crypto list (12 names); MATIC/USD has no cached 1d data
    and drops out via the standard skip -> 11 instruments.
  * Grid: momentum lookback {14, 21, 42} x regime_filter {on, off} = 6 configs.
    Headline: lookback 21, regime ON (xs_l021_reg_on).
  * Annualization 365, not 252: crypto trades every calendar day
    (config asset_classes.crypto.annualization). Used for metrics, DSR and CPCV.
  * Post-2021 sub-period (2021-01-01 -> 2024-12-31, inside the iteration window)
    reported separately per config - crypto momentum decayed; say when it works.
  * Determinism check: the headline full-window run is executed twice and the
    equity curves must be identical (seed 42, single-threaded, no network).

Honesty rules (same as run_portfolio_gate.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 6 new trials are recorded in the shared TrialLedger BEFORE the runs,
    and the ledger's full updated count deflates every DSR.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_crypto_xs_gate.py                 # full 6-config gate
    .venv-mac/bin/python scripts/run_crypto_xs_gate.py --configs xs_l021_reg_on,xs_l021_reg_off

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
from apex_quant.backtest.result import compute_metrics  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.crypto_xs_momentum import CryptoXsMomentum  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import (  # noqa: E402
    DSR_THRESHOLD,
    PBO_THRESHOLD,
    run_portfolio_cpcv,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    DEFAULT_HOLDOUT_START,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    _cap_families,
    _max_gross_leverage,
    _utc,
)

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "crypto_xs_gate_2026-07-19.json"
PPY = 365                      # crypto trades every calendar day (config annualization)
HORIZON = 7                    # holding_horizon = weekly time-stop; CPCV purge matches
SUBPERIOD_START = "2021-01-01"  # post-2021 sub-period, inside the iteration window

# ── Pre-registered selection set (exactly 6 trials) ──────────────────────────
COMMON_PARAMS = {
    "vol_window": 63,
    "top_n": 3,
    "min_universe": 4,
    "min_history": 300,
    "regime_instrument": "BTC/USD",
    "regime_lookback": 63,
    "reward_risk": 1.5,
    "holding_horizon": HORIZON,
    "timeframe": "1d",
}
LOOKBACKS = (21, 14, 42)       # headline lookback first
BOOKS = {
    f"xs_l{lb:03d}_reg_{'on' if reg else 'off'}": {**COMMON_PARAMS, "lookback": lb,
                                                   "regime_filter": reg}
    for lb in LOOKBACKS for reg in (True, False)
}
HEADLINE = "xs_l021_reg_on"


def _gate(name: str, rets: pd.Series, trial_sharpes: list[float], pbo: dict,
          cpcv: dict, n_trials: int) -> dict:
    """The three gates, identical to run_portfolio_gate._gate but annualized at 365
    (crypto trades every calendar day - config asset_classes.crypto.annualization)."""
    dsr = deflated_sharpe_ratio(rets.to_numpy(), trial_sharpes, PPY, n_trials=n_trials)
    dsr_pass = dsr.get("dsr", 0.0) > DSR_THRESHOLD
    pbo_val = pbo.get("pbo")
    pbo_pass = pbo_val is not None and pbo_val < PBO_THRESHOLD
    cpcv_pass = cpcv.get("oos_sharpe_median", 0.0) > 0 and cpcv.get("frac_positive", 0.0) > 0.5
    passed = bool(dsr_pass and pbo_pass and cpcv_pass)
    return {
        "book": name,
        "passed": passed,
        "dsr_pass": dsr_pass,
        "pbo_pass": pbo_pass,
        "cpcv_pass": cpcv_pass,
        "dsr": dsr,
        "pbo": pbo,
        "cpcv": cpcv,
        "reasons": [
            f"DSR {dsr.get('dsr', 0):.3f} {'>' if dsr_pass else '<='} {DSR_THRESHOLD} "
            f"(deflated by {dsr.get('n_trials')} trials)",
            f"PBO {pbo_val if pbo_val is not None else 'n/a'} "
            f"{'<' if pbo_pass else '>='} {PBO_THRESHOLD} (config-selection overfit probability)",
            f"CPCV median OOS Sharpe {cpcv.get('oos_sharpe_median', 0):.3f}, "
            f"{cpcv.get('frac_positive', 0)*100:.0f}% of {cpcv.get('n_paths', 0)} paths positive",
        ],
    }


def _subperiod_metrics(res, since: str = SUBPERIOD_START) -> dict:
    """Post-2021 sub-period inside the iteration window: equity sliced at `since`,
    trades counted by exit date within the sub-period, metrics recomputed at 365/yr.
    Positions opened before `since` and closed after it contribute their full P&L
    to the sub-period's trade stats (exit-date convention), and their mark-to-market
    is in the equity slice throughout - documented in the prereg deliverable."""
    eq = res.equity[res.equity.index >= _utc(since)]
    trades = [t for t in res.trades if t.exit_time >= since]
    m = compute_metrics(eq, trades, PPY)
    m["trades_per_week"] = round(len(trades) / max(len(eq) / 7.0, 1e-9), 2)
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: crypto cross-sectional "
                                             "momentum sleeve, 6-config lookback x regime grid "
                                             "(iteration window only).")
    ap.add_argument("--configs", default="",
                    help="comma-separated subset of grid names (default: all 6)")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: config crypto list; MATIC skips)")
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
                   or list(cfg.data.crypto))

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

    # Record the 6 pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in books.items():
            ledger.record({"book": name, "universe": "crypto_11", "timeframe": "1d",
                           "factory": "crypto_xs_momentum", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(books)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (CRYPTO XS MOMENTUM, SLEEVE E) 2026-07-19 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments {sorted(panel)} | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"configs: {len(books)} {list(books)} | headline: {HEADLINE} | annualization: {PPY}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config -> returns (DSR/PBO) + trade metrics, one shared
    #    equity curve with config risk caps binding.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    equity_by_book: dict[str, pd.Series] = {}
    for name, params in books.items():
        t_start = time.time()
        model = CryptoXsMomentum(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=PPY,
        )
        rets = res.returns
        returns_by_book[name] = rets
        equity_by_book[name] = res.equity
        m = res.metrics
        m["trades_per_week"] = round(m.get("n_trades", 0) / max(len(res.equity) / 7.0, 1e-9), 2)
        results[name] = {"params": params, "metrics": m,
                         "subperiod_post2021": _subperiod_metrics(res),
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        tag = " [HEADLINE]" if name == HEADLINE else ""
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}{tag}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            sp = results[name]["subperiod_post2021"]
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
                  f"trades/wk={m['trades_per_week']} lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)
            print(f"    post-2021: sharpe={sp.get('sharpe', 0):.2f} "
                  f"PF={sp.get('profit_factor')} maxDD={sp.get('max_drawdown', 0)*100:.1f}% "
                  f"trades={sp.get('n_trades')} ({sp.get('trades_per_week')}/wk)", flush=True)

    # 1b. Determinism check: the headline full-window run twice, equity identical.
    det_ok = True
    if HEADLINE in books:
        model = CryptoXsMomentum(panel, **books[HEADLINE])
        res2 = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=PPY,
        )
        det_ok = bool(equity_by_book[HEADLINE].equals(res2.equity))
        print(f"determinism check (headline run twice, seed {cfg.seed}): "
              f"{'IDENTICAL' if det_ok else 'MISMATCH'}", flush=True)

    # 2. PBO across the whole pre-registered selection set (6 configs).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV OOS distribution per config (15 paths; purge = the 7-bar holding horizon).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in books]
    verdicts: dict[str, dict] = {}
    for name, params in books.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: CryptoXsMomentum(p, **kw), params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=PPY, exit_mode="managed",
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
        "grid": {"lookback": list(LOOKBACKS), "regime_filter": [True, False]},
        "headline": HEADLINE,
        "periods_per_year": PPY,
        "subperiod_start": SUBPERIOD_START,
        "determinism_check": det_ok,
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
