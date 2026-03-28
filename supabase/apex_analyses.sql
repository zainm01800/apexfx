-- ══════════════════════════════════════════════════════════════
-- ApexFX — AI Analysis Memory Table
-- Run this in your Supabase dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════

-- Create the table
CREATE TABLE IF NOT EXISTS apex_analyses (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  symbol          TEXT,
  timeframe       TEXT,
  direction       TEXT,               -- 'long' | 'short'
  feature_vector  JSONB NOT NULL,     -- 12-dim normalised vector [0..1]
  analysis_text   TEXT NOT NULL,
  scorecard       JSONB,              -- { entry_quality, stop_placement, ... }
  verdict         TEXT,               -- 'Strong Setup' | 'Acceptable Setup' | ...
  combined_score  INTEGER,
  probability     INTEGER,
  entry_price     NUMERIC,
  sl_price        NUMERIC,
  tp_price        NUMERIC,
  method_detected TEXT,               -- 'ICT' | 'SMC' | 'Supply & Demand' | etc.
  outcome         TEXT DEFAULT 'pending',  -- 'pending' | 'tp_hit' | 'sl_hit' | 'breakeven'
  verdict_correct BOOLEAN,
  outcome_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS apex_analyses_user_id_idx     ON apex_analyses (user_id);
CREATE INDEX IF NOT EXISTS apex_analyses_user_sym_idx    ON apex_analyses (user_id, symbol);
CREATE INDEX IF NOT EXISTS apex_analyses_created_at_idx  ON apex_analyses (created_at DESC);

-- Row Level Security — each user can only see their own analyses
ALTER TABLE apex_analyses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage their own analyses" ON apex_analyses;
CREATE POLICY "Users can manage their own analyses"
  ON apex_analyses
  FOR ALL
  USING  (auth.uid()::text = user_id OR user_id = 'anonymous')
  WITH CHECK (auth.uid()::text = user_id OR user_id = 'anonymous');
