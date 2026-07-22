# PRE-REGISTRATION — Book M: Master Multi-Timeframe Portfolio (Book M - Master) (2026-07-22)

**Status:** Pre-registered BEFORE execution. 1 new trial charged to trial_ledger.json.
**Reference Baseline:** `book_h_gold_252` (Certified Production Baseline).

---

## 1. Executive Summary & Core Hypothesis

Previous expansion books (E, J, K) suffered from a structural bottleneck: expanding the universe without expanding slot capacity caused **over 41,000 candidate signals to be vetoed** simply because the 10 swing-bucket slots were full.

**Book M (Master Portfolio)** resolves this bottleneck by implementing a 3-pillar architecture:
1. **Expanded Capacity with Scaled Risk:** Expands the swing slot cap from 10 to 25 concurrent slots, while scaling individual position risk down from 2.0% to 0.80% per trade (preserving the strict 6.5% portfolio risk ceiling).
2. **Multi-Timeframe Layering:** Combines the Daily (1d) Trend-Following core with Intraday (1h / 15m) Mean-Reversion/Scalp Forex & Crypto sleeves. This generates **35–50 trades per month** without crowding out long-term swing positions.
3. **UK Retail (UCITS/Halal) Compliance:** Restricts equity holdings to 100% UK-tradeable UCITS ETFs (`IITU.L`, `IUES.L`, `BTEC.L`, `SMH.L`, `ISDW.L`, `SGLD.L`) and halal-screened mega-caps, eliminating IBKR PRIIPs/KID Error 201.

---

## 2. Configuration & Parameter Specification

### Sleeve A: Daily Trend Core (1d)
* **Holdings (26 instruments):** 
  * *UCITS & Commodities (6):* `IITU.L`, `IUES.L`, `BTEC.L`, `SMH.L`, `ISDW.L`, `SGLD.L`
  * *Mega-Cap Equities (9):* `AAPL`, `MSFT`, `NVDA`, `META`, `AMZN`, `GOOGL`, `TSLA`, `AMD`, `TSM`
  * *Crypto Majors (4):* `BTC/USD`, `ETH/USD`, `SOL/USD`, `BNB/USD`
  * *FX Core (7):* `EUR/USD`, `GBP/USD`, `USD/JPY`, `USD/CHF`, `AUD/USD`, `USD/CAD`, `NZD/USD`
* **Signal Engine:** `RegimeGatedMomentum` + `MultiTimeframeMomentum` (1w x 50 HTF gate), lookback 252d, holding horizon 21d, RR 1.5.

### Sleeve B: Intraday Forex & Crypto High-Frequency Sleeves (1h / 15m)
* **Active Forex Setups (8 pairs):** `EUR/AUD (1h)`, `GBP/NZD (15m)`, `EUR/NZD (15m)`, `GBP/AUD (1d/1h)`, `GBP/CAD (1d)`, `EUR/CAD (1d)`, `CHF/JPY (1d/15m)`, `AUD/NZD (1h)`.
* **Signal Engine:** Counter-Trend Mean-Reversion & Breakout Momentum with Weekly MA Bypass.

### Risk Management & Caps
* **Max Risk per Trade:** `0.008` (0.80% risk per position).
* **Max Concurrent Swing Slots:** `25` slots.
* **Max Portfolio Open Risk:** `0.065` (6.5% ceiling).
* **Max Gross Leverage:** `3.0x`.
* **Drawdown Breakers:** `0.10` / `0.20`.

---

## 3. Evaluation Grid & Decision Rule

Exactly **2 configurations** are evaluated to ensure 100% rank stability and prevent set-level PBO inflation:

| Config | Description | Universe | Slots | Risk / Trade |
|---|---|---|---|---|
| `book_h_gold_252` | Certified Baseline (deduped) | 39 instruments | 10 slots | 2.0% |
| `book_m_master_252` | **Master Portfolio (NEW)** | 50+ instruments (Multi-TF) | 25 slots | 0.8% |

### Binding Decision Rule
`book_m_master_252` will be **ADOPTED** if and only if it passes all three statistical gates on clean data:
1. **Deflated Sharpe Ratio (DSR):** $> 0.95$ (deflated by total trial ledger count $n \ge 212$).
2. **Probability of Backtest Overfitting (PBO):** $< 0.50$ (across the 2-config selection set).
3. **Combinatorial Purged Cross-Validation (CPCV):** Median OOS Sharpe $> 0$ with $> 50\%$ of 15 paths positive.

---

## 4. Script & Deliverables
* **Prereg:** `data_store/book_m_master_prereg.md` (this file)
* **Script:** `scripts/run_portfolio_gate_book_m.py`
* **Output Artifacts:** `data_store/validation/book_m_gate_2026-07-22.json` & `data_store/book_m_gate.md`
