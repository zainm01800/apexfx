"""
Full Universe Adaptive Backtester
==================================
Runs all instruments (forex, crypto, equities) across all trading styles
(scalp 15m, intraday 1h, swing 1d, position 1d) and saves results to CSV.

Features:
  - Skips already-cached instruments on re-runs (instant load)
  - Rate-limit safe (8s sleep between Twelve Data API fetches)
  - Saves incremental progress to results CSV after each instrument
  - Resumable: skips instruments already in results CSV
  - Prints a live summary table as it goes

Usage:
  python scripts/run_full_backtest.py
  python scripts/run_full_backtest.py --styles scalp intraday
  python scripts/run_full_backtest.py --instruments BTC/USD ETH/USD
  python scripts/run_full_backtest.py --start 2024-01-01
  python scripts/run_full_backtest.py --resume   # skip already-completed rows
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Bootstrap path ──────────────────────────────────────────────────────────
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

# ── Load .env ────────────────────────────────────────────────────────────────
def load_local_env():
    env_path = ENGINE_DIR / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

load_local_env()

from apex_quant.config import get_config
from apex_quant.data import clean, get_adapter
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.data.store import ParquetStore
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.backtest.engine import Backtester

cfg = get_config()

# ── Style parameters ─────────────────────────────────────────────────────────
STYLE_PARAMS = {
    "ultra_scalp": {
        "timeframe": "1m",
        "momentum_lookback": 8,
        "vol_window": 8,
        "holding_horizon": 15,   # max 15 minutes hold (15 x 1m bars)
        "warmup": 40,
        "max_history_days": 14,  # OANDA demo/live key 1m history limit
        "atr_stop_mult": 1.5,
        "reward_risk": 1.2
    },
    "micro_scalp": {
        "timeframe": "5m",
        "momentum_lookback": 8,
        "vol_window": 8,
        "holding_horizon": 12,   # max 1 hour hold (12 x 5m bars)
        "warmup": 60,
        "max_history_days": 180, # Extended from 57 for OandaAdapter (6 months)
        "atr_stop_mult": 2.0,
        "reward_risk": 1.3
    },
    "scalp": {
        "timeframe": "15m",
        "momentum_lookback": 14,
        "vol_window": 14,
        "holding_horizon": 36, # shortened from 48 for faster trade exit/capital recycling
        "warmup": 70, # ma_window=50 + momentum_lookback=14 + buffer
        "max_history_days": 730, # Extended from 59 for OandaAdapter (2 years)
        "atr_stop_mult": 2.5,  # optimized stop mult for active scalping
        "reward_risk": 1.5     # optimized R:R target for faster take-profits
    },
    "intraday": {
        "timeframe": "1h",
        "momentum_lookback": 24,
        "vol_window": 24,
        "holding_horizon": 72, # 3 days max hold
        "warmup": 80, # ma_window=50 + momentum_lookback=24 + buffer
        "max_history_days": 1460, # Extended from 720 for OandaAdapter (4 years)
        "atr_stop_mult": 2.5,  # optimized stop mult for intraday trading
        "reward_risk": 2.0     # 2.0 R:R for intraday
    },
    "swing": {
        "timeframe": "1d",
        "momentum_lookback": 63,
        "vol_window": 63,
        "holding_horizon": 10,
        "warmup": 120, # ma_window=50 + momentum_lookback=63 + buffer
        "max_history_days": 10000,
        "atr_stop_mult": 3.0,  # wider daily baseline stop
        "reward_risk": 2.0
    },
    "position": {
        "timeframe": "1d",
        "momentum_lookback": 126,
        "vol_window": 126,
        "holding_horizon": 40,
        "warmup": 180, # ma_window=50 + momentum_lookback=126 + buffer
        "max_history_days": 10000,
        "atr_stop_mult": 3.0,  # wider daily baseline stop
        "reward_risk": 2.0
    },
}

CSV_COLUMNS = [
    "instrument", "style", "timeframe", "start_date", "end_date",
    "n_trades", "win_rate", "net_pnl", "profit_factor",
    "run_at",
]

TRADE_COLUMNS = [
    "instrument", "style", "timeframe",
    "direction", "entry_time", "entry_price",
    "exit_time", "exit_price", "units",
    "pnl", "return_pct", "exit_reason",
]

def calc_metrics(trades):
    if not trades:
        return {"n_trades": 0, "win_rate": None, "net_pnl": 0.0, "profit_factor": None}
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and wins else (float("inf") if wins else 0.0)
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(pnls) * 100, 1),
        "net_pnl": round(sum(pnls), 2),
        "profit_factor": round(pf, 3) if np.isfinite(pf) else None,
    }

def fmt(v, suffix=""):
    if v is None:
        return "N/A"
    return f"{v}{suffix}"


def save_trades(trades, instrument: str, style: str, timeframe: str, trades_dir: Path):
    """Append individual trade records to a per-style parquet file."""
    if not trades:
        return
    trades_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for t in trades:
        rows.append({
            "instrument": instrument,
            "style": style,
            "timeframe": timeframe,
            "direction": t.direction,
            "entry_time": str(t.entry_time),
            "entry_price": t.entry_price,
            "exit_time": str(t.exit_time),
            "exit_price": t.exit_price,
            "units": t.units,
            "pnl": t.pnl,
            "return_pct": t.return_pct,
            "exit_reason": t.exit_reason,
        })
    new_df = pd.DataFrame(rows)
    parquet_path = trades_dir / f"trades_{style}.parquet"
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        # Remove old rows for this instrument+style combo then append fresh
        existing = existing[~((existing["instrument"] == instrument) &
                              (existing["style"] == style))]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_parquet(parquet_path, index=False)
    print(f"      -> {len(rows)} trades saved to {parquet_path.name}")

def run_one(instrument: str, style: str, start_str: str, end_str: str,
            store: ParquetStore, adapter, use_twelve: bool,
            trades_dir: Path | None = None) -> dict | None:
    params = STYLE_PARAMS[style]
    timeframe = params["timeframe"]
    warmup = params["warmup"]
    now = datetime.utcnow()

    # Twelve Data free tier doesn't support intraday (15m/1h) for equities.
    # Skip early — swing/position (1d) still work fine via Yahoo daily data.
    is_equity = instrument in list(cfg.data.equities)
    is_intraday_tf = timeframe in ("5m", "15m", "1h")

    # For equities on intraday timeframes, Twelve Data free tier doesn't work.
    # Fall back to Yahoo Finance which supports 15m (60 days) and 1h (730 days).
    YAHOO_INTRADAY_LIMITS = {"5m": 57, "15m": 59, "1h": 730}
    
    style_start_str = start_str
    if is_equity:
        print(f"\n    [fallback] equity -> Yahoo Finance", end=" ", flush=True)
        yahoo_adapter = get_adapter("yahoo")
        if is_intraday_tf:
            max_days = YAHOO_INTRADAY_LIMITS.get(timeframe, 59)
            earliest = now - timedelta(days=max_days)
            start_dt = datetime.strptime(style_start_str, "%Y-%m-%d")
            if start_dt < earliest:
                style_start_str = (earliest + timedelta(days=2)).strftime("%Y-%m-%d")
        active_adapter = yahoo_adapter
        need_sleep = False
    elif use_twelve and is_intraday_tf:
        print(f"\n    [fallback] equity intraday -> Yahoo Finance", end=" ", flush=True)
        yahoo_adapter = get_adapter("yahoo")
        max_days = YAHOO_INTRADAY_LIMITS.get(timeframe, 59)
        earliest = now - timedelta(days=max_days)
        start_dt = datetime.strptime(style_start_str, "%Y-%m-%d")
        if start_dt < earliest:
            style_start_str = (earliest + timedelta(days=2)).strftime("%Y-%m-%d")
        active_adapter = yahoo_adapter
        need_sleep = False
    else:
        active_adapter = adapter
        # Clamp start for Yahoo (non-Twelve Data) non-equity runs
        if not use_twelve:
            max_days = params["max_history_days"]
            earliest = now - timedelta(days=max_days)
            start_dt = datetime.strptime(style_start_str, "%Y-%m-%d")
            if start_dt < earliest:
                style_start_str = (earliest + timedelta(days=2)).strftime("%Y-%m-%d")
        elif timeframe == "1d":
            # For daily (1d) styles (Swing/Position) using Twelve Data, request 3 years (1095 days)
            # to make sure we satisfy Position's 316-bar warmup requirement.
            earliest = now - timedelta(days=1095)
            start_dt = datetime.strptime(style_start_str, "%Y-%m-%d")
            if start_dt > earliest:
                style_start_str = earliest.strftime("%Y-%m-%d")
        need_sleep = use_twelve

    # Cache-miss check (only sleep for Twelve Data if fetch needed)
    cached = store.load(instrument, timeframe)
    start_ts = pd.Timestamp(style_start_str, tz="UTC")
    end_ts = pd.Timestamp(end_str, tz="UTC")
    need_fetch = cached.empty or cached.index[0] > start_ts or cached.index[-1] < end_ts

    if need_fetch and need_sleep:
        print(f"    [rate-limit] sleeping 8s before fetching {instrument} {timeframe}...")
        time.sleep(8.0)

    try:
        df = clean(store.get_or_fetch(instrument, active_adapter, style_start_str, end_str, timeframe=timeframe))
    except Exception as e:
        print(f"    [fetch error] {instrument} {style}: {e}")
        return None


    min_bars = warmup + params["momentum_lookback"] + 10
    if len(df) < min_bars:
        print(f"    [skip] {instrument} {style}: only {len(df)} bars (need {min_bars})")
        return None

    # Override risk parameters in config dynamically for this style
    cfg.risk.atr_stop_mult = params.get("atr_stop_mult", 3.0)

    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum(
        momentum_lookback=params["momentum_lookback"],
        vol_window=params["vol_window"],
        holding_horizon=params["holding_horizon"],
        reward_risk=params.get("reward_risk", 2.0),
        regime_method="rule_based",
        timeframe=timeframe,
        bypass_calibration=False,
        instrument=instrument,
        enable_mean_reversion=True,
    )
    strat.fit(pit, df.index)

    try:
        res = Backtester(cfg).run(
            pit, strat, instrument,
            start=start_str, end=end_str,
            warmup=warmup, max_hold=params["holding_horizon"]
        )
        metrics = calc_metrics(res.trades)
        metrics["start_date"] = start_str
        metrics["end_date"] = end_str
        if trades_dir and res.trades:
            save_trades(res.trades, instrument, style, timeframe, trades_dir)
        return metrics
    except Exception as e:
        print(f"    [backtest error] {instrument} {style}: {e}")
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(description="Full Universe Backtester")
    parser.add_argument("--instruments", nargs="*", default=None,
                        help="Override instrument list (space-separated)")
    parser.add_argument("--styles", nargs="*",
                        default=["micro_scalp", "scalp", "intraday", "swing", "position"],
                        choices=list(STYLE_PARAMS.keys()),
                        help="Styles to run")
    parser.add_argument("--start", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: 1 year ago for Twelve Data, style-specific for Yahoo)")
    parser.add_argument("--end", type=str, default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--output", type=str, default="backtest_results.csv",
                        help="Output CSV filename (saved in data_store/)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip instrument+style combos already in the output CSV")
    args = parser.parse_args()

    twelve_key = os.getenv("APEX_TWELVE_DATA_KEY")
    use_twelve = bool(twelve_key and twelve_key.strip())

    now = datetime.utcnow()
    end_str = args.end or now.strftime("%Y-%m-%d")

    # Build instrument list
    if args.instruments:
        instruments = args.instruments
    else:
        instruments = list(cfg.universe)

    styles = args.styles

    # Set default start
    if args.start:
        default_start = args.start
    elif use_twelve:
        default_start = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    else:
        default_start = (now - timedelta(days=59)).strftime("%Y-%m-%d")  # Yahoo 15m limit

    # Output CSV path
    output_path = Path(cfg.data.store_dir)
    if not output_path.is_absolute():
        output_path = ENGINE_DIR / output_path
    output_path.mkdir(parents=True, exist_ok=True)
    csv_path = output_path / args.output
    trades_dir = output_path / "trades"  # individual trade records saved here
    trades_dir.mkdir(parents=True, exist_ok=True)

    # Load already-completed rows for resume
    completed = set()
    if args.resume and csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                completed.add((row["instrument"], row["style"]))
        print(f"[resume] {len(completed)} already-completed (instrument, style) combos found.")

    # Open CSV for append
    csv_exists = csv_path.exists()
    csv_file = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
    if not csv_exists:
        writer.writeheader()
        csv_file.flush()

    # Setup adapter
    provider_name = cfg.data.provider.lower()
    if provider_name == "oanda":
        adapter = get_adapter("oanda")
        print(f"[*] Data adapter: OANDA REST API (dual-endpoint)")
        use_twelve = False  # Disable Twelve Data overrides for OANDA
    elif use_twelve:
        from apex_quant.data.twelve_data_adapter import TwelveDataAdapter
        adapter = TwelveDataAdapter(api_key=twelve_key.strip())
        print(f"[*] Data adapter: Twelve Data (Key: {twelve_key[:6]}...)")
    else:
        adapter = get_adapter(cfg.data.provider)
        print(f"[*] Data adapter: {adapter.__class__.__name__}")

    store = ParquetStore()
    total_combos = len(instruments) * len(styles)
    done = 0

    print(f"\n{'='*80}")
    print(f"  FULL UNIVERSE BACKTEST")
    print(f"  {len(instruments)} instruments × {len(styles)} styles = {total_combos} backtests")
    print(f"  Period: {default_start} -> {end_str}")
    print(f"  Output: {csv_path}")
    print(f"{'='*80}\n")

    # Summary accumulator for final table
    all_results = []

    for inst in instruments:
        print(f"\n{'-'*60}")
        print(f"  Instrument: {inst}")
        print(f"{'-'*60}")

        for style in styles:
            done += 1
            if (inst, style) in completed:
                print(f"  [{done}/{total_combos}] {inst} / {style.upper()} -> SKIPPED (already done)")
                continue

            print(f"  [{done}/{total_combos}] {inst} / {style.upper()} ...", end=" ", flush=True)

            t0 = time.time()
            result = run_one(inst, style, default_start, end_str, store, adapter, use_twelve,
                             trades_dir=trades_dir)
            elapsed = time.time() - t0

            if result is None:
                print("FAILED")
                continue

            n = result["n_trades"]
            wr = result["win_rate"]
            pnl = result["net_pnl"]
            pf = result["profit_factor"]

            status = f"{n} trades | WR={fmt(wr,'%')} | PnL=${fmt(pnl)} | PF={fmt(pf)} ({elapsed:.1f}s)"
            print(status)

            row = {
                "instrument": inst,
                "style": style,
                "timeframe": STYLE_PARAMS[style]["timeframe"],
                "start_date": result["start_date"],
                "end_date": result["end_date"],
                "n_trades": n,
                "win_rate": wr,
                "net_pnl": pnl,
                "profit_factor": pf,
                "run_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            writer.writerow(row)
            csv_file.flush()
            all_results.append(row)

    csv_file.close()

    # ── Final Summary Table ───────────────────────────────────────────────────
    print(f"\n\n{'='*100}")
    print(f"  FULL UNIVERSE RESULTS SUMMARY")
    print(f"{'='*100}")
    hdr = f"{'Instrument':<14} {'Style':<10} {'TF':<5} {'Trades':>7} {'WinRate':>8} {'Net PnL':>12} {'PF':>6}"
    print(hdr)
    print("-" * 100)

    # Sort by net PnL descending
    all_results.sort(key=lambda x: float(x["net_pnl"] or 0), reverse=True)
    for row in all_results:
        wr_str = f"{row['win_rate']}%" if row["win_rate"] is not None else "N/A"
        pnl_str = f"${row['net_pnl']:,.2f}" if row["net_pnl"] is not None else "N/A"
        pf_str = f"{row['profit_factor']:.2f}" if row["profit_factor"] is not None else "N/A"
        print(f"{row['instrument']:<14} {row['style'].upper():<10} {row['timeframe']:<5} "
              f"{row['n_trades']:>7} {wr_str:>8} {pnl_str:>12} {pf_str:>6}")

    print(f"\n[*] Full results saved to: {csv_path}")

if __name__ == "__main__":
    main()
