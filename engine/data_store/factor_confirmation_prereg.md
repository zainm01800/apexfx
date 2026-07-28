# PRE-REGISTRATION — Cross-asset factor confirmation on Book H gold (2026-07-28)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
exact gate rule, the configuration set (ONE experiment family, two sub-sleeve configs), the
adoption/falsification rule, and the ledger plan before execution. Changing anything after
the run requires a new pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` — the certified halal trend book (lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`**, `book_h_gapaware_2026-07-22.json`:
Sharpe 0.86284, 1637 trades, final equity 292,551.34; reproduced EXACT on 2026-07-28 before
this gate). Iteration window strictly < 2025-01-01, seed 42, warmup 250, CPCV purge 21,
15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

Ehsani & Linnainmaa (JF 2022, "Factor momentum and the momentum factor"): time-series
momentum in individual assets is largely explained by momentum in **factors** — an asset's
own trend persists when the underlying factor trends the same way and reverses when the
factor disagrees. Conditioning entries on factor-trend agreement should therefore raise
per-trade expectancy within the factor-exposed sleeves.

**H:** equity-sleeve trades whose direction agrees with the halal equity index's 63-day
trend, and alt-crypto trades whose direction agrees with BTC's 63-day trend, have **higher
win rate / net expectancy** than disagreeing trades; gating entries on factor agreement
raises the sleeve's per-trade expectancy out-of-sample.

## 2. Exact rule (pre-registered constants)

Per instrument `i`, per decision bar `t`, point-in-time (closes up to and including `t`):

- Factor trend lookback **L = 63** trading days (one quarter; one value, not swept):
  `factor_sign(t) = sign(close_t / close_{t−63} − 1)` of the factor instrument,
  forward-filled onto the book's union timeline (last known value — causal).
- **(a) EQUITY sleeve:** factor = **ISWD.L** (iShares MSCI World Islamic — the book's own
  halal equity index). Sleeve = the book's genuine equity exposure: the 12 screened stocks,
  the 3 Islamic UCITS index ETFs, the 5 sector ETFs — i.e. every equity-class instrument
  **except SGLD.L** (a gold ETC, not a stock — ungated) and SPSK (sukuk; not in this book).
- **(b) ALT-CRYPTO sleeve:** factor = **BTC/USD**. Sleeve = every crypto instrument
  **except BTC itself** (BTC is the factor — ungated).
- **FX is excluded from the experiment** (no credible factor analog — all 7 majors ungated),
  as are BTC, gold, and (vacuously) sukuk.
- **Gate:** a momentum-mode entry in a gated sleeve is kept iff
  `factor_sign(t) == sign(trade direction)`; vetoed otherwise. An **undefined** factor trend
  (fewer than L bars of factor history) blocks nothing — absence of data is not evidence
  against the trade. A factor trend of exactly 0 blocks both directions (no agreement).
  Bollinger mean-reversion signals are not gated (counter-trend by construction; the
  mechanism is about trend persistence).
- Sizing, exits, costs, caps, HTF gate, regime gate: **unchanged certified machinery**.

## 3. Configurations (the full selection set: exactly 3 — ONE family, two configs)

| Config | `entry_gate` | Question |
|---|---|---|
| `fac_control_252` (control) | `None` (certified) | anchor — must reproduce the certified numbers exactly |
| `fac_equity_iswd_63` (challenger a) | `{"kind":"factor","sleeve":"equity","lookback":63}` | does ISWD.L confirmation raise equity-sleeve expectancy OOS? |
| `fac_crypto_btc_63` (challenger b) | `{"kind":"factor","sleeve":"crypto","lookback":63}` | does BTC confirmation raise alt-crypto expectancy OOS? |

Universe, params, costs, caps, warmup, window: identical; the ONLY difference is the entry
gate. Certified panel insertion order (EQUITY_CORE first — the certified numbers are
ordering-sensitive). Implementation: the same `DirectionalEntryGate` wrapper seam as the
FIP gate; `entry_gate=None` default ⇒ byte-identical certified behaviour.

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (the machinery's strict threshold, deflated by the **full
updated TrialLedger count**, §6) **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths
positive (purge 21) **and** PBO < 0.5 across the 3-config selection set (16 splits,
seed 42; standing caveat: ~100% universe overlap — reported as computed). The gate spec
flows into every CPCV fold via the model factory params; per-path trade lists are captured
so sleeve expectancy is measured on the SAME 15 folds.

## 5. Pre-committed adoption / falsification rule

The FAMILY is **ADOPTED** (flag available for a future certified-change decision) iff ALL
legs hold for **BOTH** challengers:

- **Expectancy leg (per challenger, on its own sleeve):** mean net pnl per trade of
  sleeve trades entered inside each CPCV test window **strictly greater** than the
  control's same-sleeve figure on **≥ 12 of the 15** paths (equity challenger measured on
  equity-sleeve trades; crypto challenger on alt-crypto trades; a path where either side
  has zero sleeve trades counts as not-improved).
- **DSR leg (per challenger):** full-window DSR > **0.95** at the full updated ledger
  count (§6).
- **Sharpe-noise leg (per challenger):** full-window Sharpe ≥ control Sharpe − **0.05**
  (the reduced trade count must not degrade the book materially).
- **PBO leg (family):** PBO < **0.5** across the 3-config set.

**KILL: any leg fails for either challenger ⇒ the family is REJECTED.** Per-sleeve
outcomes are reported honestly either way; a one-sleeve pass is NOT a partial adoption.
The control failing to reproduce the anchor aborts the gate (hard check, §7).

Measured but not verdict-binding: the mechanism diagnostic — control trades per sleeve
split by factor agreement at entry (agreeing vs disagreeing expectancy and win rate, the
direct test of §1) — plus the full metric set, CPCV path distributions, veto counts.

## 6. Ledger plan

`TrialLedger` at **n = 299** at writing (per the FIP gate's recording, 2026-07-28). This
campaign evaluates exactly 3 configs, so exactly **3 new trials** (`fac_control_252`,
`fac_equity_iswd_63`, `fac_crypto_btc_63`, kind `factor_confirmation_gate`, mrpt 0.01)
are recorded BEFORE the first run → **n = 302** deflates every DSR in this gate. No other
lookbacks, factor choices, or sleeve definitions will be evaluated; any follow-up is a new
pre-registration.

## 7. Known limitations

- **The factor choices are the book's own anchors, not optimised** — ISWD.L is the halal
  world index the book already trades; BTC is crypto's factor by construction. 63d is the
  standard quarterly horizon. Nothing swept.
- **The equity sleeve is index-heavy**: ISWD.L gates ISDU.L/ISDE.L/XLK/… — instruments
  highly correlated with the factor itself, so the gate there is close to a self-
  confirmation; the 12 single stocks are where the mechanism has real discriminative
  content. Reported per instrument group, not verdict-binding.
- **Alt-crypto history is short** (several names list 2020+): early folds have thin crypto
  sleeves; paths with zero sleeve trades count as not-improved by rule.
- **Vetoing factor-disagreeing entries changes slot competition** (freed slots recycle) —
  the Sharpe-noise leg exists because the system-level effect is not the mean of the
  per-signal effect.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34) —
  hard-checked before any comparison; a mismatch aborts the gate.
