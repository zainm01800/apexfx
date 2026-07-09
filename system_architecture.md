# APEX Quant — System Architecture & Data Flow Diagram

This document contains a comprehensive flow diagram detailing how your automated quant systems, data adapters, live scanner, MT4 execution bridge, and web dashboard interact.

## System Architecture Flowchart

```mermaid
graph TD
    classDef main fill:#1f1a3a,stroke:#8b5cf6,stroke-width:2px,color:#fff;
    classDef data fill:#1e293b,stroke:#475569,stroke-width:1px,color:#cbd5e1;
    classDef api fill:#0f172a,stroke:#00f0ff,stroke-width:1.5px,color:#00f0ff;
    classDef execution fill:#0c0a09,stroke:#e7e5e4,stroke-width:1.5px,color:#e7e5e4;

    subgraph DATA_FEEDS["1. Data Ingestion & Storage"]
        OANDA["🟢 OANDA Broker API<br/>(Real-Time & Historical Feed)"]:::api
        YF["Yahoo Finance<br/>(Silent Fallback)"]:::data
        CACHE[("Local Parquet Cache<br/>(engine/data_store/*.parquet)")]:::data
    end

    subgraph SYSTEM_ENGINE["2. Backtest & Parameter Optimization"]
        BTEST["run_full_backtest.py<br/>(Downloads & Paginated History)"]:::main
        OPT["optimize_high_frequency_portfolio.py<br/>(Parameter Sweeper & Filter)"]:::main
        CONF_JSON[("high_frequency_optimized_configs.json<br/>(Saved Setup Parameters)")]:::data
    end

    subgraph LIVE_TRADING["3. Live Scanner & Signals"]
        BOT["run_live_paper_trading.py<br/>(Runs 15m Loop --interval 900)"]:::main
        IND["Indicators & Entry/Exit Rules<br/>(ATR stops, momentum)"]:::data
        SIG_DIR[("MT4 Common File Directory<br/>(trade_signals.json)")]:::data
    end

    subgraph MT4_BRIDGE["4. Trade Execution (OANDA Demo)"]
        MT4["MetaTrader 4 Client Terminal<br/>(Logged into OANDA-Demo-1)"]:::execution
        EA["apex_mt4_bridge EA<br/>(AutoTrading Enabled 😊)"]:::execution
    end

    subgraph WEB_DASHBOARD["5. Real-Time Analytics UI"]
        SUPA[("Supabase DB Table<br/>(apex_research_memory)")]:::data
        VAPI["Vercel Edge Functions<br/>(api/memory.js & api/candles.js)"]:::api
        UI["Web Dashboard Frontend<br/>(history.html & dashboard.js)"]:::api
    end

    %% Data Flow Connections
    OANDA -->|Download historical candles| BTEST
    YF -->|Secondary source| BTEST
    BTEST -->|Save candle history| CACHE
    CACHE -->|Feed historical data| OPT
    OPT -->|Find & save best edges| CONF_JSON

    CONF_JSON -->|Load 39 active system configs| BOT
    OANDA -->|Fetch real-time bid/ask midprices| BOT
    BOT -->|Process indicators| IND
    IND -->|Write trade signal file| SIG_DIR
    SIG_DIR -->|Read signal instantly| EA
    EA -->|Place order on broker| MT4

    BOT -->|Log execution state| SUPA
    SUPA -->|Retrieve scan logs| VAPI
    VAPI -->|Serve statistics & scoreboard| UI
```

---

## Detailed Component Breakdown

### 1. Data Ingestion & Storage
* **OandaAdapter**: Built to download broker candles. Clamps date ranges below 5,000 candles to avoid API bounds errors and paginates them automatically. It includes a rate-limit protector to avoid OANDA key lockouts.
* **Local Parquet Cache**: Stores raw OHLCV wicks and prices locally on your disk. This eliminates repeated API fetches and makes backtests/sweeps execute instantly.

### 2. Backtest & Parameter Optimization
* **Backtester (`run_full_backtest.py`)**: Runs historical validation over years of data.
* **Optimizer (`optimize_high_frequency_portfolio.py`)**: Scans all parquet files, sweeps momentum, holding, and reward-to-risk combinations, filters out unprofitable systems, and outputs robust configs.

### 3. Live Scanner & Signals
* **Live Bot (`run_live_paper_trading.py`)**: Boots up with your OANDA Demo API credentials, fetches real-time prices, loads the 39 optimized configurations, processes indicator wicks, and writes signals to disk.

### 4. Trade Execution (OANDA Demo)
* **Expert Advisor (`apex_mt4_bridge.mq4`)**: Stays loaded on your MT4 chart. When it sees a fresh trade signal JSON file written by Python, it reads it and routes the buy/sell order to OANDA's practice servers instantly.

### 5. Real-Time Analytics UI
* **Web Dashboard**: Queries Supabase edge-functions. The frontend displays dynamic live broker status badges, loads up to 1,000 resolved trades, paginates cards to avoid UI lag, and features a scoreboard with custom trade count limits.
