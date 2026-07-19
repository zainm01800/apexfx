"""Pre-registered portfolio-level gate: the US large-cap short-term reversal sleeve.

Implements data_store/st_reversal_prereg.md exactly (research-audit Task B rank #1:
Nagel 2012 RFS — reversal profits are liquidity-provision returns that spike in
high-vol states; de Groot/Huij/Zhou 2012 JBF — cost-aware construction keeps
30-50bps/week net on large-liquid US names). Weekly-rebalanced, long-only,
bottom-bucket 5-day-loser reversal over 32 halal-screened US large caps (no
banks/financials), with cost-aware and vol-state variants.

Thin orchestration, no new math: ShortTermReversal (strategies/st_reversal.py,
the cross_sectional.py / crypto_xs_momentum.py pattern) + PortfolioBacktester +
run_portfolio_cpcv + deflated_sharpe_ratio + probability_of_backtest_overfitting,
composed exactly like scripts/run_crypto_xs_gate.py. Differences from that gate,
all pre-registered:

  * Universe: 32 screened US large caps (12 already in the store + 20 fetched via
    the normal ParquetStore.get_or_fetch Yahoo path 2026-07-19). SPY is loaded
    ONLY as the vol-state / correlation reference - the model never trades it.
  * Grid: formation {5, 10} x filter {plain, cost, vol_state} = 6 configs.
    Headline: formation 5, plain, bottom-3 (rev_f5_plain).
  * Annualization 252 (cash equities; config asset_classes.equity). Costs: the
    v5 equity bps model - 2bps spread + 1bps slippage per side (~4bps round trip).
  * Texture checks the mechanism requires, reported per config: annualized
    turnover + realized weekly cost estimate; SPY-21d-vol regime breakdown (does
    the high-vol half carry the P&L?); named crisis episodes; and rho of the
    sleeve's daily returns vs the book_d multi-asset trend book (reconstructed
    by re-running its exact pre-registered config - the 2026-07-17 gate JSON
    stores metrics only, no equity series; already ledgered, no new trial) and
    vs SPY as reference.

Honesty rules (same as run_portfolio_gate.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
    The 2025+ holdout is never touched here.
  * Exactly 6 new trials are recorded in the shared TrialLedger BEFORE the runs,
    and the ledger's full updated count deflates every DSR.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_st_reversal_gate.py                 # full 6-config gate
    .venv-mac/bin/python scripts/run_st_reversal_gate.py --configs rev_f5_plain,rev_f5_cost

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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.st_reversal import ShortTermReversal  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
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
from run_portfolio_gate_multiasset import BOOKS as MA_BOOKS, FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "st_reversal_gate_2026-07-19.json"
PPY = 252                      # cash equities (config asset_classes.equity.annualization)
HORIZON = 5                    # holding_horizon = weekly time-stop; CPCV purge matches
UNIVERSE_LABEL = "us_largecap_halal_32"

# 32 screened liquid US large caps (no banks/financials - halal constraint, see
# the prereg). SPY is added separately as the vol-state reference, never traded.
ST_REVERSAL_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR", "TSM",
    "NFLX", "UBER", "XOM", "JNJ", "WMT", "PG", "KO", "V", "MA", "HD", "BA", "CAT",
    "INTC", "CSCO", "ORCL", "CRM", "ADBE", "PFE", "ABBV", "NKE", "MCD", "COST",
]
REGIME_INSTRUMENT = "SPY"

# Named high-vol episodes inside the iteration window (crisis-alpha inspection).
EPISODES = {
    "volmageddon_2018": ("2018-02-01", "2018-02-28"),
    "q4_2018": ("2018-10-01", "2018-12-31"),
    "covid_2020": ("2020-02-19", "2020-04-30"),
    "bear_2022": ("2022-01-03", "2022-10-31"),
}

# ── Pre-registered selection set (exactly 6 trials) ──────────────────────────
COMMON_PARAMS = {
    "vol_window": 20,
    "sig_mult": 1.5,
    "min_universe": 10,
    "min_history": 300,
    "regime_instrument": REGIME_INSTRUMENT,
    "mkt_vol_window": 21,
    "mkt_median_window": 126,
    "reward_risk": 1.5,
    "holding_horizon": HORIZON,
    "timeframe": "1d",
}
BOOKS = {
    "rev_f5_plain": {**COMMON_PARAMS, "formation": 5, "filter_mode": "plain", "bottom_n": 3},
    "rev_f5_cost": {**COMMON_PARAMS, "formation": 5, "filter_mode": "cost", "bottom_n": 2},
    "rev_f5_volstate": {**COMMON_PARAMS, "formation": 5, "filter_mode": "vol_state", "bottom_n": 3},
    "rev_f10_plain": {**COMMON_PARAMS, "formation": 10, "filter_mode": "plain", "bottom_n": 3},
    "rev_f10_cost": {**COMMON_PARAMS, "formation": 10, "filter_mode": "cost", "bottom_n": 2},
    "rev_f10_volstate": {**COMMON_PARAMS, "formation": 10, "filter_mode": "vol_state", "bottom_n": 3},
}
HEADLINE = "rev_f5_plain"


def _annualized_turnover(res) -> float:
    """One-way entry notional traded per year per unit of mean equity. Quote-
    currency conversion ignored (same approximation as _max_gross_leverage;
    helper mirrored from scripts/run_volmanaged_book_gate.py)."""
    if not res.trades or res.equity.empty:
        return 0.0
    notional = sum(abs(tr.entry_price * tr.units) for tr in res.trades)
    mean_eq = float(res.equity.mean())
    years = len(res.equity) / 252.0
    return float(notional / mean_eq / years) if mean_eq > 0 and years > 0 else 0.0


def _spy_vol_state(spy_close: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """SPY 21d realised vol >= its 126d rolling median, aligned to `index`
    (backward-only - the same construction the strategy's vol_state mode uses)."""
    vol = np.log(spy_close).diff().rolling(21).std(ddof=1)
    med = vol.rolling(126).median()
    return (vol >= med).reindex(index).fillna(False)


def _vol_regime_breakdown(rets: pd.Series, spy_close: pd.Series) -> dict:
    """Split the sleeve's daily returns by the SPY-21d-vol state. The Nagel
    mechanism requires the HIGH-vol half to carry the P&L."""
    high = _spy_vol_state(spy_close, rets.index)
    tot_log = float(np.log1p(rets).sum())
    out = {}
    for label, mask in (("high_vol", high), ("low_vol", ~high)):
        r = rets[mask]
        lg = float(np.log1p(r).sum()) if len(r) else 0.0
        sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
        out[label] = {
            "n_days": int(len(r)),
            "mean_daily_bps": round(float(r.mean()) * 1e4, 2) if len(r) else None,
            "sharpe": round(float(r.mean() / sd) * np.sqrt(PPY), 3) if sd > 0 else None,
            "log_ret_sum": round(lg, 4),
            "pnl_share": round(lg / tot_log, 3) if abs(tot_log) > 1e-12 else None,
        }
    return out


def _episode_rows(rets: pd.Series, spy_rets: pd.Series) -> dict:
    """Sleeve vs SPY total return inside each named crisis episode."""
    rows = {}
    for name, (a, b) in EPISODES.items():
        r = rets[(rets.index >= _utc(a)) & (rets.index <= _utc(b))]
        s = spy_rets[(spy_rets.index >= _utc(a)) & (spy_rets.index <= _utc(b))]
        rows[name] = {
            "sleeve_ret": round(float((1 + r).prod() - 1), 4) if len(r) else None,
            "spy_ret": round(float((1 + s).prod() - 1), 4) if len(s) else None,
            "n_days": int(len(r)),
        }
    return rows


def _corr(a: pd.Series, b: pd.Series) -> float | None:
    """Daily-return correlation on the aligned (inner) index."""
    j = pd.concat([a, b], axis=1).dropna()
    return round(float(j.corr().iloc[0, 1]), 4) if len(j) >= 40 else None


def _load_panel(store: ParquetStore, instruments: list[str], holdout_start) -> dict:
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
    return panel


def _trend_book_returns(cfg, store: ParquetStore, holdout_start) -> pd.Series | None:
    """Reconstruct the book_d multi-asset trend book's daily returns by re-running
    its exact pre-registered config on the iteration window. The 2026-07-17 gate
    JSON stores metrics only - no equity series - so this re-run (already in the
    ledger, no new trial) is the access path. Returns None if the panel is thin."""
    params = MA_BOOKS["book_d_multiasset_252"]
    instruments = list(cfg.data.equities) + list(cfg.data.crypto) + FX_MAJORS_7
    panel = _load_panel(store, instruments, holdout_start)
    if len(panel) < 2:
        return None
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    model = TrendBook(panel, **params)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes={k: "1d" for k in panel},
        warmup=WARMUP, periods_per_year=PPY,
    )
    print(f"book_d trend re-run for rho: {res.summary()} "
          f"({len(panel)} instruments)", flush=True)
    return res.returns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: US large-cap short-term "
                                             "reversal sleeve, 6-config formation x filter grid "
                                             "(iteration window only).")
    ap.add_argument("--configs", default="",
                    help="comma-separated subset of grid names (default: all 6)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--skip-trend-rho", action="store_true",
                    help="skip the book_d trend-book re-run (rho vs trend then reports vs SPY only)")
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

    # The model needs SPY in the panel (vol-state reference) but never trades it:
    # pits (what the backtester iterates) exclude it - strategies() matches.
    panel = _load_panel(store, ST_REVERSAL_UNIVERSE + [REGIME_INSTRUMENT], holdout_start)
    if REGIME_INSTRUMENT not in panel:
        print(f"need {REGIME_INSTRUMENT} cached for the vol-state reference")
        return 1
    if len(panel) < 11:
        print("need >= 10 tradable instruments + SPY for this gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items() if k != REGIME_INSTRUMENT}
    timeframes = {k: "1d" for k in pits}
    spy_close = panel[REGIME_INSTRUMENT]["close"]
    spy_rets = spy_close.pct_change().dropna()

    # Record the 6 pre-registered trials BEFORE running, so this run's own trials
    # count toward the deflation denominator (canonical-JSON dedup inside TrialLedger).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in books.items():
            ledger.record({"book": name, "universe": UNIVERSE_LABEL, "timeframe": "1d",
                           "factory": "st_reversal", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(books)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (US LARGE-CAP ST REVERSAL, HALAL-SCREENED) 2026-07-19 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    print(f"universe: {len(pits)} tradable + {REGIME_INSTRUMENT} reference | window: "
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
        model = ShortTermReversal(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=PPY,
        )
        rets = res.returns
        returns_by_book[name] = rets
        equity_by_book[name] = res.equity
        m = res.metrics
        n_weeks = max(len(res.equity) / 5.0, 1e-9)          # 252-calendar: 5 bars/week
        m["trades_per_week"] = round(m.get("n_trades", 0) / n_weeks, 2)
        turnover = _annualized_turnover(res)
        results[name] = {"params": params, "metrics": m,
                         "annualized_turnover": round(turnover, 2),
                         "est_cost_drag_pct_per_year": round(turnover * 0.04, 3),  # ~4bps RT
                         "est_cost_bps_per_week": round(turnover * 4.0 / 52.0, 1),
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "vol_regime_breakdown": _vol_regime_breakdown(rets, spy_close),
                         "crisis_episodes": _episode_rows(rets, spy_rets),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        tag = " [HEADLINE]" if name == HEADLINE else ""
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}{tag}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            vr = results[name]["vol_regime_breakdown"]
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
                  f"trades/wk={m['trades_per_week']} turnover~{turnover:.1f}x/yr "
                  f"(cost~{results[name]['est_cost_bps_per_week']}bps/wk) "
                  f"lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)
            print(f"    vol regimes: high-vol {vr['high_vol']['n_days']}d "
                  f"sharpe={vr['high_vol']['sharpe']} pnl_share={vr['high_vol']['pnl_share']} | "
                  f"low-vol {vr['low_vol']['n_days']}d sharpe={vr['low_vol']['sharpe']} "
                  f"pnl_share={vr['low_vol']['pnl_share']}", flush=True)
            eps = results[name]["crisis_episodes"]
            print(f"    episodes: " + "; ".join(
                f"{k} {v['sleeve_ret']*100:+.1f}% (SPY {v['spy_ret']*100:+.1f}%)"
                for k, v in eps.items() if v["sleeve_ret"] is not None), flush=True)

    # 1b. Determinism check: the headline full-window run twice, equity identical.
    det_ok = True
    if HEADLINE in books:
        model = ShortTermReversal(panel, **books[HEADLINE])
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

    # 3. CPCV OOS distribution per config (15 paths; purge = the 5-bar holding horizon).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in books]
    verdicts: dict[str, dict] = {}
    for name, params in books.items():
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: ShortTermReversal(p, **kw), params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=PPY, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. Correlation texture: rho vs the book_d trend book (re-run of its exact
    #    pre-registered config - the gate JSON stores no equity series) and vs SPY.
    print("-" * 72, flush=True)
    trend_rets = None if args.skip_trend_rho else _trend_book_returns(cfg, store, holdout_start)
    rho_note = ("book_d curve reconstructed by re-running its pre-registered config on the "
                "iteration window (portfolio_gate_multiasset_2026-07-17.json stores metrics only, "
                "no equity series); rho vs SPY reported alongside as reference."
                if trend_rets is not None else
                "book_d re-run skipped or panel thin - rho reported vs SPY only.")
    for name in books:
        results[name]["rho_vs_spy"] = _corr(returns_by_book[name], spy_rets)
        results[name]["rho_vs_trend_book_d"] = (_corr(returns_by_book[name], trend_rets)
                                                if trend_rets is not None else None)
        print(f"  rho {name}: vs book_d={results[name]['rho_vs_trend_book_d']} "
              f"vs SPY={results[name]['rho_vs_spy']}", flush=True)

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
        "universe": list(pits.keys()),
        "regime_instrument": REGIME_INSTRUMENT,
        "grid": {"formation": [5, 10], "filter_mode": ["plain", "cost", "vol_state"]},
        "headline": HEADLINE,
        "periods_per_year": PPY,
        "determinism_check": det_ok,
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "rho_note": rho_note,
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
