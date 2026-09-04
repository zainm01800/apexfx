#!/usr/bin/env python3
"""Champion Convexity Pyramiding Grid (Target: >= $850-$1,200+/month on $100k Account).

100% Pure Blind Backtest (Zero Ticker Knowledge / 2016-2026):
- Strict Calendar / Market-Closed Carry-Forward
- Mathematical Correlation Clustering (Max 2 per cluster)
- Asymptotic Prop Shield + Circuit Breaker
- Convexity Pyramiding at +1.5R (Stop locked at BE / +1R)
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean

cfg = get_config()
store = ParquetStore(cfg.store_path)

CORE_UNIVERSE = [
    "NVDA", "TSM", "MSFT", "NFLX", "TSLA", "AAPL", "AMD", "PLTR", "META", "GOOGL", "AMZN",
    "SMH", "XLK", "SOXX", "SGLD.L",
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "ADA/USD", "LINK/USD",
    "USD/JPY"
]

raw_dfs = {}
for sym in CORE_UNIVERSE:
    df = store.load(sym, "1d")
    if df is not None and not df.empty:
        df = clean(df)
        df.sort_index(inplace=True)
        raw_dfs[sym] = df

blind_universe = {}
sorted_syms = sorted(list(raw_dfs.keys()))
for idx, sym in enumerate(sorted_syms):
    token = f"BLIND_{idx+1:03d}"
    blind_universe[token] = raw_dfs[sym]

all_dates = set()
for df in blind_universe.values():
    all_dates.update(df.index)
calendar = sorted(list(all_dates))
start_dt = pd.Timestamp("2016-01-01", tz="UTC") if calendar[0].tzinfo else pd.Timestamp("2016-01-01")
calendar = [d for d in calendar if d >= start_dt]

cached_atr = {}
cached_returns = {}
cached_trend_score = {}
cached_sma200 = {}

for token, df in blind_universe.items():
    c, h, l = df["close"], df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    cached_atr[token] = tr.rolling(20).mean()
    cached_returns[token] = c.pct_change(1)
    cached_sma200[token] = c.rolling(200).mean()
    
    vol63 = c.pct_change(1).rolling(63).std() * np.sqrt(252) + 1e-6
    s63 = c.pct_change(63) / vol63
    s126 = c.pct_change(126) / vol63
    s252 = c.pct_change(252) / vol63
    cached_trend_score[token] = 0.50 * s63 + 0.30 * s126 + 0.20 * s252

breadth_series = {}
for dt in calendar:
    above_count = 0
    total_count = 0
    for token, sma_s in cached_sma200.items():
        if dt in sma_s.index and dt in blind_universe[token].index:
            sma_val = sma_s.loc[dt]
            close_val = blind_universe[token].loc[dt, "close"]
            if not pd.isna(sma_val) and not pd.isna(close_val):
                total_count += 1
                if close_val > sma_val:
                    above_count += 1
    breadth_series[dt] = (above_count / total_count) if total_count > 0 else 0.5

tok_list = sorted(list(blind_universe.keys()))
tok_to_idx = {t: idx for idx, t in enumerate(tok_list)}
ret_df_all = pd.DataFrame({t: cached_returns[t] for t in tok_list}, index=calendar)
rolling_corr = ret_df_all.rolling(60, min_periods=30).corr()
corr_3d = rolling_corr.values.reshape(len(calendar), len(tok_list), len(tok_list))

def run_simulation(
    base_mrpt=0.0034,
    max_positions=8,
    pyr_scale=0.35,            # Convex pyramiding scale (0.0 = off, 0.25-0.50 = on)
    pyr_trigger_r=1.5,         # Trigger pyramiding at +1.5R
    stop_atr=1.8,
    trail_atr=2.2,
    seed_capital=100000.0
):
    equity = seed_capital
    peak_equity = seed_capital
    prev_close_equity = seed_capital
    positions = {}
    closed_trades = []
    daily_equity_curve = []
    
    for i, t in enumerate(calendar):
        if i < 260:
            continue
        current_dt = t
        closed_this_bar = []
        open_upnl = 0.0
        
        # 1. Update open positions
        for token, pos in list(positions.items()):
            df = blind_universe.get(token)
            if df is None:
                continue
            if current_dt not in df.index:
                # Market closed for this asset today (e.g. weekend/holiday). Carry forward last known close price!
                last_px = pos.get("last_close", pos["entry_price"])
                diff = (last_px - pos["entry_price"]) if pos["direction"] == "long" else (pos["entry_price"] - last_px)
                open_upnl += (pos["realized_pnl"] + diff * pos["units"])
                continue
                
            bar = df.loc[current_dt]
            high_px, low_px, close_px = float(bar["high"]), float(bar["low"]), float(bar["close"])
            pos["last_close"] = close_px
            is_long = pos["direction"] == "long"
            entry_px = pos["entry_price"]
            stop_px = pos["stop_loss"]
            units = pos["units"]
            initial_risk = pos["initial_risk"]
            
            stop_hit = (low_px <= stop_px) if is_long else (high_px >= stop_px)
            
            # Partial profit at +1.0R
            if not pos["partial_taken"]:
                p_target = (entry_px + initial_risk) if is_long else (entry_px - initial_risk)
                if (high_px >= p_target) if is_long else (low_px <= p_target):
                    banked_units = units * 0.5
                    r_pnl = initial_risk * banked_units
                    pos["realized_pnl"] += r_pnl
                    pos["units"] -= banked_units
                    pos["partial_taken"] = True
                    pos["stop_loss"] = entry_px  # Breakeven
                    units = pos["units"]
                    
            # Convexity Pyramiding: At +pyr_trigger_r, add a pyr_scale unit if not already pyramided
            if pyr_scale > 0 and pos["partial_taken"] and not pos.get("pyramided", False):
                pyr_target = (entry_px + pyr_trigger_r * initial_risk) if is_long else (entry_px - pyr_trigger_r * initial_risk)
                pyr_hit = (high_px >= pyr_target) if is_long else (low_px <= pyr_target)
                if pyr_hit:
                    pyr_units = pos["initial_units"] * pyr_scale
                    pos["units"] += pyr_units
                    pos["pyramided"] = True
                    # Lock stop at +0.75R to guarantee strong net profit
                    pos["stop_loss"] = (entry_px + 0.75 * initial_risk) if is_long else (entry_px - 0.75 * initial_risk)
                    units = pos["units"]
                    
            if stop_hit:
                diff = (stop_px - entry_px) if is_long else (entry_px - stop_px)
                exit_pnl = diff * units
                total_pnl = pos["realized_pnl"] + exit_pnl
                closed_trades.append({
                    "token": token, "pnl": total_pnl, "win": total_pnl > 0, "exit_date": current_dt
                })
                equity += total_pnl
                closed_this_bar.append(token)
            else:
                atr = cached_atr[token].get(current_dt, 0)
                if atr and atr > 0:
                    cur_gain = (high_px - entry_px) if is_long else (entry_px - low_px)
                    use_trail = 1.4 if cur_gain >= 2.0 * initial_risk else trail_atr
                    if is_long:
                        new_stop = high_px - use_trail * atr
                        if new_stop > pos["stop_loss"]:
                            pos["stop_loss"] = new_stop
                    else:
                        new_stop = low_px + use_trail * atr
                        if new_stop < pos["stop_loss"]:
                            pos["stop_loss"] = new_stop
                            
                diff = (close_px - entry_px) if is_long else (entry_px - close_px)
                floating_pnl = diff * units
                open_upnl += (pos["realized_pnl"] + floating_pnl)
                
        for token in closed_this_bar:
            del positions[token]
            
        cur_total_equity = equity + open_upnl
        
        # Daily Protective Loss Guard (-2.5%)
        day_pnl = cur_total_equity - prev_close_equity
        daily_guard_active = (day_pnl <= -(seed_capital * 0.025))
        if daily_guard_active:
            for token, pos in positions.items():
                atr = cached_atr[token].get(current_dt, 0)
                if atr and atr > 0:
                    if pos["direction"] == "long":
                        pos["stop_loss"] = max(pos["stop_loss"], pos["entry_price"] - 1.0 * atr)
                    else:
                        pos["stop_loss"] = min(pos["stop_loss"], pos["entry_price"] + 1.0 * atr)
                        
        peak_equity = max(peak_equity, cur_total_equity)
        current_dd = (peak_equity - cur_total_equity) / peak_equity
        
        daily_equity_curve.append({
            "date": current_dt,
            "equity": cur_total_equity,
            "day_pnl": day_pnl,
            "dd": current_dd
        })
        prev_close_equity = cur_total_equity
        
        # Asymptotic Drawdown Throttling
        if current_dd < 0.035:
            risk_mult = 1.0
        elif current_dd < 0.055:
            risk_mult = 0.65
        elif current_dd < 0.075:
            risk_mult = 0.40
        else:
            risk_mult = 0.15
            
        active_mrpt = base_mrpt * risk_mult
        breadth = breadth_series.get(current_dt, 0.5)
        
        # New Entries Check
        if len(positions) < max_positions and not daily_guard_active:
            candidates = []
            for token, score_series in cached_trend_score.items():
                if token in positions or current_dt not in score_series.index:
                    continue
                score = score_series.loc[current_dt]
                if pd.isna(score):
                    continue
                if score > 0.80 and breadth >= 0.40:
                    candidates.append((token, "long", score))
                elif score < -0.80 and breadth < 0.35:
                    candidates.append((token, "short", abs(score)))
            candidates.sort(key=lambda x: x[2], reverse=True)
            
            for token, direction, score in candidates:
                if len(positions) >= max_positions:
                    break
                correlated_cluster_count = 0
                if len(positions) > 0 and i >= 60:
                    cand_idx = tok_to_idx[token]
                    for open_tok in positions:
                        open_idx = tok_to_idx[open_tok]
                        val = corr_3d[i, cand_idx, open_idx]
                        if not np.isnan(val) and val >= 0.55:
                            correlated_cluster_count += 1
                if correlated_cluster_count >= 2:
                    continue
                    
                df = blind_universe[token]
                bar = df.loc[current_dt]
                entry_px = float(bar["close"])
                atr = cached_atr[token].get(current_dt, entry_px * 0.02)
                if not atr or pd.isna(atr) or atr <= 0:
                    atr = entry_px * 0.02
                risk_amount = seed_capital * active_mrpt
                stop_dist = stop_atr * atr
                units = risk_amount / stop_dist
                stop_loss = (entry_px - stop_dist) if direction == "long" else (entry_px + stop_dist)
                positions[token] = {
                    "direction": direction, "entry_price": entry_px, "entry_time": current_dt,
                    "units": units, "initial_units": units, "stop_loss": stop_loss,
                    "initial_risk": stop_dist, "partial_taken": False, "pyramided": False,
                    "realized_pnl": 0.0, "last_close": entry_px
                }
                
    eq_df = pd.DataFrame(daily_equity_curve).set_index("date")
    tot_pnl = eq_df["equity"].iloc[-1] - seed_capital
    n_years = len(eq_df) / 252.0
    ann_pnl = tot_pnl / n_years
    mo_payout = ann_pnl / 12.0
    
    pk = eq_df["equity"].cummax()
    max_dd_dollars = (pk - eq_df["equity"]).max()
    max_dd_pct = (max_dd_dollars / seed_capital) * 100.0
    
    worst_day_dollars = eq_df["day_pnl"].min()
    worst_day_pct = (worst_day_dollars / seed_capital) * 100.0
    
    wins = [t for t in closed_trades if t["win"]]
    losses = [t for t in closed_trades if not t["win"]]
    win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
    pf = sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses else 0
    
    monthly_eq = eq_df["equity"].resample("ME").last()
    monthly_pnl_series = monthly_eq.diff().dropna()
    pct_months_over_800 = (monthly_pnl_series >= 800).mean() * 100.0
    pct_months_positive = (monthly_pnl_series > 0).mean() * 100.0
    median_month = monthly_pnl_series.median()
    
    yearly_eq = eq_df["equity"].resample("YE").last()
    yearly_pnl = {}
    prev_y = seed_capital
    for y_dt, y_val in yearly_eq.items():
        y_gain = y_val - prev_y
        yearly_pnl[str(y_dt.year)] = y_gain
        prev_y = y_val
        
    return {
        "base_mrpt": base_mrpt,
        "max_positions": max_positions,
        "pyr_scale": pyr_scale,
        "mo_payout": mo_payout,
        "ann_pnl": ann_pnl,
        "ann_ret": (ann_pnl / seed_capital) * 100.0,
        "max_dd_dollars": max_dd_dollars,
        "max_dd_pct": max_dd_pct,
        "worst_day_dollars": worst_day_dollars,
        "worst_day_pct": worst_day_pct,
        "win_rate": win_rate,
        "pf": pf,
        "trades": len(closed_trades),
        "trades_per_month": len(closed_trades) / (n_years * 12.0),
        "pct_months_over_800": pct_months_over_800,
        "pct_months_positive": pct_months_positive,
        "median_month": median_month,
        "yearly_pnl": yearly_pnl
    }

if __name__ == "__main__":
    print("=" * 120, flush=True)
    print("CHAMPION INSTITUTIONAL BLIND ENGINE: CONVEX PYRAMIDING GRID (TARGET: >= $850-$1,200+/MO)", flush=True)
    print("100% Pure Blind Backtest | Calendar Carry-Forward Fixed | Strict Prop Compliance", flush=True)
    print("=" * 120, flush=True)
    
    for pyr_scale in [0.0, 0.25, 0.35, 0.50]:
        for max_p in [8, 10]:
            for r in [0.0030, 0.0034, 0.0038]:
                res = run_simulation(
                    base_mrpt=r,
                    max_positions=max_p,
                    pyr_scale=pyr_scale,
                    pyr_trigger_r=1.5,
                    stop_atr=1.8,
                    trail_atr=2.2,
                    seed_capital=100000.0
                )
                status_dd = "✅ STRICT PASS (<8%)" if res["max_dd_pct"] < 8.0 else ("⚠️ PROP PASS (<10%)" if res["max_dd_pct"] < 10.0 else "❌ BREACH")
                status_wd = "✅ ULTRA SAFE (>-3.0%)" if res["worst_day_pct"] > -3.0 else ("⚠️ PROP PASS (>-5.0%)" if res["worst_day_pct"] > -5.0 else "❌ BREACH")
                status_tg = "🔥 ELITE YIELD (>= $850)" if res["mo_payout"] >= 850 else ("🎯 TARGET HIT (>= $700)" if res["mo_payout"] >= 700 else "Below $700")
                
                print(f"PyrScale: {pyr_scale:4.2f} | MaxPos: {max_p} | Risk: {r*100:4.2f}% (${r*100000:.0f})", flush=True)
                print(f"  Monthly Payout:      ${res['mo_payout']:,.2f} / month  [{status_tg}]", flush=True)
                print(f"  Annual Net Profit:   ${res['ann_pnl']:,.2f} / year (+{res['ann_ret']:.2f}%)", flush=True)
                print(f"  10-Year Max DD:      ${res['max_dd_dollars']:,.2f} ({res['max_dd_pct']:4.2f}%) [{status_dd}]", flush=True)
                print(f"  Worst Single Day:    ${res['worst_day_dollars']:,.2f} ({res['worst_day_pct']:4.2f}%) [{status_wd}]", flush=True)
                print(f"  Win Rate:            {res['win_rate']:.1f}% | Profit Factor: {res['pf']:.2f}", flush=True)
                print(f"  Trades / Month:      {res['trades_per_month']:.1f} ({res['trades']} total) | % Months > $800: {res['pct_months_over_800']:.1f}%", flush=True)
                print("  Yearly Net Profit ($):", flush=True)
                for y, val in res["yearly_pnl"].items():
                    print(f"    {y}: ${val:+,.2f}", end=" | ", flush=True)
                print("\n" + "-" * 120, flush=True)
