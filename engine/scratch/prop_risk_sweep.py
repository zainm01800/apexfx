"""Monte Carlo: prop two-step challenge pass odds vs per-trade risk.

Trade distribution matched to Book H gold gate (2026-07-19): 55% win rate,
expectancy ~+0.3R, positive skew (trend book: small losses, mixed winners).
  45% -> -1.0R (stopped) | 40% -> +0.9R (managed exit small win) | 15% -> +2.8R (runner)
  expectancy = -0.45 + 0.36 + 0.42 = +0.33R (haircut applies separately)
Rules: static floor -8% from start balance, phase-1 target +8%, phase-2 +5%.
Daily-loss rule omitted: at <=1.5% risk and ~0.7 trades/day it cannot bind
(max day loss ~2x1.5%=3% < 4-5% limit) -- matches prior MC finding.
Trades/month = 1557 trades / 108 months = 14.4 (Book H gold gate).
"""
import numpy as np

RNG = np.random.default_rng(42)
TRADES_PER_MONTH = 14.4
N_PATHS = 20000


def trade_outcomes(n, rng, profile="bt"):
    u = rng.random(n)
    if profile == "bt":
        # Book H gold gate stats: 55% win, +0.33R expectancy
        return np.where(u < 0.45, -1.0, np.where(u < 0.85, 0.9, 2.8))
    # "haircut": ~50% edge degradation (backtest -> live), 55% win, +0.165R
    return np.where(u < 0.45, -1.0, np.where(u < 0.86, 0.75, 2.2))


def sweep(profile):
    print(f"\n--- profile: {profile} ---")
    print(f"{'risk':>6} {'P(p1)':>7} {'P(p2)':>7} {'P(pass)':>8} {'months':>7} {'2-att%':>7} {'fundSurv12mo':>12} {'avg mo%':>8}")
    for risk in (0.005, 0.0075, 0.010, 0.0125, 0.015):
        p1, t1 = run_phase(risk, 0.08, profile=profile)
        p2, t2 = run_phase(risk, 0.05, profile=profile)
        p_pass = p1 * p2
        months = (t1 + t2) / TRADES_PER_MONTH if t1 == t1 else float("nan")
        surv, mo = run_funded(risk, profile=profile)
        cum2 = 1 - (1 - p_pass) ** 2
        print(f"{risk*100:5.2f}% {p1*100:6.1f}% {p2*100:6.1f}% {p_pass*100:7.1f}% {months:6.1f}m {cum2*100:6.1f}% {surv*100:11.1f}% {mo*100:7.1f}%")


def run_phase(risk, target, floor=-0.08, max_trades=400, rng=RNG, profile="bt"):
    eq = np.zeros(N_PATHS)
    active = np.ones(N_PATHS, dtype=bool)
    passed = np.zeros(N_PATHS, dtype=bool)
    t_pass = np.full(N_PATHS, max_trades, dtype=int)
    for t in range(max_trades):
        r = trade_outcomes(N_PATHS, rng, profile) * risk
        eq = np.where(active, eq + r, eq)
        bust = active & (eq <= floor)
        active &= ~bust
        new_pass = active & (eq >= target)
        t_pass = np.where(new_pass & ~passed, t + 1, t_pass)
        passed |= new_pass
        active &= ~new_pass
        if not active.any():
            break
    med_trades = float(np.median(t_pass[passed])) if passed.any() else float("nan")
    return float(passed.mean()), med_trades


def run_funded(risk, floor=-0.08, months=12, rng=RNG, profile="bt"):
    """12 months on the funded account, same static floor; survival + mean monthly P&L."""
    n_trades = int(round(months * TRADES_PER_MONTH))
    eq = np.zeros(N_PATHS)
    alive = np.ones(N_PATHS, dtype=bool)
    for _ in range(n_trades):
        r = trade_outcomes(N_PATHS, rng, profile) * risk
        eq = np.where(alive, eq + r, eq)
        alive &= eq > floor
    survivors = alive
    monthly_ret = np.where(survivors, eq / months, np.nan)
    return float(survivors.mean()), float(np.nanmean(monthly_ret))


sweep("bt")
sweep("haircut")
