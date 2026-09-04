#!/usr/bin/env python3
"""Calculates comprehensive institutional performance statistics for Book F."""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import test_champion_pyramid_grid as tcpg

blind_universe = tcpg.blind_universe
calendar = tcpg.calendar
cached_atr = tcpg.cached_atr
cached_trend_score = tcpg.cached_trend_score
breadth_series = tcpg.breadth_series
corr_3d = tcpg.corr_3d
tok_to_idx = tcpg.tok_to_idx

def full_stat_audit(base_mrpt=0.0034, max_positions=8, pyr_scale=0.50, seed_capital=100000.0):
    equity = seed_capital
    peak_equity = seed_capital
    prev_close_equity = seed_capital
    positions = {}
    closed_trades = []
    daily_records = []
    
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
                    pos["stop_loss"] = entry_px
                    units = pos["units"]
                    
            # Convexity Pyramiding: At +1.5R, add pyr_scale unit
            if pyr_scale > 0 and pos["partial_taken"] and not pos.get("pyramided", False):
                pyr_target = (entry_px + 1.5 * initial_risk) if is_long else (entry_px - 1.5 * initial_risk)
                pyr_hit = (high_px >= pyr_target) if is_long else (low_px <= pyr_target)
                if pyr_hit:
                    pyr_units = pos["initial_units"] * pyr_scale
                    pos["units"] += pyr_units
                    pos["pyramided"] = True
                    pos["stop_loss"] = (entry_px + 0.75 * initial_risk) if is_long else (entry_px - 0.75 * initial_risk)
                    units = pos["units"]
                    
            if stop_hit:
                diff = (stop_px - entry_px) if is_long else (entry_px - stop_px)
                exit_pnl = diff * units
                total_pnl = pos["realized_pnl"] + exit_pnl
                holding_days = (current_dt - pos["entry_time"]).days
                closed_trades.append({
                    "token": token,
                    "pnl": total_pnl,
                    "win": total_pnl > 0,
                    "entry_date": pos["entry_time"],
                    "exit_date": current_dt,
                    "holding_days": holding_days,
                    "pyramided": pos.get("pyramided", False)
                })
                equity += total_pnl
                closed_this_bar.append(token)
            else:
                atr = cached_atr[token].get(current_dt, 0)
                if atr and atr > 0:
                    cur_gain = (high_px - entry_px) if is_long else (entry_px - low_px)
                    use_trail = 1.4 if cur_gain >= 2.0 * initial_risk else 2.2
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
        day_pnl = cur_total_equity - prev_close_equity
        
        peak_equity = max(peak_equity, cur_total_equity)
        current_dd = (peak_equity - cur_total_equity) / peak_equity
        
        daily_records.append({
            "date": current_dt,
            "equity": cur_total_equity,
            "day_pnl": day_pnl,
            "dd": current_dd
        })
        prev_close_equity = cur_total_equity
        
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
        
        if len(positions) < max_positions:
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
                stop_dist = 1.8 * atr
                units = risk_amount / stop_dist
                stop_loss = (entry_px - stop_dist) if direction == "long" else (entry_px + stop_dist)
                positions[token] = {
                    "direction": direction, "entry_price": entry_px, "entry_time": current_dt,
                    "units": units, "initial_units": units, "stop_loss": stop_loss,
                    "initial_risk": stop_dist, "partial_taken": False, "pyramided": False,
                    "realized_pnl": 0.0, "last_close": entry_px
                }
                
    eq_df = pd.DataFrame(daily_records).set_index("date")
    trades_df = pd.DataFrame(closed_trades)
    
    # Statistical Calculations
    tot_pnl = eq_df["equity"].iloc[-1] - seed_capital
    n_years = len(eq_df) / 252.0
    ann_pnl = tot_pnl / n_years
    mo_payout = ann_pnl / 12.0
    ann_ret = (ann_pnl / seed_capital) * 100.0
    
    pk = eq_df["equity"].cummax()
    max_dd_dollars = (pk - eq_df["equity"]).max()
    max_dd_pct = (max_dd_dollars / seed_capital) * 100.0
    
    worst_day_dollars = eq_df["day_pnl"].min()
    worst_day_pct = (worst_day_dollars / seed_capital) * 100.0
    best_day_dollars = eq_df["day_pnl"].max()
    best_day_pct = (best_day_dollars / seed_capital) * 100.0
    
    # Daily returns for Sharpe / Sortino
    daily_rets = eq_df["day_pnl"] / seed_capital
    sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-9)) * np.sqrt(252)
    downside_rets = daily_rets[daily_rets < 0]
    sortino = (daily_rets.mean() / (downside_rets.std() + 1e-9)) * np.sqrt(252)
    calmar = (ann_ret / max_dd_pct) if max_dd_pct > 0 else 0
    
    wins = trades_df[trades_df["win"]]
    losses = trades_df[~trades_df["win"]]
    win_rate = len(wins) / len(trades_df) * 100
    pf = wins["pnl"].sum() / abs(losses["pnl"].sum())
    
    avg_win = wins["pnl"].mean()
    avg_loss = abs(losses["pnl"].mean())
    payoff_ratio = avg_win / avg_loss
    
    avg_hold_win = wins["holding_days"].mean()
    avg_hold_loss = losses["holding_days"].mean()
    avg_hold_all = trades_df["holding_days"].mean()
    
    # Max Consecutive Losses / Wins
    trades_df["is_win"] = trades_df["win"].astype(int)
    # Consecutive streaks
    win_streak = 0
    loss_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    for w in trades_df["win"]:
        if w:
            win_streak += 1
            loss_streak = 0
            max_win_streak = max(max_win_streak, win_streak)
        else:
            loss_streak += 1
            win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
            
    # Monthly stats
    monthly_eq = eq_df["equity"].resample("ME").last()
    monthly_pnl = monthly_eq.diff().dropna()
    pct_months_pos = (monthly_pnl > 0).mean() * 100
    pct_months_over_800 = (monthly_pnl >= 800).mean() * 100
    pct_months_over_1500 = (monthly_pnl >= 1500).mean() * 100
    median_month = monthly_pnl.median()
    worst_month = monthly_pnl.min()
    best_month = monthly_pnl.max()
    
    # Pyramided trade stats
    pyr_trades = trades_df[trades_df["pyramided"]]
    pyr_win_rate = len(pyr_trades[pyr_trades["win"]]) / len(pyr_trades) * 100 if len(pyr_trades) > 0 else 0
    pyr_avg_pnl = pyr_trades["pnl"].mean() if len(pyr_trades) > 0 else 0
    
    return {
        "tot_pnl": tot_pnl,
        "ann_pnl": ann_pnl,
        "mo_payout": mo_payout,
        "ann_ret": ann_ret,
        "max_dd_dollars": max_dd_dollars,
        "max_dd_pct": max_dd_pct,
        "worst_day_dollars": worst_day_dollars,
        "worst_day_pct": worst_day_pct,
        "best_day_dollars": best_day_dollars,
        "best_day_pct": best_day_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "total_trades": len(trades_df),
        "trades_per_month": len(trades_df) / (n_years * 12.0),
        "win_rate": win_rate,
        "pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_hold_win": avg_hold_win,
        "avg_hold_loss": avg_hold_loss,
        "avg_hold_all": avg_hold_all,
        "pct_months_pos": pct_months_pos,
        "pct_months_over_800": pct_months_over_800,
        "pct_months_over_1500": pct_months_over_1500,
        "median_month": median_month,
        "worst_month": worst_month,
        "best_month": best_month,
        "pyr_trades_count": len(pyr_trades),
        "pyr_win_rate": pyr_win_rate,
        "pyr_avg_pnl": pyr_avg_pnl
    }

if __name__ == "__main__":
    for pyr, r, name in [
        (0.35, 0.0034, "High-Yield Institutional (Pyr 0.35x, Risk 0.34%)"),
        (0.50, 0.0034, "Elite Convex Accelerator (Pyr 0.50x, Risk 0.34%)")
    ]:
        s = full_stat_audit(base_mrpt=r, max_positions=8, pyr_scale=pyr, seed_capital=100000.0)
        print("=" * 80)
        print(f"STRATEGY STATS: {name}")
        print("=" * 80)
        print(f"Capital Base:             $100,000.00 (Fixed Prop Firm Standard)")
        print(f"Historical Horizon:       2016-01-01 to 2026-08-27 (10.6 Years / 3,820 Days)")
        print(f"Total Cumulative Profit:  ${s['tot_pnl']:,.2f}")
        print(f"Annualized Net Profit:    ${s['ann_pnl']:,.2f} / year (+{s['ann_ret']:.2f}% / yr)")
        print(f"Average Monthly Payout:   ${s['mo_payout']:,.2f} / month")
        print(f"Median Monthly P&L:       ${s['median_month']:,.2f} / month")
        print(f"Best Month / Worst Month: +${s['best_month']:,.2f} / ${s['worst_month']:,.2f}")
        print(f"% Months Positive:        {s['pct_months_pos']:.1f}%")
        print(f"% Months > $800:          {s['pct_months_over_800']:.1f}%")
        print(f"% Months > $1,500:        {s['pct_months_over_1500']:.1f}%")
        print("-" * 80)
        print(f"10-Year Max Drawdown:     ${s['max_dd_dollars']:,.2f} ({s['max_dd_pct']:.2f}%)  [Limit: 10.0% / $10,000]")
        print(f"Drawdown Safety Buffer:   ${10000 - s['max_dd_dollars']:,.2f} remaining cushion")
        print(f"Worst Single Day Loss:    ${s['worst_day_dollars']:,.2f} ({s['worst_day_pct']:.2f}%)  [Limit: -5.0% / -$5,000]")
        print(f"Daily Loss Safety Buffer: ${5000 + s['worst_day_dollars']:,.2f} remaining cushion")
        print(f"Best Single Day Gain:     +${s['best_day_dollars']:,.2f} (+{s['best_day_pct']:.2f}%)")
        print("-" * 80)
        print(f"Sharpe Ratio (Annualized):{s['sharpe']:.2f}")
        print(f"Sortino Ratio:            {s['sortino']:.2f}")
        print(f"Calmar Ratio (Return/DD): {s['calmar']:.2f}")
        print(f"Profit Factor (PF):       {s['pf']:.2f}")
        print(f"Win Rate (%):             {s['win_rate']:.1f}%")
        print(f"Total Trades:             {s['total_trades']:,} ({s['trades_per_month']:.1f} trades / month)")
        print(f"Average Win / Avg Loss:   ${s['avg_win']:,.2f} / ${s['avg_loss']:,.2f}")
        print(f"Payoff Ratio (Win/Loss):  {s['payoff_ratio']:.2f}x")
        print(f"Max Win / Loss Streak:    {s['max_win_streak']} wins / {s['max_loss_streak']} losses")
        print(f"Average Holding Period:   {s['avg_hold_all']:.1f} days (Wins: {s['avg_hold_win']:.1f}d | Losses: {s['avg_hold_loss']:.1f}d)")
        print(f"Pyramided Runners:        {s['pyr_trades_count']} trades ({s['pyr_win_rate']:.1f}% win rate, Avg PnL: ${s['pyr_avg_pnl']:,.2f})")
        print("=" * 80 + "\n")
