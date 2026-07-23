"""Faster funding WITHOUT buying it with account mortality: change the CONTRACT, not the risk.

Every previous simulation assumed one product: two phases, +8% then +5% (13% of cumulative
target) behind a -8% floor. Raising risk to shorten that is a bad trade — at 2.0% you are
funded in ~8 months but only 35% of those accounts survive a year.

The target itself is a product choice, not a law. One-step challenges and lower-target firms
exist, and they change the time WITHOUT changing the risk that later kills the account.

Structures modelled (floor -8% throughout, the common static-drawdown contract):
  * 2-step 8+5   — the baseline used so far
  * 1-step 10    — single phase, +10%
  * 1-step 8     — single phase, +8%
  * 1-step 6     — single phase, +6% (lower-target firms)

Reported per (structure x risk): expected calendar months to funded INCLUDING failed attempts,
and 12-month funded survival, because speed is only worth what survives it.

MEASUREMENT ONLY - no ledger charge.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402

from prop_time_to_pass import book_r_multiples, FLOOR  # noqa: E402

N_PATHS = 20000
MAX_TRADES = 1500

STRUCTURES = {
    "2-step 8%+5%": [0.08, 0.05],
    "1-step 10%": [0.10],
    "1-step 8%": [0.08],
    "1-step 6%": [0.06],
}
RISKS = [0.0075, 0.010, 0.0125, 0.015]


def run_phase(pool, risk, target, rng):
    eq = np.zeros(N_PATHS)
    active = np.ones(N_PATHS, dtype=bool)
    passed = np.zeros(N_PATHS, dtype=bool)
    used = np.full(N_PATHS, MAX_TRADES, dtype=int)
    for i in range(MAX_TRADES):
        if not active.any():
            break
        eq[active] += rng.choice(pool, size=int(active.sum()), replace=True) * risk
        hit = active & (eq >= target)
        passed[hit] = True
        used[hit] = i + 1
        blown = active & ~hit & (eq <= FLOOR)
        used[blown] = i + 1
        active = active & ~hit & (eq > FLOOR)
    return passed, used


def months_to_funded(pool, risk, targets, tpm, rng, max_attempts=6):
    months = np.zeros(N_PATHS)
    funded = np.zeros(N_PATHS, dtype=bool)
    for _ in range(max_attempts):
        todo = ~funded
        if not todo.any():
            break
        ok = np.ones(N_PATHS, dtype=bool)
        cost = np.zeros(N_PATHS)
        for tgt in targets:
            p, u = run_phase(pool, risk, tgt, rng)
            cost += np.where(ok, u, 0)
            ok = ok & p
        months[todo] += cost[todo] / tpm
        funded = funded | (todo & ok)
    return months, funded


def survival(pool, risk, tpm, rng, months=12, dd=0.08):
    eq = np.zeros(N_PATHS)
    peak = np.zeros(N_PATHS)
    alive = np.ones(N_PATHS, dtype=bool)
    for _ in range(int(months * tpm)):
        eq[alive] += rng.choice(pool, size=int(alive.sum()), replace=True) * risk
        peak = np.maximum(peak, eq)
        alive = alive & ((peak - eq) < dd)
    return float(alive.mean())


def main() -> int:
    r, tpm = book_r_multiples()
    print("=" * 100)
    print("CHALLENGE STRUCTURE vs RISK — buying speed without buying mortality")
    print("=" * 100)
    print(f"book: {tpm:.1f} trades/month, mean R {r.mean():+.3f}, floor {FLOOR*100:.0f}%\n")
    print(f"{'structure':<16} {'risk':>6} {'E[months]':>10} {'p90':>7} "
          f"{'12mo survival':>14} {'live-account odds':>18}")

    best = []
    for label, targets in STRUCTURES.items():
        for risk in RISKS:
            rng = np.random.default_rng(11)
            m, f = months_to_funded(r, risk, targets, tpm, rng)
            e = float(m[f].mean()) if f.any() else float("nan")
            p90 = float(np.percentile(m[f], 90)) if f.any() else float("nan")
            rng = np.random.default_rng(11)
            s = survival(r, risk, tpm, rng)
            best.append((label, risk, e, s))
            print(f"{label:<16} {risk*100:5.2f}% {e:9.1f}m {p90:6.1f}m "
                  f"{s*100:13.1f}% {s*100:17.1f}%")
        print()

    print("=" * 100)
    print("ROUTES REACHING FUNDED IN <= 9 MONTHS, ranked by 12-month survival:")
    fast = sorted([b for b in best if b[2] <= 9.0], key=lambda x: -x[3])
    if fast:
        for label, risk, e, s in fast[:8]:
            print(f"  {label:<16} at {risk*100:.2f}%  ->  {e:.1f} months, "
                  f"{s*100:.0f}% still trading a year later")
    else:
        print("  none")
    print("\nCompare the baseline: 2-step at 2.00% was 7.9 months at 35% survival.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
