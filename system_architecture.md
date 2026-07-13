# APEX Quant — System Architecture & Data Flow Diagram

This document contains a comprehensive flow diagram detailing how your automated quant systems, data adapters, live scanner, MT4 execution bridge, and web dashboard interact.

## System Architecture Flowchart

```mermaid
graph TD
    %% Style Definitions (Unidirectional & Decoupled Theme)
    classDef purple fill:#5f0d57,stroke:#4a0844,stroke-width:1.5px,color:#fff;
    classDef blue fill:#0c4cb6,stroke:#093b8e,stroke-width:1.5px,color:#fff;
    classDef green fill:#0b734b,stroke:#085839,stroke-width:1.5px,color:#fff;
    classDef orange fill:#d97000,stroke:#aa5800,stroke-width:1.5px,color:#fff;
    classDef darkblue fill:#1a2b49,stroke:#111e33,stroke-width:1.5px,color:#fff;

    %% Ingestion & Parser
    UR["User Request / Cron Trigger"]:::purple
    TIP["Technical Indicators Parser<br/>(SMA, RSI, MACD, BB, Fib)"]:::purple
    GEP["Grounding evidence pack<br/>(News, Seasonality, Macro)"]:::purple
    
    %% Strategy & Validation Flow (Strictly Unidirectional)
    HP["Hypothesis Proposer<br/>(Local LLM or Programmatic)"]:::blue
    BBSD["Bull/Bear/Supervisor Debate"]:::blue
    CDPV["CPCV / DSR / PBO Validation<br/>(Iron Curtain)"]:::blue
    PQE["Python Quant Engine<br/>(Backtesting & Research)"]:::blue
    
    AIC["AI committee / LLM Client<br/>(Ollama / Gemini / Groq)"]:::green
    
    %% Execution Layer
    APC["Active Position Check<br/>(Is price filled?)"]:::blue
    OR["Outcome Resolution<br/>(TP / SL / Expired / Closed Early)"]:::blue
    OI["Outcome: Invalidated<br/>(Freeze & halt candle wicks)"]:::darkblue
    CGL["Candle Grader Loop<br/>(gradeRow)"]:::darkblue
    
    %% Downstream Data Sinks (Pure Reads/Logs)
    DB[("Supabase Database<br/>(apex_research_memory)")]:::orange
    AS["Accuracy Scoreboard<br/>(Win Rate, Net R Profit, Brier)"]:::purple
    
    %% Offline/Batch Calibration (Decoupled Loop)
    ACF["AI Context Calibration Feedback<br/>(Offline Batch Process)"]:::green

    %% 1. Ingestion Flow
    UR -->|Fresh Scan Ticker| TIP
    TIP --> GEP
    
    %% 2. Proposal & Validation Flow
    HP --> BBSD
    BBSD -->|POST /api/ai proxy| AIC
    BBSD -->|Configure config.yaml| PQE
    BBSD -->|Debate survivors| CDPV
    CDPV -->|Rank validated strategies| PQE
    
    GEP -->|Feed indicators & context| AIC
    
    %% 3. AI Committee & Invalidation Flow
    AIC -->|Propose Verdict & levels| DB
    AIC -->|If CLOSE_TRADE verdict| OI
    OI --> DB
    
    %% 4. Position Checks & Database Logger (Unidirectional)
    CGL -->|Check Yahoo Candles| APC
    APC -->|Outcome Resolution| OR
    OR -->|Log final outcome| DB
    CGL -->|Log gradeRow state| DB
    
    %% 5. Downstream UI Sinks
    DB -->|Read logs| AS
    
    %% 6. Decoupled Asynchronous Calibration (Runs Offline)
    AS -.->|Asynchronous Batch Logs| ACF
    ACF -.->|Weekly/Nightly Prompt Updates| AIC
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

### 4. Unidirectional Outcome Resolution
* **Candle Grader Loop**: Continuously checks open positions against live market candles.
* **State Machine Protection**: By enforcing a unidirectional data flow (In-flight Scan ➔ Executed Signal ➔ DB Logger), the platform prevents race conditions between parallel threads.

### 5. Downstream Analytics (Supabase / Dashboard)
* **Supabase & Scoreboard**: Act as pure downstream sinks. They read logs and metadata to render statistics (accuracy, average R:R, calibration curves) without emitting state broadcasts back up to the active decision-making layers.

### 6. Decoupled Calibration Loop
* **AI Context Calibration Feedback**: Decoupled from the real-time pipeline. Instead of modifying prompts dynamically after every trade (which causes overfitting to market noise), this runs as an **offline batch job** (e.g. nightly/weekly). It updates the LLM committee's context using consolidated track record data, allowing it to adapt to long-term regime changes.
