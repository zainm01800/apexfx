"""Pre-registered portfolio-level gate: the PEAD sleeve (long-only post-earnings
announcement drift on 33 screened US names).

Implements data_store/pead_prereg.md exactly (research basis: Bernard & Thomas
1989 JAE; Chordia et al. 2014 JAE - the liquid-name long-only honest estimate is
net Sharpe 0.4-0.6). Thin orchestration, no new math: PeadBook
(strategies/pead.py, the crypto_xs_momentum.py shared-model pattern) +
PortfolioBacktester + run_portfolio_cpcv + deflated_sharpe_ratio +
probability_of_backtest_overfitting, composed exactly like
scripts/run_crypto_xs_gate.py. Differences from that gate, all pre-registered:

  * Universe: 33 halal-screened US single names (no banks/financials; TSM out -
    foreign private issuer with no 8-K/2.02). SPY loads separately as the market
    proxy for the madj config and is NOT traded.
  * Event-driven entries from the SEC EDGAR 8-K/2.02 cache
    (data_store/earnings_calendar/), 2-day announcement-window return >= +2% as
    the positive-surprise proxy (no BMO/AMC flag, no estimates - see prereg).
  * exit_mode="barrier" with a catastrophic-only signal barrier pair
    (-30%/+200%): FIXED-HORIZON exits at holding_horizon bars (the academic PEAD
    bet), not the managed TMS stack. The gate tallies exit reasons to prove the
    barriers did not bind.
  * WARMUP=70, not 250: the sleeve needs only its 63-bar volume median + margin.
  * Grid (6 configs, the whole selection set): hold {5,10,20} plain; hold
    {10,20} + volume filter; hold 10 market-adjusted. Headline: pead_h10.

Honesty rules (same as run_portfolio_gate.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 6 new trials are recorded in the shared TrialLedger BEFORE the runs,
    and the ledger's full updated count deflates every DSR.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_pead_gate.py                    # full 6-config gate
    .venv-mac/bin/python scripts/run_pead_gate.py --configs pead_h10 --no-ledger   # smoke test

Exit code 0 if all configs pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import Counter
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
from apex_quant.strategies.pead import PeadBook  # noqa: E402
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
    _cap_families,
    _max_gross_leverage,
    _utc,
)

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "pead_gate_2026-07-19.json"
EVENTS_DIR = ENGINE_DIR / "data_store" / "earnings_calendar"
PPY = 252                      # cash equities (config asset_classes.equity.annualization)
WARMUP = 70                    # 63-bar volume median + margin (pre-registered)
EXIT_MODE = "barrier"          # fixed-horizon exits via catastrophic-only barriers
MARKET = "SPY"                 # market proxy for the madj config (never traded)

# ── Pre-registered selection set (exactly 6 trials) ──────────────────────────
COMMON_PARAMS = {
    "gap_threshold": 0.02,
    "vol_median_window": 63,
    "min_history": 70,
    "stop_pct": 0.30,
    "target_pct": 2.00,
    "reward_risk": 1.5,
    "timeframe": "1d",
}
BOOKS = {
    "pead_h10":     {**COMMON_PARAMS, "holding_horizon": 10},
    "pead_h05":     {**COMMON_PARAMS, "holding_horizon": 5},
    "pead_h20":     {**COMMON_PARAMS, "holding_horizon": 20},
    "pead_h10_vol": {**COMMON_PARAMS, "holding_horizon": 10, "volume_filter": True},
    "pead_h20_vol": {**COMMON_PARAMS, "holding_horizon": 20, "volume_filter": True},
    "pead_h10_madj": {**COMMON_PARAMS, "holding_horizon": 10, "market_adjust": True},
}
HEADLINE = "pead_h10"


def _gate(name: str, rets: pd.Series, trial_sharpes: list[float], pbo: dict,
          cpcv: dict, n_trials: int) -> dict:
    """The three gates, identical to run_portfolio_gate._gate."""
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


def _per_year(eq: pd.Series) -> dict:
    """Per-calendar-year return and Sharpe of the book equity curve (decay check)."""
    out = {}
    rets = eq.pct_change().dropna()
    for yr, g in rets.groupby(rets.index.year):
        if len(g) < 20:
            continue
        out[str(yr)] = {
            "return": round(float((1 + g).prod() - 1), 4),
            "sharpe": round(float(g.mean() / g.std() * (PPY ** 0.5)), 2) if g.std() > 0 else 0.0,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: PEAD sleeve, 6-config "
                                             "hold x filter grid (iteration window only).")
    ap.add_argument("--configs", default="",
                    help="comma-separated subset of grid names (default: all 6)")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: all names with an EDGAR cache)")
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

    # Earnings cache defines the universe (every cached name has EDGAR events).
    events: dict[str, list[str]] = {}
    for p in sorted(EVENTS_DIR.glob("*.json")):
        payload = json.loads(p.read_text())
        if payload.get("events"):
            events[payload["symbol"]] = payload["events"]
    instruments = ([s.strip() for s in args.instruments.split(",") if s.strip()]
                   or sorted(events))

    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        if inst not in events:
            print(f"skip {inst}: no EDGAR earnings cache")
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
        panel[inst] = df
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    spy = clean(store.load(MARKET, "1d"))
    spy_close = spy[spy.index < holdout_start]["close"]

    def _factory(p, **kw):
        return PeadBook(p, events=events, market=spy_close, **kw)

    # Record the 6 pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in books.items():
            ledger.record({"book": name, "universe": f"pead_us{len(panel)}", "timeframe": "1d",
                           "factory": "pead_book", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(books)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (PEAD SLEEVE) 2026-07-19 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} names | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"configs: {len(books)} {list(books)} | headline: {HEADLINE} | exits: fixed-horizon ({EXIT_MODE})")
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
        model = _factory(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode=EXIT_MODE).run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=PPY,
        )
        rets = res.returns
        returns_by_book[name] = rets
        equity_by_book[name] = res.equity
        m = res.metrics
        m["trades_per_week"] = round(m.get("n_trades", 0) / max(len(res.equity) / (7.0 * 252 / PPY), 1e-9), 2)
        exits = dict(Counter(t.exit_reason for t in res.trades))
        results[name] = {"params": params, "metrics": m,
                         "n_events_seen": model.n_events, "n_signals": model.n_qualifying,
                         "exit_reasons": exits,
                         "per_year": _per_year(res.equity),
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        tag = " [HEADLINE]" if name == HEADLINE else ""
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}{tag}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    signals {model.n_qualifying}/{model.n_events} events | "
                  f"expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
                  f"trades/wk={m['trades_per_week']} lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| exits {exits}", flush=True)
            print(f"    caps bound: {_cap_families(res.constraint_log)}", flush=True)

    # 1b. Determinism check: the headline full-window run twice, equity identical.
    det_ok = True
    if HEADLINE in books:
        model = _factory(panel, **books[HEADLINE])
        res2 = PortfolioBacktester(cfg, exit_mode=EXIT_MODE).run(
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

    # 3. CPCV OOS distribution per config (15 paths; purge = the config's own holding horizon).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in books]
    verdicts: dict[str, dict] = {}
    for name, params in books.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, _factory, params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP,
            horizon=params["holding_horizon"],
            periods_per_year=PPY, exit_mode=EXIT_MODE,
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
        "grid": {k: {kk: vv for kk, vv in v.items() if kk not in COMMON_PARAMS
                     or v.get(kk) != COMMON_PARAMS.get(kk)} for k, v in BOOKS.items()},
        "headline": HEADLINE,
        "exit_mode": EXIT_MODE,
        "warmup": WARMUP,
        "periods_per_year": PPY,
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
