# 📈 Z.FX (ApexFX) — Quantitative Trading & Market Intelligence Platform

[![Live App](https://img.shields.io/badge/Live_Terminal-Z.FX_App-00F0FF?style=for-the-badge&logo=vercel)](https://apexfx.vercel.app/)
[![Python](https://img.shields.io/badge/Engine-Python_3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/Database-Supabase_PostgreSQL-3ECF8E?style=for-the-badge&logo=supabase)](https://supabase.com)
[![Vercel](https://img.shields.io/badge/Deployment-Vercel_Serverless-000000?style=for-the-badge&logo=vercel)](https://vercel.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](#license)

**Z.FX (ApexFX)** is an enterprise-grade, full-stack **Quantitative Algorithmic Trading Platform & AI Market Research Engine**. The system automates multi-asset screening, risk-gated execution modeling, live portfolio mirror syncing (via Interactive Brokers), and pre-registered forward paper testing across equities, forex, and crypto.

---

## 🌟 Executive Overview & Key Features

* **🤖 Autonomous Quantitative Alpha Engine**: Continuously screens 42 global instruments (US Equities/ETFs, Forex Majors, Crypto) using a 252-day regime-gated momentum model.
* **🛡️ Dynamic Risk Sizing & Volatility Scale**: Sizes positions using **Fractional-Kelly Sizing** ($0.20$ multiplier) bounded by a $1\%$ max risk per trade and $6.5\%$ portfolio risk ceiling.
* **⚡ Real-World Friction & Prop Realism Engine**: Models exact execution overhead for MetaTrader 5 (MT5) Swap-Free (Halal) Funded Accounts, factoring in bid-ask spread overhead ($\sim \$0.015/\text{share}$) and prop firm commissions ($\sim \$0.01/\text{share}$).
* **🏛️ Live Interactive Brokers (IBKR) Sync**: Streams live account balances, open positions, and fill history from IBKR TWS API / Flex Web API directly into Supabase (PostgreSQL), executing FIFO trade-matching algorithms for exact realized P&L precision.
* **📊 Institutional-Grade Web Terminal**: Built with vanilla JavaScript ES6+, HTML5 glassmorphism UI, and TradingView Lightweight Charts for high-frequency interactive price trajectory rendering.

---

## 📐 System Architecture

```
                                  +---------------------------------------+
                                  |     Quantitative Signal Engine        |
                                  |    (Python 3.11 / Pandas / NumPy)     |
                                  +-------------------+-------------------+
                                                      |
                                    Runs 252d Regime-Gated Scanning
                                    Applies Fractional-Kelly Position Sizing
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |   Supabase Database (PostgreSQL)      |
                                  |  (Tables: apex_ibkr_trades, positions)|
                                  +-------------------+-------------------+
                                                      |
                                        REST API / Edge Functions
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |   Vercel Serverless Edge Layer        |
                                  |     (/api/ibkr, /api/paper, PWA)      |
                                  +-------------------+-------------------+
                                                      |
                                       Renders Financial UI & Charts
                                                      |
                                                      v
+-------------------------------------------------------------------------------------------------+
|                                    Z.FX Client Web Terminal                                     |
|  +---------------------------+  +---------------------------+  +------------------------------+ |
|  |  ✨ Realistic Results     |  |     📈 IBKR Terminal      |  |     🧪 Progress Paper Proof  | |
|  |  MT5 Swap-Free CFD Model  |  |   Live Broker Portfolio   |  |   60-Day Pre-Reg Sandbox     | |
|  +---------------------------+  +---------------------------+  +------------------------------+ |
+-------------------------------------------------------------------------------------------------+
```

---

## 🧠 Quantitative Strategy & Risk Architecture

### 1. Alpha Signal Generation & Regime Filtering
The core scanning engine (`engine/`) runs a **252-day regime-gated momentum model**:
* **Universe**: 42 instruments (24 US Equities/ETFs, 11 Crypto, 7 Forex majors).
* **Higher-Timeframe Filter**: Signals are gated against the 1-week higher-timeframe trend filter. Trades execute only when short-term momentum aligns with macro direction.

### 2. Volatility-Scaled Fractional-Kelly Position Sizing
$$S = \min\left( \text{Kelly}_{0.20} \times \frac{\text{Capital}}{\text{ATR}_{14} \times 2.5}, \, \text{Cap}_{6.5\%} \right)$$
* **Risk per Trade**: Capped at $1\%$ of net liquidation value.
* **Portfolio Risk Cap**: Aggregate risk bounded at $6.5\%$ of portfolio capital.

### 3. Exits & Automated Risk Breakers
* **Initial Stop Loss**: $2.5 \times 14$-period ATR.
* **Take-Profit Scalper**: Scaled partial profit exits at $1\text{R}$ and $1.5\text{R}$.
* **Trailing Exits**: Chandelier ATR trailing stops and time-decay exit rules.
* **Circuit Breakers**:
  * **10% Drawdown**: Automatically scales down new position sizes by $50\%$.
  * **15% Drawdown**: Triggers an automated circuit breaker halting all new entries.

---

## 💻 Tech Stack & Engineering Directory

### **Core Stack**
* **Quantitative Engine**: Python 3.11, Pandas, NumPy, SciPy, Requests
* **Backend Database**: Supabase (PostgreSQL 15, Row Level Security, JSONB data stores)
* **Frontend UI**: Vanilla JavaScript (ES6 Modules), CSS3 Glassmorphism System, HTML5
* **Financial Charting**: TradingView Lightweight Charts (v4.2.3)
* **API & Hosting**: Vercel Serverless Functions, Node.js Edge Runtime, Service Worker PWA

### **Directory Overview**
```
apexfx/
├── engine/                       # Python Quantitative Research & Signal Processing
│   ├── data_store/               # Local JSONB setup ledgers & trade history logs
│   ├── paper_portfolio/          # Pre-registered paper proofing experiment ledgers
│   └── scripts/                  # IBKR mirror scripts, backtesters, correlation screeners
├── public/                       # PWA Frontend Client Pages & Styling
│   ├── realistic.html            # Realistic Funded Account Results view & card grid
│   ├── realistic.js              # FIFO trade-matching, MT5 spread & commission calculations
│   ├── ibkr-trades.html          # Interactive Brokers Live Terminal view
│   ├── ibkr-trades.js            # Live IBKR account state rendering & interactive chart toggle
│   ├── progress.html             # Pre-registered 60-day paper test progress dashboard
│   ├── history.html              # Quant AI scan history, setup learnings, and watchlist
│   ├── dashboard.css             # Core design tokens, dark mode slate palette & layout grids
│   ├── realistic.css             # Glassmorphic card styling & trade card responsive grids
│   └── app-mode.js               # Global application mode toggle state manager
├── api/                          # Vercel Serverless & Edge API Routes
│   ├── ibkr.js                   # High-performance Supabase query endpoint for IBKR trades
│   └── paper.js                  # Engine paper portfolio serverless data route
└── vercel.json                   # Vercel routing rules, rewrites, and security headers
```

---

## 🛠️ Local Development & Setup Guide

### Prerequisites
* **Python**: `v3.11+`
* **Node.js**: `v18+`
* **Supabase / Postgres**: Active account or local database instance

### 1. Repository Setup
```bash
git clone https://github.com/zainm01800/apexfx.git
cd apexfx
```

### 2. Python Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r engine/requirements.txt   # install pandas, numpy, requests
```

### 3. Local Web Development Server
```bash
npm install
npx vercel dev   # Or start local static server on http://localhost:3000
```

---

## 📝 License & Notice

**Z.FX (ApexFX)** is distributed under the **MIT License**.

> **Disclaimer**: *This project is built for quantitative research, backtesting, and educational demonstration purposes only. It does not constitute financial advice or an offer to buy/sell any financial instruments.*
