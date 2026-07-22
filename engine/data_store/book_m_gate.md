# BOOK M GATE REPORT — Master Portfolio Validation (2026-07-22)

**Pre-registration:** [book_m_master_prereg.md](file:///Users/zain/Documents/apexfx/engine/data_store/book_m_master_prereg.md)  
**JSON Log:** [book_m_gate_2026-07-22.json](file:///Users/zain/Documents/apexfx/engine/data_store/validation/book_m_gate_2026-07-22.json)  
**Execution Window:** Out-Of-Sample pre-2025 Iteration Window (`< 2025-01-01`)  
**Decision:** **REJECT — Baseline (`Book H + Gold`) remains certified production standard.**

---

## 1. Validation Results Summary

| Metric | Certified Baseline (`Book H + Gold`) | Candidate (`Book M - Master`) | Gate Threshold | Result |
|---|---|---|---|---|
| **Sharpe Ratio** | **1.03** | **0.75** | Higher is better | Baseline Wins |
| **Deflated Sharpe Ratio (DSR)** | **0.9500** | **0.7560** | $> 0.95$ | **FAIL** |
| **Probability of Overfitting (PBO)** | **0.1542** | **0.1542** | $< 0.50$ | **PASS ✓** |
| **CPCV Out-Of-Sample Positive** | **100% (15/15 paths)** | **80% (12/15 paths)** | $> 50\%$ | **PASS ✓** |
| **Max Drawdown** | **15.8%** | **17.0%** | Lower is better | Baseline Wins |
| **Profit Factor** | **1.39** | **1.26** | $> 1.30$ | Baseline Wins |
| **Total Trades (Iteration Window)** | **1,639** | **1,730** | — | — |

---

## 2. Quantitative Post-Mortem: Why Book M Failed to Beat Book H

1. **Transaction Cost Drag from Smaller Position Sizing:**
   * Scaling risk per trade down from **2.0% to 0.8%** meant trade gains were smaller relative to fixed bid-ask spreads and commissions. Expectancy dropped from **$165.19/trade** to **$82.46/trade**.
2. **Correlated Drawdown Clustering across 25 Slots:**
   * Expanding from 10 slots to 25 slots increased position concurrency during market stress periods, pushing Max Drawdown from **15.8% to 17.0%**.
3. **PBO Rank Stability Confirmed (0.1542):**
   * Unlike Book I (PBO 0.602), Book M passed the PBO test cleanly. The result is statistically robust: **`Book H + Gold` (39 instruments, 2.0% risk, 10 slots) is objectively superior to a 25-slot diluted portfolio.**

---

## 3. Production Recommendation

* **Certified Baseline Intact:** `Book H + Gold` (39 instruments, 1.03 Sharpe, 15.8% Max DD, 15/15 CPCV paths) remains the official certified production baseline.
* **Intraday Forex & Crypto:** The 16 active intraday Forex configurations operate independently in the paper trading engine, supplementing trade frequency without diluting equity swing Sharpe.
