# PRE-REGISTRATION — Expected-Value Slot Allocation & Capacity Expansion Gate

**Date: 2026-07-22**
**Author: APEX Quant Research**
**Status: Pre-registered BEFORE strategy gate run**

## 1. Objective & Hypothesis
The certified trend book (`book_h_gold_252`) baseline Sharpe of 0.863 suffers from a major ordering artifact (`slot_allocation="order"`, spread 0.645 across orderings; true order-independent median 0.515). The `_BUCKET_LIMITS["swing"] = 10` capacity cap vetoed candidate trades 18,147 times in the decade sweep.

Hypothesis:
1. Allocating scarce slots by point-in-time Expected Value ($p \cdot b - (1-p)$) removes arbitrary iteration-order tie-breaks, collapsing ordering spread to EXACTLY 0.000 (true order-invariance).
2. Expanding swing slot capacity from 10 slots to 16 and 20 slots permits high-EV trades that were previously blocked by artificial bucket limits, raising order-invariant Sharpe from 0.586 to 0.835+ without increasing risk per trade ($1.0\%$) or violating the funded prop account drawdown wall ($\le 6.0\%$ annual volatility).

## 2. Pre-Registered Selection Grid (3 Configs = 3 Ledger Charges)
1. `ev_alloc_10_slots`: `slot_allocation="expected_value"`, `max_swing_slots=10` (order-invariant baseline).
2. `ev_alloc_16_slots`: `slot_allocation="expected_value"`, `max_swing_slots=16`.
3. `ev_alloc_20_slots`: `slot_allocation="expected_value"`, `max_swing_slots=20`.

## 3. Data Integrity & Validation Gates
- Iteration Window: Data strictly BEFORE `2025-01-01` (2025+ holdout is locked and never touched).
- Trial Ledger: Record all 3 trials in `data_store/validation/trial_ledger.json` before execution ($N \ge 221$).
- Order-Invariance Audit: Evaluate every config across 6 shuffled instrument orderings (gate order + 5 random permutations) to report median Sharpe and spread.
- Paired Significance: Circular Block Bootstrap ($B=10000$, block=21) and Diebold-Mariano test on return difference vs baseline.
- Deflated Sharpe Ratio (DSR): $> 0.95$ at full deflated trial count ($N = 221$).
- PBO & CPCV: 15 paths, passing OOS Sharpe.
- Determinism: Seed 42, byte-identical twin run.
