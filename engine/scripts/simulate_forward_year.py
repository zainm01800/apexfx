"""What could a year of Book H look like, month by month? — block-bootstrap Monte Carlo.

A single "expected monthly profit" number is a lie for a Sharpe~1 book: the month-to-month
path is dominated by noise, and roughly 4 months in 12 lose money. This script answers the
question the honest way — as a DISTRIBUTION — by re-running the certified book to get its
actual daily returns, then block-bootstrapping (21-day blocks, so autocorrelation and fat
tails survive) 10,000 synthetic 12-month paths.

Two scenarios are always reported side by side:
  * BACKTEST     — the book's in-window statistics as measured (optimistic by construction:
                   in-sample, one data snapshot, no regime change).
  * HAIRCUT 50%  — the same paths with the edge halved, the standard prior for live vs
                   backtest degradation. Treat this as the realistic case, not the base case.

NOT A FORECAST and NOT investment advice: a bootstrap of past returns assumes the future
resembles the sample. The forward paper test has ~2 days of evidence; the certified numbers
are snapshot-dependent (see data_store/book_i_gate.md). Read the spread, not the mean.

Usage:
    cd engine
    .venv-mac/bin/python scripts/simulate_forward_year.py                 # £100k
    .venv-mac/bin/python scripts/simulate_forward_year.py --equity 1000000
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
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

from run_portfolio_gate import COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

OUT_PATH = ENGINE_DIR / "data_store" / "reports" / "forward_year_simulation.json"
BLOCK = 21          # trading days per block ~ one month; preserves autocorrelation
MONTHS = 12
N_SIMS = 10_000
SEED = 42


def book_daily_returns() -> pd.Series:
    """Re-run the certified book (book_h_gold_252) and return its daily return series."""
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    universe = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7
    panel = {}
    for inst in sorted(set(universe)):
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
    model = TrendBook(panel, **params)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes={k: "1d" for k in panel},
        warmup=WARMUP, periods_per_year=252,
    )
    return res.returns.dropna()


def bootstrap_paths(rets: np.ndarray, edge_scale: float, rng) -> np.ndarray:
    """10,000 x 12 matrix of monthly returns from block-resampled daily returns."""
    mu = rets.mean()
    adj = (rets - mu) + mu * edge_scale          # scale the EDGE, keep the noise
    n_blocks = len(adj) - BLOCK
    starts = rng.integers(0, n_blocks, size=(N_SIMS, MONTHS))
    monthly = np.empty((N_SIMS, MONTHS))
    for m in range(MONTHS):
        idx = starts[:, m][:, None] + np.arange(BLOCK)[None, :]
        monthly[:, m] = np.prod(1.0 + adj[idx], axis=1) - 1.0
    return monthly


def summarize(monthly: np.ndarray, equity: float) -> dict:
    annual = np.prod(1.0 + monthly, axis=1) - 1.0
    flat = monthly.ravel()
    curves = np.cumprod(1.0 + monthly, axis=1)
    peaks = np.maximum.accumulate(curves, axis=1)
    max_dd = np.max((peaks - curves) / peaks, axis=1)
    return {
        "monthly_pct": {
            "mean": float(flat.mean() * 100),
            "median": float(np.median(flat) * 100),
            "p05": float(np.percentile(flat, 5) * 100),
            "p95": float(np.percentile(flat, 95) * 100),
            "pct_losing_months": float((flat < 0).mean() * 100),
        },
        "monthly_gbp": {
            "mean": float(flat.mean() * equity),
            "p05": float(np.percentile(flat, 5) * equity),
            "p95": float(np.percentile(flat, 95) * equity),
        },
        "annual_pct": {
            "mean": float(annual.mean() * 100),
            "median": float(np.median(annual) * 100),
            "p05": float(np.percentile(annual, 5) * 100),
            "p25": float(np.percentile(annual, 25) * 100),
            "p75": float(np.percentile(annual, 75) * 100),
            "p95": float(np.percentile(annual, 95) * 100),
            "prob_losing_year": float((annual < 0).mean() * 100),
        },
        "annual_gbp": {
            "median": float(np.median(annual) * equity),
            "p05": float(np.percentile(annual, 5) * equity),
            "p95": float(np.percentile(annual, 95) * equity),
        },
        "worst_month_pct": float(flat.min() * 100),
        "median_max_drawdown_pct": float(np.median(max_dd) * 100),
        "p95_max_drawdown_pct": float(np.percentile(max_dd, 95) * 100),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Block-bootstrap Monte Carlo of a forward year.")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--sims", type=int, default=N_SIMS)
    args = ap.parse_args(argv)

    print("re-running book_h_gold_252 for its daily return series...", flush=True)
    rets = book_daily_returns()
    r = rets.to_numpy()
    # The portfolio series carries crypto weekend bars, so it is NOT a 252-day calendar.
    # Derive observations/year from the actual index span, and size a "month" from that —
    # annualizing this series at 252 (as the gate's own metrics do) overstates nothing but
    # mismeasures the calendar, and a 21-obs block would not be a month here.
    span_years = (rets.index[-1] - rets.index[0]).days / 365.25
    per_year = len(r) / span_years
    block = max(5, int(round(per_year / 12)))
    ann_ret = (1 + r.mean()) ** per_year - 1
    ann_vol = r.std(ddof=0) * np.sqrt(per_year)
    print(f"  {len(r)} obs over {span_years:.1f}y ({per_year:.0f}/yr, {block}-obs months) | "
          f"ann return {ann_ret*100:.2f}% | ann vol {ann_vol*100:.2f}% "
          f"| sharpe {ann_ret/ann_vol:.2f}", flush=True)
    globals()["BLOCK"] = block

    rng = np.random.default_rng(SEED)
    out = {"equity": args.equity, "n_sims": args.sims, "block_days": BLOCK, "seed": SEED,
           "block_days_used": block, "obs_per_year": per_year,
           "source_stats": {"n_days": len(r), "ann_return_pct": ann_ret * 100,
                            "ann_vol_pct": ann_vol * 100, "sharpe": ann_ret / ann_vol},
           "scenarios": {}}
    for label, scale in (("backtest", 1.0), ("haircut_50pct", 0.5)):
        monthly = bootstrap_paths(r, scale, np.random.default_rng(SEED))
        s = summarize(monthly, args.equity)
        out["scenarios"][label] = s
        m, a = s["monthly_pct"], s["annual_pct"]
        print(f"\n=== {label.upper()} (equity £{args.equity:,.0f}) ===")
        print(f"  month: mean {m['mean']:+.2f}% (£{s['monthly_gbp']['mean']:+,.0f}) | "
              f"5-95% band {m['p05']:+.2f}%..{m['p95']:+.2f}% "
              f"(£{s['monthly_gbp']['p05']:+,.0f}..£{s['monthly_gbp']['p95']:+,.0f})")
        print(f"  losing months: {m['pct_losing_months']:.0f}% | worst simulated month {s['worst_month_pct']:.1f}%")
        print(f"  year: median {a['median']:+.1f}% (£{out['scenarios'][label]['annual_gbp']['median']:+,.0f}) | "
              f"5-95% {a['p05']:+.1f}%..{a['p95']:+.1f}% | P(losing year) {a['prob_losing_year']:.0f}%")
        print(f"  drawdown within the year: median {s['median_max_drawdown_pct']:.1f}%, "
              f"95th pct {s['p95_max_drawdown_pct']:.1f}%")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nsaved {OUT_PATH.relative_to(ENGINE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
