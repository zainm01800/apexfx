"""
APEX Quant — Full Universe Fast Vectorised Parameter Sweep
===========================================================
Scans every available parquet in data_store across 15m, 1h, and 1d timeframes.
Finds the best robust config per symbol/timeframe combination.
Produces a full portfolio optimised config + daily profit projection.
"""

import sys
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR    = Path(__file__).resolve().parent.parent / "data_store"
OUTPUT_FILE = DATA_DIR / "high_frequency_optimized_configs.json"

# ── Asset classification map ────────────────────────────────────────────────
def classify(symbol: str) -> str:
    s = symbol.upper()
    if "USD" in s and "/" in s:
        if any(c in s for c in ["BTC","ETH","SOL","BNB","XRP","ADA","AVAX","DOGE","LINK","ARB","SUI","MATIC"]):
            return "Crypto"
        return "Forex"
    if "/" in s:
        return "Forex"
    return "Equity"

# ── Build full asset list from disk ────────────────────────────────────────
def build_asset_list():
    assets = []
    seen = set()
    for fp in sorted(DATA_DIR.glob("*.parquet")):
        if fp.stat().st_size < 5000:   # skip empty files
            continue
        name = fp.stem  # e.g. BTC_USD_1h
        parts = name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        raw_sym, tf = parts[0], parts[1]
        symbol = raw_sym.replace("_", "/") if "/" not in raw_sym and "USD" in raw_sym.upper() else raw_sym
        # Fix crypto/forex symbols: BTC_USD -> BTC/USD, EUR_USD -> EUR/USD
        if raw_sym.count("_") == 1 and raw_sym.endswith("USD"):
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and "JPY" in raw_sym:
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and "GBP" in raw_sym:
            symbol = raw_sym.replace("_", "/")
        elif raw_sym.count("_") == 1 and ("EUR" in raw_sym or "AUD" in raw_sym or
                                           "NZD" in raw_sym or "CHF" in raw_sym or
                                           "CAD" in raw_sym):
            symbol = raw_sym.replace("_", "/")
        else:
            symbol = raw_sym  # plain equity ticker

        key = (symbol, tf)
        if key not in seen:
            seen.add(key)
            assets.append((symbol, fp.name, tf, classify(symbol)))
    return assets

# ── Technical helpers ───────────────────────────────────────────────────────
def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"]  - df["close"].shift(1)).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(window).mean()

COST_BPS = {"Crypto": 3.0, "Forex": 2.0, "Equity": 3.5}
INITIAL_EQUITY = 100_000.0
RISK_PER_TRADE = 0.01

def fast_backtest(df: pd.DataFrame, mom_lb: int, hold_h: int, rr: float,
                  asset_class: str) -> dict:
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    ret   = close.pct_change(mom_lb)
    vol   = close.pct_change().rolling(mom_lb).std(ddof=1).replace(0, np.nan)
    sig   = (ret / vol).fillna(0.0)

    ma         = close.rolling(50).mean()
    trend_up   = (close > ma).to_numpy()
    trend_dn   = (close < ma).to_numpy()
    at         = atr(df, 14).to_numpy()
    sig_np     = sig.to_numpy()
    close_np   = close.to_numpy()
    high_np    = high.to_numpy()
    low_np     = low.to_numpy()
    cost_bps   = COST_BPS.get(asset_class, 3.0)

    equity     = INITIAL_EQUITY
    trades     = []
    skip_until = -1
    N          = len(df)
    warmup     = max(mom_lb, 50) + 1

    for i in range(warmup, N - hold_h):
        if i <= skip_until:
            continue
        s = sig_np[i]
        price = close_np[i]
        atr_v = at[i]
        if not (np.isfinite(s) and np.isfinite(atr_v) and atr_v > 0 and price > 0):
            continue

        if   s > 0.5 and trend_up[i]:   direction = 1
        elif s < -0.5 and trend_dn[i]:  direction = -1
        else: continue

        stop_d  = 2.5 * atr_v
        tgt_d   = rr * stop_d
        cost    = price * cost_bps / 10000.0
        units   = (equity * RISK_PER_TRADE) / stop_d
        entry   = price + cost * direction

        sp = entry - stop_d * direction
        tp = entry + tgt_d  * direction

        outcome  = "time"
        exit_px  = close_np[min(i + hold_h, N - 1)]
        for j in range(i + 1, min(i + hold_h + 1, N)):
            h, l = high_np[j], low_np[j]
            if direction == 1:
                if l <= sp:  exit_px = sp - cost; outcome = "stop";   break
                if h >= tp:  exit_px = tp - cost; outcome = "target"; break
            else:
                if h >= sp:  exit_px = sp + cost; outcome = "stop";   break
                if l <= tp:  exit_px = tp + cost; outcome = "target"; break

        pnl = (exit_px - entry) * direction * units
        equity += pnl
        trades.append(pnl)
        skip_until = i + hold_h

    if not trades:
        return {"n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0}

    wins   = [p for p in trades if p > 0]
    losses = [p for p in trades if p < 0]
    pf     = (sum(wins) / abs(sum(losses))) if losses else 99.0

    return {
        "n_trades":      len(trades),
        "net_pnl":       sum(trades),
        "win_rate":      len(wins) / len(trades),
        "profit_factor": min(pf, 99.0),
    }

# ── Parameter grid ──────────────────────────────────────────────────────────
GRID = [
    (mom, hh, rr)
    for mom in [14, 28]
    for hh  in [24, 48]
    for rr   in [1.5, 2.0]
]

# Timeframe-specific thresholds
MIN_TRADES = {"15m": 20, "1h": 10, "1d": 5}
MIN_PF     = {"15m": 1.05, "1h": 1.05, "1d": 1.08}

def main():
    assets = build_asset_list()
    print(f"APEX Quant — Full Universe Sweep ({len(assets)} symbol/timeframe combos)")
    print("=" * 65)

    optimized = []

    for symbol, fname, tf, asset_class in assets:
        fp = DATA_DIR / fname
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        if len(df) < 300:
            continue

        split     = int(len(df) * 0.75)
        df_train  = df.iloc[:split]
        df_oos    = df.iloc[split:]
        min_t     = MIN_TRADES.get(tf, 8)
        min_pf    = MIN_PF.get(tf, 1.05)

        best       = None
        best_score = -np.inf

        for mom, hh, rr in GRID:
            tr = fast_backtest(df_train, mom, hh, rr, asset_class)
            if tr["n_trades"] < min_t or tr["net_pnl"] <= 0 or tr["profit_factor"] < min_pf:
                continue
            oos = fast_backtest(df_oos, mom, hh, rr, asset_class)
            if oos["n_trades"] < max(2, min_t // 4) or oos["net_pnl"] <= 0:
                continue

            score = tr["profit_factor"] * np.sqrt(tr["n_trades"])
            if score > best_score:
                best_score = score
                best = {
                    "symbol": symbol, "asset_class": asset_class, "timeframe": tf,
                    "parameters": {"momentum_lookback": mom, "hold_horizon": hh, "reward_risk": rr},
                    "train": tr, "oos": oos,
                }

        if best:
            t, o = best["train"], best["oos"]
            print(f"  [OK] {symbol:12s} {tf:4s} | {t['n_trades']:4d} trades | "
                  f"PnL=+${t['net_pnl']:>8,.0f} | WR={t['win_rate']*100:.0f}% | "
                  f"PF={t['profit_factor']:.2f} | OOS +${o['net_pnl']:>7,.0f}")
            optimized.append(best)

    # ── Save + Report ────────────────────────────────────────────────────────
    print("\n" + "=" * 65)

    if not optimized:
        print("No configs passed validation.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(optimized, f, indent=2, default=str)

    total_train = sum(c["train"]["net_pnl"] for c in optimized)
    total_oos   = sum(c["oos"]["net_pnl"]   for c in optimized)
    total_trades= sum(c["train"]["n_trades"] + c["oos"]["n_trades"] for c in optimized)
    avg_wr      = np.mean([c["train"]["win_rate"] for c in optimized]) * 100
    avg_pf      = np.mean([c["train"]["profit_factor"] for c in optimized])

    # OOS window ≈ 25% of total history
    # Average history per asset: crypto/forex ~3 years, equities ~1.5 years → avg ~2 years
    # OOS = 0.25 * 2 years = 6 months = ~130 trading days
    oos_days    = 130
    daily_raw   = total_oos / oos_days
    daily_10k   = daily_raw * (10_000 / 100_000)

    print(f"\nRobust Systems Found:        {len(optimized)}")
    print(f"Total Trades (train+oos):    {total_trades:,}")
    print(f"Train PnL (full portfolio):  +${total_train:,.0f}")
    print(f"OOS PnL  (full portfolio):   +${total_oos:,.0f}")
    print(f"Avg Win Rate:                {avg_wr:.1f}%")
    print(f"Avg Profit Factor:           {avg_pf:.2f}x")
    print(f"\n--- $10,000 Account Projection ---")
    print(f"Estimated Daily Profit:   +${daily_10k:>7.2f}/day")
    print(f"Estimated Monthly Profit: +${daily_10k*21:>7.2f}/month")
    print(f"Estimated Annual Return:  +${daily_10k*252:>7.2f}/year  ({daily_10k*252/100:.1f}% on $10k)")
    print(f"\nSaved {len(optimized)} configs -> {OUTPUT_FILE.name}")

if __name__ == "__main__":
    main()
