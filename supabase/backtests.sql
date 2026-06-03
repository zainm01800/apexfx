-- ══════════════════════════════════════════════════════════════════════════════
-- APEX Backtest Knowledge Base
-- A persistent store of every backtest + CPCV/DSR/PBO validation the engine runs,
-- across instruments / strategies / configs. The Deep Analyse reads a per-symbol
-- summary and feeds it to the committee, so the AI's verdict is informed by what
-- has actually survived testing.
-- Run this once in Supabase dashboard -> SQL Editor.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS apex_backtests (
  id                  TEXT PRIMARY KEY,   -- "{instrument}|{strategy}|{config_label}"
  instrument          TEXT NOT NULL,
  strategy            TEXT NOT NULL,
  config_label        TEXT,
  timeframe           TEXT,
  passed              BOOLEAN,            -- cleared DSR + PBO + CPCV gates
  dsr                 NUMERIC,            -- Deflated Sharpe (prob, need > 0.95)
  pbo                 NUMERIC,            -- Prob. of Backtest Overfitting (need < 0.5)
  oos_sharpe_median   NUMERIC,
  frac_positive       NUMERIC,            -- fraction of CPCV OOS paths positive
  n_paths             INTEGER,
  observed_sharpe_ann NUMERIC,
  config_version      INTEGER,
  generated_for       TEXT,
  inserted_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS apex_bt_inst_idx ON apex_backtests (instrument, inserted_at DESC);

ALTER TABLE apex_backtests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select" ON apex_backtests;
DROP POLICY IF EXISTS "anon_insert" ON apex_backtests;
DROP POLICY IF EXISTS "anon_update" ON apex_backtests;
CREATE POLICY "anon_select" ON apex_backtests FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert" ON apex_backtests FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "anon_update" ON apex_backtests FOR UPDATE TO anon USING (true) WITH CHECK (true);
