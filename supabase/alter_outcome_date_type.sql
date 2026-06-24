-- ══════════════════════════════════════════════════════════════════════════════
-- Alter outcome_date from DATE to TIMESTAMPTZ to preserve resolution time
-- Run this once in the Supabase dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_research_memory 
  ALTER COLUMN outcome_date TYPE TIMESTAMPTZ USING outcome_date::TIMESTAMPTZ;
