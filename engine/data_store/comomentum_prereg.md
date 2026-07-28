# PRE-REGISTRATION — Comomentum crowding gate on Book H gold (2026-07-28)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
exact gate rule, the configuration set, the adoption/falsification rule, and the ledger plan
before execution. Changing anything after the run requires a new pre-registration and new
ledger charges.

**Base book:** `book_h_gold_252` — the certified halal trend book (lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`**, `book_h_gapaware_2026-07-22.json`:
Sharpe 0.86284, 1637 trades, final equity 292,551.34; reproduced EXACT on 2026-07-28 before
this gate). Iteration window strictly < 2025-01-01, seed 42, warmup 250, CPCV purge 21,
15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

Lou & Polk (RFS 2022, "Comomentum: inferring arbitrage activity from return correlations"):
when the assets in a momentum cohort move **abnormally in lockstep**, the trade is crowded
with arbitrage capital, and the crowded state predicts **reversals** — momentum crashes
cluster in high-comomentum periods. Blocking new entries while the same-direction cohort is
abnormally correlated should cut the book's exposure to momentum crashes.

**H:** entries taken while the same-direction momentum cohort's abnormal correlation is high
(> +1.5σ) have **worse (reversal-prone) expectancy** than entries in normal states; blocking
new entries in those states improves the book's **left tail** (worst day, worst month) at a
small Sharpe cost.

## 2. Exact rule (pre-registered constants)

Per decision bar `t` on the book's union timeline, point-in-time (closes up to and
including `t` only):

- **Cohorts (per direction):** instruments whose **252-day** momentum
  `close_t / close_{t−252} − 1` is positive (LONG cohort) / negative (SHORT cohort) at `t`;
  per-instrument momentum forward-filled onto the union timeline (last known value — the
  same cross-sectional construction as the FIP gate). Cohorts are defined by the book's own
  formation lookback, NOT by open positions (Lou & Polk's cohort is past-return winners /
  losers, not holdings).
- **Comomentum:** `c(t)` = mean pairwise Pearson correlation of daily returns among cohort
  members over the trailing **60** union-timeline rows, pairwise-complete on each
  instrument's own-calendar returns (the same construction as the certified portfolio
  correlation frame — no forward-fill inside the returns). Undefined when the cohort has
  < **5** members or < **10** defined pairwise values.
- **Abnormal comomentum:** `z(t) = (c(t) − median) / std` over the trailing **252** values
  of `c` (window inclusive of `t`; population std, ddof=0). Undefined until the 60-row
  correlation window and the full 252-row reference window both exist (~312 trading days)
  — an undefined state blocks nothing.
- **Gate:** block NEW momentum-mode entries in a direction while that direction's
  `z(t) > +1.5`. Exits are never gated; Bollinger mean-reversion signals are not gated
  (counter-trend by construction). Sizing, exits, costs, caps, HTF gate, regime gate:
  **unchanged certified machinery**.

## 3. Configurations (the full selection set: exactly 2)

| Config | `entry_gate` | Question |
|---|---|---|
| `comom_control_252` (control) | `None` (certified) | anchor — must reproduce the certified numbers exactly |
| `comom_gate_252` (challenger) | `{"kind":"comomentum","lookback":252,"corr_window":60,"ref_window":252,"z_thresh":1.5}` | does blocking crowded-state entries improve the left tail at small Sharpe cost? |

Universe, params, costs, caps, warmup, window: identical; the ONLY difference is the entry
gate. Certified panel insertion order (EQUITY_CORE first). Implementation: the same
`DirectionalEntryGate` wrapper seam as the FIP and factor-confirmation gates;
`entry_gate=None` default ⇒ byte-identical certified behaviour.

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (the machinery's strict threshold, deflated by the **full
updated TrialLedger count**, §6) **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths
positive (purge 21) **and** PBO < 0.5 across the 2-config selection set (16 splits,
seed 42; standing caveat: ~100% universe overlap — reported as computed). The gate spec
flows into every CPCV fold via the model factory params.

## 5. Pre-committed adoption / falsification rule

The challenger is **ADOPTED** (flag available for a future certified-change decision) iff
ALL FOUR legs hold:

- **Left-tail leg (both metrics):** worst daily return AND worst-month pnl on the £100k
  book are **less negative** than the control's (strictly).
- **Sharpe-cost leg:** full-window Sharpe drop ≤ **0.03** (absolute) — the tail insurance
  may cost a little, not much.
- **DSR leg:** challenger full-window DSR > **0.95** at the full updated ledger count (§6).
- **PBO leg:** PBO < **0.5** across the 2-config set.

**KILL: any leg fails ⇒ REJECTED.** The control failing to reproduce the anchor aborts the
gate (hard check, §7).

Measured but not verdict-binding: the mechanism diagnostic — control trades split by
whether their (entry date, direction) would have been blocked (abnormal comomentum > +1.5
in the trade's direction at entry): blocked-state vs kept-state expectancy and win rate
(the direct test of §1) — plus the full metric set, CPCV path distributions, veto counts,
blocked-date counts per direction.

## 6. Ledger plan

`TrialLedger` at **n = 302** at writing (per the factor-confirmation gate's recording,
2026-07-28). This campaign evaluates exactly 2 configs, so exactly **2 new trials**
(`comom_control_252`, `comom_gate_252`, kind `comomentum_gate`, mrpt 0.01) are recorded
BEFORE the first run → **n = 304** deflates every DSR in this gate. No other correlation
windows, reference windows, z thresholds, or cohort definitions will be evaluated; any
follow-up is a new pre-registration.

## 7. Known limitations

- **The constants are literature-standard, not optimised** — 60d correlation window, 252d
  reference, +1.5σ threshold, cohort = the book's own 252d formation sign. Nothing swept.
- **Cohorts are momentum-sign cohorts, not the book's open positions** (the faithful
  Lou & Polk reading); a state where the book is flat but the cohort is crowded still
  blocks. Reported, not verdict-binding.
- **Cross-asset correlation is calendar-contaminated by construction** — equities trade
  5 days, crypto 7; pairwise-complete correlations on the union timeline mix calendars
  (weekend equity returns are missing, crypto's are not). This is the certified
  correlation machinery's own convention, reused deliberately.
- **The gate's value shows up in crashes, which are rare** — ~9 years of iteration data
  contain few true momentum crashes; the left-tail leg may be decided by one or two
  episodes. That is why the DSR/PBO legs also bind.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34) —
  hard-checked before any comparison; a mismatch aborts the gate.
