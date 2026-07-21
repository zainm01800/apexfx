# Book I gate — REJECT ALL FOUR / ADOPT NOTHING (2026-07-20)

**The verdict first: no universe change is adopted.** All four configs — including the
certified baseline re-run — fail the shared PBO leg (0.602 ≥ 0.5). Per the binding prereg
decision rule (`book_i_prereg.md` §4): adopt nothing. The certified Book H + gold stands.

| Config | Sharpe | maxDD | DSR (n=208) | CPCV med / %+ | PBO leg | Verdict |
|---|---|---|---|---|---|---|
| book_h_gold_252 (baseline) | 1.03 | 15.8% | 1.000 ✓ | 0.058 / 100% ✓ | 0.602 ✗ | REJECT |
| book_i_prune_252 (−XLE −ISWD) | 1.00 | 16.6% | 0.999 ✓ | 0.063 / 100% ✓ | 0.602 ✗ | REJECT |
| book_i_exp_252 (+18 names) | 1.05 | **13.3%** | 1.000 ✓ | 0.062 / 100% ✓ | 0.602 ✗ | REJECT |
| book_i_exp_prune_252 | 0.99 | 15.9% | 1.000 ✓ | 0.061 / 100% ✓ | 0.602 ✗ | REJECT |

## Honest reading
1. **DSR and CPCV pass everywhere** (all 15 paths positive on every config). The rejection is
   entirely the set-level PBO: among four near-identical overlapping books, the in-sample
   winner's OOS rank is unstable — the pre-registered caveat (§5 of the prereg and of the Book
   H prereg before it) materialised at 4 configs where it didn't at 3. PBO here is telling us
   "you cannot claim the *best* of these four is reliably best," which is true — and the rule
   converts that into adopt-nothing rather than letting us pick the prettiest.
2. **H-prune is evidentially DEAD**: removing the two documented losers (XLE −£18.6k,
   ISWD.L −£8.1k) made the book *worse* (Sharpe 1.03→1.00, maxDD 15.8→16.6%) — the losers'
   losses were already paid inside the certified curve; removing them also removed diversification.
   Do not re-propose.
3. **H-exp is directionally interesting but unproven**: +18 halal names → Sharpe 1.05 with
   maxDD down to 13.3% (breadth doing what breadth should) — but it did NOT clear the set-level
   gate, so it is a hypothesis for a future cleaner experiment (2 configs, not 4), not a result.
4. **Reproduction drift, reported**: the certified 2026-07-19 baseline (Sharpe 1.086, +510%,
   1557 trades) does not reproduce on today's parquets (1.03, +266%, 1639 trades). Yahoo
   adjusted-close re-basing shifts in-window history under the book. All four configs above ran
   on the SAME snapshot, so the comparison is internally valid; but certified numbers are
   snapshot-dependent facts. Institutional fix: point-in-time data (Norgate/Sharadar), already
   recommended.

## Mechanics
Prereg before run; 3 new trials charged before execution (ledger 205 → 208; baseline dedup'd
against its 2026-07-19 key). Determinism: two full runs, results JSONs identical modulo
`generated_at` and the expected `n_trials_before` (205 pre-charge vs 208 post). Iteration
window strictly < 2025-01-01; holdout untouched. Results: `validation/book_i_gate_2026-07-20.json`.
