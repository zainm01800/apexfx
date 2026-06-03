-- ══════════════════════════════════════════════════════════════════════════════
-- APEX Strategy Backtests (client-side multi-strategy backtest results)
-- One row per pair × timeframe × strategy × run. Populated by the in-browser
-- backtest engine (public/lib + backtest.worker.js) via /api/backtest-runs.
-- APPEND-ONLY: each id embeds the run timestamp, so re-running adds rows rather
-- than overwriting. Separate from apex_backtests (the Python engine's DSR/PBO/CPCV
-- store) — that table is NOT touched.
-- Run this once in Supabase dashboard -> SQL Editor.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS apex_strategy_backtests (
  id                TEXT PRIMARY KEY,        -- "{pair}_{tf}_{strategy}_{runTs}"
  run_id            TEXT,                    -- groups all rows from one run
  inserted_at       TIMESTAMPTZ DEFAULT now(),
  instrument        TEXT NOT NULL,
  asset_class       TEXT,                    -- Forex | Stock | ETF | Crypto | Futures
  timeframe         TEXT NOT NULL,           -- 1m..1w
  strategy          TEXT NOT NULL,
  strategy_family   TEXT,
  regime_filtered   BOOLEAN DEFAULT FALSE,
  data_from         TIMESTAMPTZ,             -- actual history range used
  data_to           TIMESTAMPTZ,
  n_bars            INTEGER,
  n_trades          INTEGER,
  total_return      NUMERIC,                 -- %, net of modelled spread
  sharpe            NUMERIC,                 -- annualised
  max_drawdown      NUMERIC,                 -- %
  win_rate          NUMERIC,                 -- %
  avg_win_pct       NUMERIC,
  avg_loss_pct      NUMERIC,
  avg_win_pips      NUMERIC,                 -- forex only (else null)
  avg_loss_pips     NUMERIC,
  expectancy        NUMERIC,                 -- per-trade %, net of spread
  profit_factor     NUMERIC,                 -- null = no losing trades
  low_sample        BOOLEAN,                 -- < 30 trades -> exploratory only
  shallow_sharpe    BOOLEAN,                 -- 1m/5m -> Sharpe is noisy
  regime_breakdown  JSONB,                   -- { "up/normal": {n,winRate,avgPnl}, ... }
  signal_lift       JSONB,                   -- confluence per-signal predictive power (Layer 5)
  threshold_sweep   JSONB,                   -- confluence entry-threshold distribution (Layer 5)
  params            JSONB,                   -- exact strategy params (reproducibility)
  app_version       TEXT
);

CREATE INDEX IF NOT EXISTS asb_instrument_idx ON apex_strategy_backtests (instrument);
CREATE INDEX IF NOT EXISTS asb_run_idx        ON apex_strategy_backtests (run_id);
CREATE INDEX IF NOT EXISTS asb_lookup_idx     ON apex_strategy_backtests (instrument, timeframe, strategy);
CREATE INDEX IF NOT EXISTS asb_inserted_idx   ON apex_strategy_backtests (inserted_at DESC);

ALTER TABLE apex_strategy_backtests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select" ON apex_strategy_backtests;
DROP POLICY IF EXISTS "anon_insert" ON apex_strategy_backtests;
CREATE POLICY "anon_select" ON apex_strategy_backtests FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert" ON apex_strategy_backtests FOR INSERT TO anon WITH CHECK (true);
