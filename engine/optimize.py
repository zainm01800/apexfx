#!/usr/bin/env python3
"""Parallel parameter optimizer for APEX trading strategies.

Performs a Random Search over the strategy + risk parameter space, running
backtests across multiple instruments using local parquet data.  Results are
scored on a composite of Sharpe, Profit Factor, CAGR, and drawdown penalty.

Usage:
    cd engine
    python3 optimize.py                          # default (500 iters)
    python3 optimize.py --iters 1000             # more exploration
    python3 optimize.py --instruments EUR/USD    # single instrument
    python3 optimize.py --timeframes 1d          # single timeframe
    python3 optimize.py --jobs 4                 # override CPU count
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

# Ensure engine/ is on sys.path so we can import apex_quant
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Tunable parameter domains
# ---------------------------------------------------------------------------

# Discrete choices for each parameter (random search samples uniformly from these)
PARAM_DOMAINS: dict[str, list[float | int | str]] = {
    # Risk layer
    "atr_stop_mult": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
    "kelly_fraction": [0.0, 0.05, 0.1, 0.2, 0.3],
    "max_risk_per_trade": [0.005, 0.01, 0.02, 0.03],
    # Strategy
    "momentum_lookback": [21, 42, 63, 126],
    "reward_risk": [1.0, 1.5, 2.0, 3.0],
    "holding_horizon": [5, 10, 20],
}

# Timeframes to test (must match available parquet files)
TIMEFRAMES = ["1d", "1h"]

# Instruments to scan (representative subset across asset classes)
DEFAULT_INSTRUMENTS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",   # forex
    "AAPL", "MSFT", "SPY", "QQQ",                  # equities / ETFs
    "BTC/USD", "ETH/USD",                          # crypto
]

MIN_BARS = 300        # skip instruments with fewer bars than this
WARMUP = 250          # bars of history needed before first signal
DEFAULT_N_ITERS = 500

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

MIN_TRADES = 5  # runs with fewer trades get a penalty


def composite_score(metrics: dict) -> float:
    """Risk-adjusted score: higher is better.

    Formula:
        score = Sharpe × ProfitFactor × CAGR / (1 + MaxDrawdown)²

    The drawdown penalty is squared to strongly discourage high-drawdown
    configurations.  Runs with no trades or zero CAGR score zero.
    """
    sharpe = metrics.get("sharpe", 0.0) or 0.0
    pf = metrics.get("profit_factor", 0.0) or 0.0
    cagr = metrics.get("ann_return", 0.0) or 0.0
    mdd = metrics.get("max_drawdown", 0.0) or 0.0
    n = metrics.get("n_trades", 0)

    if n < MIN_TRADES or cagr <= 0 or sharpe <= 0:
        return 0.0

    # Penalise infinite profit factors (all wins, no losses)
    if pf == float("inf") or pf is None:
        pf = 3.0  # cap at a reasonable upper bound

    return sharpe * pf * cagr / ((1.0 + mdd) ** 2)


# ---------------------------------------------------------------------------
# Parameter sampling
# ---------------------------------------------------------------------------

def sample_params(rng: random.Random) -> dict[str, Any]:
    """Draw one random parameter combination from the domains."""
    return {k: rng.choice(v) for k, v in PARAM_DOMAINS.items()}


def params_to_label(params: dict[str, Any]) -> str:
    """Short human-readable label for a parameter set."""
    return (
        f"atr={params['atr_stop_mult']:.1f} "
        f"kelly={params['kelly_fraction']:.2f} "
        f"risk={params['max_risk_per_trade']:.3f} "
        f"mom={params['momentum_lookback']} "
        f"rr={params['reward_risk']:.1f} "
        f"hold={params['holding_horizon']}"
    )


# ---------------------------------------------------------------------------
# Backtesting harness
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Everything we know about one parameter-set × instrument × timeframe run."""
    params: dict[str, Any]
    instrument: str
    timeframe: str
    metrics: dict = field(default_factory=dict)
    score: float = 0.0
    error: str = ""


def _load_data(instrument: str, timeframe: str, store_dir: str) -> pd.DataFrame | None:
    """Try to load cached parquet data for *instrument* at *timeframe*.

    Uses direct pandas read_parquet to avoid pulling in apex_quant.data.store
    (which triggers heavy dependency chains).
    """
    import pandas as pd

    # Build filename: use the same slug convention as ParquetStore
    slug = instrument.replace("/", "_").replace("-", "_")
    fname = f"{slug}_{timeframe}.parquet"
    path = Path(store_dir) / fname
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty or len(df) < MIN_BARS:
            return None
        return df
    except Exception:
        return None


def _run_backtest(
    params: dict[str, Any],
    instrument: str,
    timeframe: str,
    df: pd.DataFrame,
    seed: int,
    start: str | None = None,
    end: str | None = None,
) -> RunResult:
    """Execute a single backtest run with the given parameters.

    All apex_quant imports are local so the module can be loaded without
    heavy dependency chains (httpx, etc.).
    """
    import numpy as np
    import pandas as pd

    from apex_quant.backtest.engine import Backtester
    from apex_quant.config import RiskConfig, get_config, set_global_seeds
    from apex_quant.data import PointInTimeAccessor
    from apex_quant.risk.manager import RiskManager
    from apex_quant.risk.types import Direction, Signal
    from apex_quant.strategies.base import Strategy

    set_global_seeds(seed)
    cfg = get_config()

    try:
        # ── Build a custom risk config ──────────────────────────────────────
        risk_cfg = RiskConfig(
            target_portfolio_vol=cfg.risk.target_portfolio_vol,
            kelly_fraction=float(params["kelly_fraction"]),
            max_risk_per_trade=float(params["max_risk_per_trade"]),
            max_total_exposure=cfg.risk.max_total_exposure,
            max_correlated_exposure=cfg.risk.max_correlated_exposure,
            correlation_threshold=cfg.risk.correlation_threshold,
            atr_window=cfg.risk.atr_window,
            atr_stop_mult=float(params["atr_stop_mult"]),
            drawdown_breaker=cfg.risk.drawdown_breaker,
            min_position=cfg.risk.min_position,
        )

        # ── Point-in-time accessor ──────────────────────────────────────────
        pit = PointInTimeAccessor(df)

        # ── Strategy: simple momentum with the chosen lookback ──────────────
        lookback = int(params["momentum_lookback"])
        rr = float(params["reward_risk"])
        horizon = int(params["holding_horizon"])

        class _FastMomentum(Strategy):
            """Minimal momentum strategy — no calibration, no regime gating."""
            name = "fast_momentum"

            def generate(self, pit, t, instrument=""):
                w = pit.window(t, lookback + 2)
                if len(w) < lookback + 2:
                    return Signal(
                        instrument=instrument, direction=Direction.FLAT,
                        probability=0.5, reward_risk=rr,
                    )
                close = w["close"]
                mom = close.iloc[-1] / close.iloc[0] - 1.0
                if abs(mom) < 1e-8:
                    return Signal(
                        instrument=instrument, direction=Direction.FLAT,
                        probability=0.5, reward_risk=rr,
                    )
                direction = Direction.LONG if mom > 0 else Direction.SHORT
                prob = 0.5 + 0.3 * min(abs(mom) / 0.05, 1.0)
                return Signal(
                    instrument=instrument, direction=direction,
                    probability=prob, reward_risk=rr,
                )

        strategy = _FastMomentum()

        # ── Run ─────────────────────────────────────────────────────────────
        bt = Backtester(cfg=cfg, risk_manager=RiskManager(risk_cfg))
        result = bt.run(
            pit, strategy, instrument,
            start=start, end=end,
            warmup=WARMUP,
            max_hold=horizon,
        )

        return RunResult(
            params=params,
            instrument=instrument,
            timeframe=timeframe,
            metrics=result.metrics,
            score=composite_score(result.metrics),
        )

    except Exception as exc:
        return RunResult(
            params=params,
            instrument=instrument,
            timeframe=timeframe,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Worker wrapper (multiprocessing-friendly)
# ---------------------------------------------------------------------------

# We pickle the worker args as a tuple so multiprocessing can handle it.
# The function is at module top level so it's importable by worker processes.

def _worker(args: tuple) -> RunResult:
    """Top-level worker function — must be importable (not a lambda/closure)."""
    (params, instrument, timeframe, start, end, seed, data_store_dir) = args

    import pandas as pd

    # Load data directly from parquet — avoids apex_quant heavy imports
    # in the data-loading phase.
    df = _load_data(instrument, timeframe, data_store_dir)
    if df is None:
        return RunResult(
            params=params, instrument=instrument, timeframe=timeframe,
            error=f"insufficient data in {data_store_dir} for {instrument} @ {timeframe}",
        )

    return _run_backtest(params, instrument, timeframe, df, seed, start=start, end=end)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="APEX parameter optimizer")
    parser.add_argument("--iters", type=int, default=DEFAULT_N_ITERS,
                        help=f"Number of random search iterations (default: {DEFAULT_N_ITERS})")
    parser.add_argument("--instruments", type=str, nargs="*",
                        default=None, help="Instruments to test (default: representative set)")
    parser.add_argument("--timeframes", type=str, nargs="*",
                        default=None, help="Timeframes to test (default: 1d 1h)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                        help="Number of parallel workers (default: all CPU cores)")
    parser.add_argument("--start", type=str, default="2022-01-01",
                        help="Backtest start date (default: 2022-01-01)")
    parser.add_argument("--end", type=str, default="2024-12-31",
                        help="Backtest end date (default: 2024-12-31)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    run_args = parser.parse_args()

    instruments = run_args.instruments or DEFAULT_INSTRUMENTS
    timeframes = run_args.timeframes or TIMEFRAMES
    n_iters = run_args.iters
    n_jobs = min(run_args.jobs, os.cpu_count() or 4)
    rng_seed = run_args.seed
    data_store_dir = str(_HERE / "data_store")

    print(f"╔══════════════════════════════════════════════════════════╗")
    print(f"║   APEX Quant — Parallel Parameter Optimizer             ║")
    print(f"║──────────────────────────────────────────────────────────║")
    print(f"║  iterations  : {n_iters:<5}                               ║")
    print(f"║  instruments : {len(instruments):<3} ({', '.join(instruments[:5])}{'…' if len(instruments)>5 else ''})       ║")
    print(f"║  timeframes  : {', '.join(timeframes):<48}║")
    print(f"║  workers     : {n_jobs:<3} (CPU cores)                   ║")
    print(f"║  date range  : {run_args.start} → {run_args.end}                  ║")
    print(f"╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Generate all parameter combinations to search ──────────────────────
    rng = random.Random(rng_seed)
    param_samples = [sample_params(rng) for _ in range(n_iters)]

    # Build all (params, instrument, timeframe) combos, then shuffle for
    # better workload distribution across workers.
    all_tasks: list[tuple] = []
    for params in param_samples:
        for inst in instruments:
            for tf in timeframes:
                all_tasks.append((
                    params,
                    inst,
                    tf,
                    run_args.start,
                    run_args.end,
                    rng_seed,
                    data_store_dir,
                ))

    rng.shuffle(all_tasks)
    total = len(all_tasks)
    print(f"Total backtest tasks to run: {total}")
    print()

    # ── Run in parallel ────────────────────────────────────────────────────
    t0 = time.time()
    results: list[RunResult] = []
    if n_jobs > 1 and total > 1:
        import multiprocessing as mp

        pool = mp.Pool(n_jobs)
        try:
            done = 0
            for res in pool.imap_unordered(_worker, all_tasks, chunksize=4):
                results.append(res)
                done += 1
                if done % max(1, total // 20) == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    ok = sum(1 for r in results if not r.error)
                    print(f"  [{done:5d}/{total}]  {rate:.1f} runs/s  "
                          f"ETA: {eta:.0f}s  (ok={ok})")
        finally:
            pool.close()
            pool.join()
    else:
        # Single-process fallback
        for i, task in enumerate(all_tasks):
            res = _worker(task)
            results.append(res)
            if (i + 1) % max(1, total // 20) == 0 or (i + 1) == total:
                ok = sum(1 for r in results if not r.error)
                print(f"  [{i+1:5d}/{total}]  (ok={ok})")
    elapsed = time.time() - t0
    print(f"\nFinished in {elapsed:.1f}s.")
    print()

    # ── Aggregate ──────────────────────────────────────────────────────────
    # Group results by parameter set (average across instruments / timeframes)
    import numpy as np

    param_groups: dict[str, dict] = {}
    for res in results:
        if res.error:
            continue
        # Use a canonical key for exact parameter match
        key = (
            f"atr={res.params['atr_stop_mult']:.1f};"
            f"kelly={res.params['kelly_fraction']:.2f};"
            f"risk={res.params['max_risk_per_trade']:.3f};"
            f"mom={res.params['momentum_lookback']};"
            f"rr={res.params['reward_risk']:.1f};"
            f"hold={res.params['holding_horizon']}"
        )
        if key not in param_groups:
            param_groups[key] = {
                "params": res.params,
                "scores": [],
                "metrics_list": [],
                "n_runs": 0,
            }
        g = param_groups[key]
        g["scores"].append(res.score)
        g["metrics_list"].append(res.metrics)
        g["n_runs"] += 1

    # Build ranked list
    ranked = []
    for key, g in param_groups.items():
        avg_score = float(np.mean(g["scores"]))
        # Aggregate metrics (mean)
        agg = {
            "sharpe": float(np.mean([m.get("sharpe", 0) or 0 for m in g["metrics_list"]])),
            "profit_factor": float(np.mean([
                m.get("profit_factor", 0) or 0 for m in g["metrics_list"]
            ])),
            "ann_return": float(np.mean([m.get("ann_return", 0) or 0 for m in g["metrics_list"]])),
            "max_drawdown": float(np.mean([m.get("max_drawdown", 0) or 0 for m in g["metrics_list"]])),
            "n_trades": int(np.mean([m.get("n_trades", 0) or 0 for m in g["metrics_list"]])),
            "win_rate": float(np.mean([m.get("win_rate", 0) or 0 for m in g["metrics_list"]])),
        }
        ranked.append((avg_score, g["params"], agg, g["n_runs"]))

    ranked.sort(key=lambda x: x[0], reverse=True)

    # ── Print & save top 10 ────────────────────────────────────────────────
    top = ranked[:10]
    print("=" * 90)
    print("  TOP 10 PARAMETER CONFIGURATIONS")
    print("=" * 90)
    print()
    for i, (score, params, agg, n) in enumerate(top, 1):
        print(f"  #{i}  score={score:.4f}  (avg of {n} instrument×timeframe runs)")
        print(f"       ATR stop={params['atr_stop_mult']:.1f}  "
              f"Kelly={params['kelly_fraction']:.2f}  "
              f"Risk/trade={params['max_risk_per_trade']:.3f}")
        print(f"       Mom lookback={params['momentum_lookback']}  "
              f"R:R={params['reward_risk']:.1f}  "
              f"Hold={params['holding_horizon']}")
        print(f"       → Sharpe={agg['sharpe']:.3f}  "
              f"PF={agg['profit_factor']:.2f}  "
              f"CAGR={agg['ann_return']*100:.1f}%  "
              f"MDD={agg['max_drawdown']*100:.1f}%  "
              f"WinRate={agg['win_rate']*100:.0f}%  "
              f"Trades={agg['n_trades']}")
        print()

    # ── Best recommendation ────────────────────────────────────────────────
    import pandas as pd

    if top:
        best_score, best_params, best_agg, _ = top[0]
        recommendation = (
            f"**Recommended configuration** (highest composite score "
            f"{best_score:.4f}):\n\n"
            f"- ATR stop multiplier: **{best_params['atr_stop_mult']:.1f}**\n"
            f"- Kelly fraction: **{best_params['kelly_fraction']:.2f}**  "
            f"(edge gate; 0 = disabled)\n"
            f"- Max risk per trade: **{best_params['max_risk_per_trade']:.1%}**\n"
            f"- Momentum lookback: **{best_params['momentum_lookback']} bars**\n"
            f"- Reward:risk ratio: **{best_params['reward_risk']:.1f}**\n"
            f"- Holding horizon: **{best_params['holding_horizon']} bars**\n\n"
            f"Expected (average across instruments):\n"
            f"- Sharpe ratio: **{best_agg['sharpe']:.3f}**\n"
            f"- Profit factor: **{best_agg['profit_factor']:.2f}**\n"
            f"- CAGR: **{best_agg['ann_return']*100:.1f}%**\n"
            f"- Max drawdown: **{best_agg['max_drawdown']*100:.1f}%**\n"
            f"- Win rate: **{best_agg['win_rate']*100:.0f}%**\n"
        )
    else:
        recommendation = ("No successful runs completed — check data availability "
                          "in engine/data_store/.")

    # ── Write Markdown report ──────────────────────────────────────────────
    out_dir = _HERE / "data_store"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "optimization_results.md"

    lines = [
        "# APEX Quant — Parameter Optimization Results",
        "",
        f"*Generated: {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M:%S UTC')}*",
        "",
        f"**Search method:** Random Search ({n_iters} iterations × "
        f"{len(instruments)} instruments × {len(timeframes)} timeframes = "
        f"{total} backtest runs)",
        "",
        f"**Date range:** {run_args.start} → {run_args.end}",
        "",
        f"**Workers:** {n_jobs} parallel processes",
        "",
        "---",
        "",
        "## Composite Score Formula",
        "",
        "```",
        "score = Sharpe × ProfitFactor × CAGR / (1 + MaxDrawdown)²",
        "```",
        "",
        "Drawdown is penalised quadratically to favour configs with strong",
        "risk-adjusted returns. Runs with fewer than 5 trades score zero.",
        "",
        "---",
        "",
        "## Top 10 Parameter Configurations",
        "",
        "| Rank | Score | ATR Mult | Kelly | Risk/Trade | Mom Lookback | R:R | Hold | "
        "Sharpe | PF | CAGR | MDD | WinRate | Trades |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | "
        "--- | --- | --- | --- | --- | --- |",
    ]
    for i, (score, params, agg, n) in enumerate(top, 1):
        lines.append(
            f"| {i} | {score:.4f} | {params['atr_stop_mult']:.1f} | "
            f"{params['kelly_fraction']:.2f} | {params['max_risk_per_trade']:.3f} | "
            f"{params['momentum_lookback']} | {params['reward_risk']:.1f} | "
            f"{params['holding_horizon']} | "
            f"{agg['sharpe']:.3f} | {agg['profit_factor']:.2f} | "
            f"{agg['ann_return']*100:.1f}% | {agg['max_drawdown']*100:.1f}% | "
            f"{agg['win_rate']*100:.0f}% | {agg['n_trades']} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Recommendation",
        "",
        recommendation,
        "",
        "---",
        "",
        "### Full parameter space searched",
        "",
        "| Parameter | Values tested |",
        "| --- | --- |",
    ])
    for k, vs in PARAM_DOMAINS.items():
        vs_str = ", ".join(str(v) for v in vs)
        lines.append(f"| {k} | {vs_str} |")

    lines.extend([
        "",
        "### Instruments tested",
        "",
        "| Asset Class | Instruments |",
        "| --- | --- |",
    ])
    forex = [i for i in instruments if "/" in i and i.split("/")[0] not in {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "MATIC", "LINK", "ARB", "SUI"}]
    crypto = [i for i in instruments if any(b in i for b in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "MATIC", "LINK", "ARB", "SUI"])]
    equity = [i for i in instruments if "/" not in i]
    if forex:
        lines.append(f"| Forex | {' · '.join(forex)} |")
    if equity:
        lines.append(f"| Equity/ETF | {' · '.join(equity)} |")
    if crypto:
        lines.append(f"| Crypto | {' · '.join(crypto)} |")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nFull results written to: {out_path}")
    print()


if __name__ == "__main__":
    main()