"""Is "94% chance of passing" actually a 94% chance of passing? No.

The 94.2% from prop_time_to_pass.py is CONDITIONAL: P(pass | the edge is exactly +0.073R).
It resamples trades from one fixed pool, so it models only the luck of trade ORDER. It
contains no uncertainty about whether +0.073R is the true edge at all.

That estimate comes from 1,694 trades. Its sampling error is not negligible relative to its
size. The honest question is:

    P(pass) = E_over_possible_true_edges [ P(pass | edge) ]

This computes it by nesting the two sources of randomness:
  OUTER: bootstrap the trade pool (edge uncertainty — "which world am I in?")
  INNER: simulate the challenge on that pool (path uncertainty — "how do the trades land?")

The spread of the inner results across outer draws is the part the headline number hides.

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

N_OUTER = 400          # bootstrap worlds (edge uncertainty)
N_INNER = 1500         # paths per world (order uncertainty)
MAX_TRADES = 1200
RISK = 0.010           # the prop config


def sim(pool: np.ndarray, risk: float, target: float, rng, n_paths: int) -> float:
    eq = np.zeros(n_paths)
    active = np.ones(n_paths, dtype=bool)
    passed = np.zeros(n_paths, dtype=bool)
    for _ in range(MAX_TRADES):
        if not active.any():
            break
        eq[active] += rng.choice(pool, size=int(active.sum()), replace=True) * risk
        hit = active & (eq >= target)
        passed[hit] = True
        active = active & ~hit & (eq > FLOOR)
    return float(passed.mean())


def main() -> int:
    r, tpm = book_r_multiples()
    n = len(r)
    se = r.std(ddof=1) / np.sqrt(n)

    print("=" * 86)
    print('DOES "94%" MEAN A 94% CHANCE OF PASSING?')
    print("=" * 86)
    print(f"\nTHE EDGE ESTIMATE ITSELF")
    print(f"  trades                {n}")
    print(f"  mean R                {r.mean():+.4f}")
    print(f"  std of R              {r.std(ddof=1):.4f}")
    print(f"  standard error        {se:.4f}")
    print(f"  95% CI on mean R      [{r.mean()-1.96*se:+.4f}, {r.mean()+1.96*se:+.4f}]")

    rng = np.random.default_rng(42)
    boot_means = np.array([rng.choice(r, size=n, replace=True).mean() for _ in range(20000)])
    print(f"  bootstrap 95% CI      [{np.percentile(boot_means,2.5):+.4f}, "
          f"{np.percentile(boot_means,97.5):+.4f}]")
    print(f"  P(true edge <= 0)     {float((boot_means <= 0).mean())*100:.1f}%")

    print(f"\nNESTED SIMULATION  ({N_OUTER} bootstrap worlds x {N_INNER} paths, risk {RISK*100:.2f}%)")
    rng2 = np.random.default_rng(7)
    per_world = []
    for i in range(N_OUTER):
        pool = rng2.choice(r, size=n, replace=True)
        p1 = sim(pool, RISK, PHASE1, rng2, N_INNER)
        p2 = sim(pool, RISK, PHASE2, rng2, N_INNER)
        per_world.append(p1 * p2)
        if (i + 1) % 100 == 0:
            print(f"  ...{i+1}/{N_OUTER} worlds", flush=True)
    per_world = np.array(per_world)

    print(f"\nRESULT")
    print(f"  CONDITIONAL  P(pass | edge = point estimate)   ~94%   <- the headline number")
    print(f"  UNCONDITIONAL P(pass), averaging over edge      {per_world.mean()*100:.1f}%")
    print(f"\n  spread across worlds you might actually be in:")
    for q in (5, 25, 50, 75, 95):
        print(f"    {q:>2}th pct world -> P(pass) {np.percentile(per_world, q)*100:5.1f}%")
    print(f"\n  P(you are in a world where pass odds < 50%)   "
          f"{float((per_world < 0.50).mean())*100:.1f}%")
    print(f"  P(you are in a world where pass odds < 80%)   "
          f"{float((per_world < 0.80).mean())*100:.1f}%")

    print("\n" + "=" * 86)
    print("The headline models the luck of trade ORDER. It does not model the possibility")
    print("that the edge itself is smaller than measured — and none of this covers the")
    print("bigger risk, which is the backtest not transferring to live at all (the haircut")
    print("run: edge goes NEGATIVE, pass odds ~10%).")
    print("=" * 86)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
