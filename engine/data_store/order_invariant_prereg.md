# PRE-REGISTRATION — Order-invariant risk allocation: simultaneous-γ vs sequential portfolio cap (2026-07-25)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
configuration set, the flags, the gates, the falsification rule, and the ledger plan before
execution. Changing anything after the run requires a new pre-registration and new ledger
charges.

**Base book:** `book_h_gold_252` — the certified halal trend book: lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`** — the 2026-07-22 gap-aware certified
state (`book_h_gapaware_2026-07-22.json`: Sharpe 0.86284, 1637 trades, final equity
292,551.34, DSR 0.991 @ n=213). Remaining caps per config: max_portfolio_risk 0.065,
max_total_exposure 3.0, max_correlated_exposure 1.5, drawdown breakers 0.10/0.20, swing
bucket 10 slots, global hard cap 12. Iteration window strictly < 2025-01-01, seed 42,
warmup 250, CPCV purge 21, 15 paths. The 2025+ holdout is not touched.

**The defect being fixed** (measured, `engine/data_store/ordering_sensitivity_audit.md`,
2026-07-22): same-bar candidates are evaluated in **panel dict-insertion order** and each
permitted candidate is provisionally booked, so the scarce resources — the 10-slot swing
bucket, the 12-position global cap, and the 6.5% portfolio-risk budget (`RiskManager` step
5.5) — are consumed first-come-first-served. Shuffling the panel order alone moves Sharpe
**0.217 ↔ 0.863** (7 orderings; certified order = the BEST). The certified Sharpe is the
top of a luck distribution, not its centre.

---

## 1. Hypothesis

**H:** replacing sequential portfolio-cap vetoes with **simultaneous proportional scaling**
(γ) — and sequential panel-order slot selection with **signal-strength ranking** — makes the
book's allocation a deterministic function of the candidate **set**, not the panel **order**,
**without degrading performance beyond noise**.

Order-invariance is the primary claim. Performance parity against the honest baseline (the
shuffle distribution, not the lucky certified ordering) is the secondary claim.

## 2. Configurations (the full selection set: exactly 2)

| Config | Flags | Question |
|---|---|---|
| `book_h_gold_252_seq` (control) | certified defaults: `slot_allocation="order"`, `portfolio_risk_cap_mode="sequential"` | anchor + artifact demonstration |
| `book_h_gold_252_simul` (challenger) | `slot_allocation="expected_value"`, `portfolio_risk_cap_mode="simultaneous"` | does simultaneous-γ remove the artifact for free? |

Both flags are new `RiskConfig` fields defaulting to the certified behaviour; `config.yaml`
is NOT modified (its live sections are frozen). The control is byte-identical to the
certified engine. Exactly 2 configs to keep PBO meaningful.

### 2a. Challenger mechanics (pre-registered rules)

Per decision bar:

1. **Rank** all live candidates by signal strength `EV = p·b − (1−p)` descending, tie-break
   instrument name (deterministic). This replaces panel order as the selection key for the
   HARD count caps (10-slot swing bucket, 12-position global cap) when candidates exceed
   capacity. **This changes WHICH instruments win scarce slots — a deliberate, documented
   selection change** (the audit's recommended fix: `probability` is already computed and
   currently discarded exactly when capital is scarce). Count caps stay hard; no scaling
   substitutes for them.
2. **Size** each candidate independently through the unchanged `RiskManager.permit`
   pipeline (Kelly gate, per-trade cap, drawdown ramp, regime scale, vol-target ceiling,
   gross cap, correlation cap, notional cap) with step 5.5 (the sequential portfolio-risk
   clamp/veto) **skipped** — each candidate gets its raw weight as if it were the only
   candidate. Step 5.5 is bypassed via a runtime attribute the backtester sets
   (`defer_portfolio_risk_cap`), so the LIVE loop keeps the sequential cap even if the
   config flag were ever flipped live; live behaviour is unchanged either way.
3. **Scale simultaneously.** `implied_total = open_risk + Σ candidate_risk`, where
   `open_risk = Σ max(0, units × |last_px − stop|)` over open positions (the engine's
   existing open-risk measure, audit E6) and `candidate_risk = Σ risk_fraction × equity`
   over permitted candidates. If `implied_total > cap_budget = max_portfolio_risk × equity`:
   `γ = cap_budget / implied_total` (else γ = 1, no-op). γ applies **uniformly to every
   position's open risk**:
   - each permitted candidate's `units` / `notional` / `risk_fraction` are multiplied by γ
     (stop and target unchanged; constraint label `portfolio_risk_gamma={γ:.2f}`);
   - each open position with **positive** open risk is queued a trim of fraction `(1−γ)`
     of its units, executed at the NEXT BAR'S OPEN (identical fill timing to new entries)
     with the standard per-asset-class fill cost and the same commission accounting as a
     TradeManager partial. Stop and target are unchanged, so open risk scales by exactly γ.
     Positions whose stop is at/past the mark carry zero open risk and are not trimmed
     (0 × γ = 0 — the rule is uniform in risk space).
   Trims are partial reductions of a staying-open position: no `Trade` record is created;
   realised trim P&L accrues into the position's `realized_pnl_total`, exactly like TMS
   partials. γ never exceeds 1 (the mechanism only de-risks; it never re-levers).
4. Bars with no permitted candidate trigger nothing (sequential parity: the cap only ever
   binds on entries).

Determinism: open-risk and candidate-risk sums are accumulated in instrument-sorted and
EV-ranked order respectively, so γ is a pure function of the candidate set.

### 2b. What stays sequential (documented, negligible)

The gross-exposure cap (3.0×) and correlation-cluster cap (1.5×) still bind in EV-ranked
order inside `permit`. In the certified book neither label appears in the constraint log
(they do not bind); their residual order-dependence is nil in practice and is reported,
not silently ignored.

## 3. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §5) **and**
CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and** PBO < 0.5
across the 2-config selection set (16 splits, seed 42; standing caveat: the configs share
~100% of their universe and differ only in allocation, so PBO's discriminative power is
limited by construction — reported as computed). The challenger config flows into every
CPCV fold (the flags live on `cfg.risk`, which `run_portfolio_cpcv` forwards).

## 4. Shuffle protocol and pre-committed decision rule

**Shuffle protocol:** 3 seeded permutations of the certified 39-instrument panel
(`np.random.RandomState(101), (202), (303)` — fixed here so the test itself is
reproducible) plus the certified order, run through BOTH configs. Shuffle runs are the
SAME 2 configs on permuted inputs — measurement of a property of the allocation rule, not
new configurations; they carry **no ledger charge** (nothing is selected on them).

- **C1 (order-invariance, primary):** challenger metrics are identical across the 3
  shuffles AND the certified order: |ΔSharpe| ≤ 1e-9, `n_trades` exactly equal,
  |Δfinal_equity| ≤ 1e-9 relative. (Expected bit-identical: EV ranking makes PASS-2 a pure
  function of the candidate set; tolerances exist only for float-summation paranoia.)
- **C2 (artifact demonstrated):** control metrics DIFFER across the same shuffles
  (re-demonstrates the defect on this machine/data).
- **C3 (performance parity):** challenger Sharpe ≥ control shuffle-MEDIAN Sharpe − 0.05
  AND challenger profit factor ≥ control shuffle-median PF − 0.10 AND the challenger passes
  all three gates in §3. The control's certified-ordering number (0.86284) is the lucky top
  of its distribution (audit), so the binding comparison is against the shuffle median —
  the honest centre. Challenger vs certified-anchor deltas are reported for the record,
  and if the challenger matches or beats the certified anchor that is a headline, but it is
  NOT required.
- **Verdict ADOPT** = C1 ∧ C2 ∧ C3. **REJECT** otherwise (including: γ barely ever < 1 —
  mechanism inert — reported, not silently accepted).

Measured but not verdict-binding (reported): trades, expectancy, win rate, maxDD, worst
day, avg monthly P&L, γ bind count, trim count, CPCV path distributions both configs,
full shuffle tables both configs.

## 5. Ledger plan

`TrialLedger` at **n = 271** at writing (verified 2026-07-25). This campaign evaluates
exactly 2 configs, both under the certified risk anchor (`max_risk_per_trade 0.01`), so
exactly **2 new trials** (`book_h_gold_252_seq`, `book_h_gold_252_simul`,
`kind=order_invariant_gate`) are recorded BEFORE the first run → **n = 273** deflates every
DSR in this gate. No other configs will be evaluated; any follow-up (γ on notional instead
of risk, alternative strength scores, trim-at-close variant) is a new pre-registration.

## 6. Known limitations

- **Trim churn.** Uniform-γ de-risks open positions on crowded bars and never re-inflates
  them — a systematic ratchet on winners that the sequential book does not have. That is
  the honest cost of order-invariance and is exactly what C3 measures. Trims pay spread +
  slippage like any fill.
- **γ events are rare by design.** The 6.5% cap bound only 184+46 times in the certified
  run; the dominant order-dependent cap is the 10-slot swing bucket (18,155 vetoes), which
  the EV ranking addresses. Both effects are in the challenger and cannot be separated
  within the 2-config budget; separating them is a new pre-registration.
- **The control's certified-order run must reproduce the anchor** (Sharpe 0.86284, 1637
  trades, equity 292,551.34) — hard-checked before any comparison; a mismatch aborts the
  gate.
- **In-window measurement.** One decade; the shuffle distribution of the control is the
  reference for "noise", not a theoretical model.
