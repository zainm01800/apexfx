-- ══════════════════════════════════════════════════════════════════════════════
-- ApexFX Research Memory — AI analysis persistence & outcome tracking
-- Run this once in Supabase dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS apex_research_memory (
  id            TEXT PRIMARY KEY,
  symbol        TEXT NOT NULL,
  asset_type    TEXT,
  analysis_date DATE NOT NULL DEFAULT CURRENT_DATE,
  price         NUMERIC,
  verdict       TEXT,
  confidence    INTEGER,
  target_price  TEXT,
  entry_zone    TEXT,
  stop_loss     TEXT,
  risk_reward   TEXT,
  summary       TEXT,

  -- Outcome tracking (updated after price resolves)
  outcome       TEXT DEFAULT 'pending',  -- 'pending' | 'tp_hit' | 'sl_hit' | 'expired'
  outcome_price NUMERIC,
  outcome_date  DATE,

  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Fast lookups by symbol
CREATE INDEX IF NOT EXISTS arm_sym_date_idx ON apex_research_memory (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS arm_outcome_idx  ON apex_research_memory (outcome) WHERE outcome = 'pending';

-- Row Level Security (open to anon — anon key is public in the frontend)
ALTER TABLE apex_research_memory ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select" ON apex_research_memory;
DROP POLICY IF EXISTS "anon_insert" ON apex_research_memory;
DROP POLICY IF EXISTS "anon_update" ON apex_research_memory;

CREATE POLICY "anon_select" ON apex_research_memory FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert" ON apex_research_memory FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "anon_update" ON apex_research_memory FOR UPDATE TO anon USING (true) WITH CHECK (true);
