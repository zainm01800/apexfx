# PRE-REGISTRATION — 3-Sleeve Combined Portfolio Gate (2026-07-22)

**Status: Pre-registered BEFORE strategy gate run.**
**Family Name: `three_sleeve_portfolio`**
**DSR Threshold at K=12: 0.9958**

---

## 1. Objective & Hypothesis
The single-sleeve Trend Book baseline (`book_h_gold_252`) reaches Sharpe 0.922 / 1.002, but its 1.0% risk variant incurs 17.62% max drawdown — exceeding funded account rules (max 10% DD wall). At 0.5% risk, drawdown drops to 11.0%, but monthly income is £430/mo.

**Hypothesis**: Combining 3 distinct, low-correlation strategy sleeves:
1. **Book Runner Trend** (60% weight): Daily trend-following with uncapped Chandelier trailing stops across 35 instruments.
2. **Turn-of-Month (TOM) Seasonality** (25% weight): Calendar drift on equities and FX ($r = 0.195$ vs trend).
3. **Crypto XS Momentum** (15% weight): Weekly relative strength ranking on liquid crypto ($r = 0.150$ vs trend).

will raise combined portfolio Sharpe to **~1.35 – 1.45**, generating **£700+ / month** on a £100k account while reducing max drawdown to **≤ 10.0%**.

---

## 2. Grid Configurations (3 Trials Charged)
- `three_sleeve_rpt050`: Risk/trade 0.50% (`max_risk_per_trade = 0.0050`)
- `three_sleeve_rpt075`: Risk/trade 0.75% (`max_risk_per_trade = 0.0075`) [Primary Candidate]
- `three_sleeve_rpt085`: Risk/trade 0.85% (`max_risk_per_trade = 0.0085`)

---

## 3. Decision Rules & Validation Gates
1. **Max Drawdown Wall**: Must maintain forward max drawdown **≤ 10.0%** (Hard Fail if > 10.0%).
2. **Monthly Profit Target**: Must generate **≥ £700 / month** on a £100,000 account (£8,400+ / year).
3. **Deflated Sharpe Ratio (DSR)**: DSR > 0.9958 deflated by family trial ledger count $N_f$.
4. **CPCV 15 Paths**: Median OOS Sharpe > 0, > 50% positive paths.
5. **Paired Statistical Significance**: Circular Block Bootstrap ($B=10000$, block=21, seed 42) $p < 0.05$ vs single-sleeve baseline.
6. **Determinism Twin Check**: Duplicate execution with seed 42 must yield byte-identical output matching (`determinism_pass: true`).
7. **Iteration Window**: Data strictly BEFORE `2025-01-01` (2025+ holdout is locked and untouched).

---

## 4. Deliverables
- `scripts/run_portfolio_gate_three_sleeve.py`
- `data_store/validation/three_sleeve_gate_2026-07-22.json` (+ determinism twin)
- `data_store/three_sleeve_gate.md`
