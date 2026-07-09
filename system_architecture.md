# APEX Quant — System Architecture & Data Flow Diagram

This document contains a comprehensive flow diagram detailing how your automated quant systems, data adapters, live scanner, MT4 execution bridge, and web dashboard interact.

## System Architecture Flowchart

```mermaid
graph TD
    %% Style Definitions (Matching the Purple, Blue, Green, Orange Theme)
    classDef purple fill:#5f0d57,stroke:#4a0844,stroke-width:1.5px,color:#fff;
    classDef blue fill:#0c4cb6,stroke:#093b8e,stroke-width:1.5px,color:#fff;
    classDef green fill:#0b734b,stroke:#085839,stroke-width:1.5px,color:#fff;
    classDef orange fill:#d97000,stroke:#aa5800,stroke-width:1.5px,color:#fff;
    classDef darkblue fill:#1a2b49,stroke:#111e33,stroke-width:1.5px,color:#fff;

    %% Nodes Placement
    UR["User Request / Cron Trigger"]:::purple
    TIP["Technical Indicators Parser<br/>(SMA, RSI, MACD, BB, Fib)"]:::purple
    GEP["Grounding evidence pack<br/>(News, Seasonality, Macro)"]:::purple
    ARV["AI Re-check validation<br/>(Update Button)"]:::purple
    
    HP["Hypothesis Proposer<br/>(Local LLM or Programmatic)"]:::blue
    BBSD["Bull/Bear/Supervisor Debate"]:::blue
    CDPV["CPCV / DSR / PBO Validation"]:::blue
    PQE["Python Quant Engine<br/>(Backtesting & Research)"]:::blue
    
    AIC["AI committee / LLM Client<br/>(Ollama / Gemini / Groq)"]:::green
    ACF["AI Context Feedback<br/>(Historical Calibration)"]:::green
    
    APC["Active Position Check<br/>(Is price filled?)"]:::blue
    OR["Outcome Resolution<br/>(TP / SL / Expired / Closed Early)"]:::blue
    OI["Outcome: Invalidated<br/>(Freeze & halt candle wicks)"]:::darkblue
    CGL["Candle Grader Loop<br/>(gradeRow)"]:::darkblue
    
    DB[("Supabase Database<br/>(apex_research_memory)")]:::orange
    AS["Accuracy Scoreboard<br/>(Win Rate, Net R Profit, Brier)"]:::purple

    %% Logical Flows & Connectors
    UR -->|Fresh Scan Ticker| TIP
    TIP --> GEP
    
    HP --> BBSD
    BBSD -->|POST /api/ai proxy| AIC
    BBSD -->|Configure config.yaml| PQE
    BBSD -->|Debate survivors| CDPV
    CDPV -->|Rank validated strategies| PQE
    
    GEP -->|Feed indicators & context| AIC
    ARV -->|Run fresh analysis on open trade| AIC
    
    APC -->|No entry within style limit| OR
    APC -->|Filled & hits target/stop| OR
    OR -->|Save state & outcome_date| DB
    
    CGL -->|Check Yahoo Candles| APC
    CGL --> DB
    
    AIC -->|Propose Verdict & levels| DB
    AIC -->|If CLOSE_TRADE verdict| OI
    OI --> DB
    
    DB -->|Fetch lean historical rows| AS
    AS -->|Calculate expectancy & lessons| ACF
    AIC -->|Feed track record to| ACF
```

---

## Detailed Component Breakdown

### 1. Ingestion & Pre-Analysis Loop
* **Technical Indicators Parser**: Processes standard wicks, closes, SMAs, MACD, and Bollinger Bands on each candle close.
* **Grounding Evidence Pack**: Bundles external context (news sentiment, macro data, seasonal indices) into the scan prompt.

### 2. Hypothesis & Validation Engine (Python Quant Engine)
* **Debate Loop**: The hypothesis proposer structures potential setups which undergo a simulated supervisor review to prevent biases.
* **CPCV / DSR Validation**: Strategies undergo cross-validation (CPCV) and probability of backtest overfitting (PBO) verification before staging.

### 3. AI Committee Consensus
* **LLM Committee**: Employs real-time LLM validation (Ollama/Gemini/Groq) to confirm the entry thesis, target bounds, and stop levels.
* **Invalidation Trigger**: If a `CLOSE_TRADE` verdict is generated, it immediately triggers an outcome invalidation.

### 4. Outcome Resolution & Scoreboard
* **Candle Grader Loop**: Continuously checks open positions against live market candles.
* **Accuracy Scoreboard**: Pulls logs from Supabase to render win rates, expectancy, Brier score, and calibration curves dynamically on your dashboard.
