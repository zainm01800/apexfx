"""What is the FASTEST route to a funded account — and what does fast cost?

prop_time_to_pass.py reports median months among PASSING paths. That flatters high risk:
it silently ignores the attempts that blow the -8% floor, and a blown attempt costs both the
months already spent and a new fee.

This simulates the thing that actually matters: **expected calendar months until you are
funded**, counting failed attempts and restarts. It also reports what happens AFTER funding,
because passing fast and then losing the account is not passing.

Modelled per attempt: phase1 +8%, phase2 +5%, -8% static floor from phase start.
Funded survival: 12 months at the same risk, account lost if drawdown from peak hits -8%.

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

from prop_time_to_pass import book_r_multiples, FLOOR, PHASE1, PHASE2  # noqa: E402

N_PATHS = 20000
MAX_TRADES_PHASE = 1500
RNG = np.random.default_rng(42)


def run_attempt(pool, risk, target, rng):
    """One phase. Returns (passed_mask, trades_used) — trades_used counts failures too."""
    eq = np.zeros(N_PATHS)
    active = np.ones(N_PATHS, dtype=bool)
    passed = np.zeros(N_PATHS, dtype=bool)
    used = np.full(N_PATHS, MAX_TRADES_PHASE, dtype=int)
    for i in range(MAX_TRADES_PHASE):
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


def expected_months_to_funded(pool, risk, tpm, rng, max_attempts=6):
    """Expected calendar months to FIRST pass, counting failed attempts."""
    months_spent = np.zeros(N_PATHS)
    funded = np.zeros(N_PATHS, dtype=bool)
    attempts = np.zeros(N_PATHS, dtype=int)

    for _ in range(max_attempts):
        todo = ~funded
        if not todo.any():
            break
        p1, u1 = run_attempt(pool, risk, PHASE1, rng)
        p2, u2 = run_attempt(pool, risk, PHASE2, rng)
        # phase 2 only runs where phase 1 passed
        this_pass = p1 & p2
        cost = u1 + np.where(p1, u2, 0)
        months_spent[todo] += cost[todo] / tpm
        attempts[todo] += 1
        funded = funded | (todo & this_pass)
    return months_spent, funded, attempts


def funded_survival(pool, risk, tpm, rng, months=12, dd_limit=0.08):
    eq = np.zeros(N_PATHS)
    peak = np.zeros(N_PATHS)
    alive = np.ones(N_PATHS, dtype=bool)
    for _ in range(int(months * tpm)):
        eq[alive] += rng.choice(pool, size=int(alive.sum()), replace=True) * risk
        peak = np.maximum(peak, eq)
        alive = alive & ((peak - eq) < dd_limit)
    return float(alive.mean()), float(np.median(eq[alive])) if alive.any() else 0.0


def main() -> int:
    r, tpm = book_r_multiples()
    print("=" * 96)
    print("FASTEST ROUTE TO FUNDED — expected months INCLUDING failed attempts")
    print("=" * 96)
    print(f"book: {len(r)} trades, {tpm:.1f}/month, mean R {r.mean():+.3f}")
    print(f"rules: phase1 +{PHASE1*100:.0f}%, phase2 +{PHASE2*100:.0f}%, "
          f"floor {FLOOR*100:.0f}%; funded survival = 12mo, -8% from peak\n")

    print(f"{'risk':>6} {'P(pass 1st)':>12} {'E[months to funded]':>20} "
          f"{'p90 months':>11} {'E[attempts]':>12} {'funded 12mo surv':>17} {'12mo profit':>12}")
    rows = []
    for risk in (0.0060, 0.0075, 0.010, 0.0125, 0.015, 0.020):
        rng = np.random.default_rng(11)
        p1, u1 = run_attempt(r, risk, PHASE1, rng)
        p2, _ = run_attempt(r, risk, PHASE2, rng)
        p_first = float((p1 & p2).mean())

        rng = np.random.default_rng(11)
        months, funded, attempts = expected_months_to_funded(r, risk, tpm, rng)
        e_months = float(months[funded].mean()) if funded.any() else float("nan")
        p90 = float(np.percentile(months[funded], 90)) if funded.any() else float("nan")

        rng = np.random.default_rng(11)
        surv, med_eq = funded_survival(r, risk, tpm, rng)
        rows.append((risk, e_months, surv))
        print(f"{risk*100:5.2f}% {p_first*100:11.1f}% {e_months:19.1f}m {p90:10.1f}m "
              f"{float(attempts[funded].mean()):11.2f} {surv*100:16.1f}% "
              f"{med_eq*100:11.1f}%")

    print("\n" + "=" * 96)
    print("JOINT VIEW — funded AND still alive 12 months later:")
    for risk, e_months, surv in rows:
        print(f"  {risk*100:5.2f}%  ->  funded in ~{e_months:.1f} months, "
              f"then {surv*100:.0f}% still trading a year on  "
              f"=> {surv*100:.0f}% of attempts end in a LIVE funded account")
    print("=" * 96)
    print("Fast is bought with attempts and with account mortality. The 12-month survival")
    print("column is the one that decides whether 'passing' was worth anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
