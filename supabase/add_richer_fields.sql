-- ══════════════════════════════════════════════════════════════════════════════
-- Migration: add richer analysis fields to apex_research_memory
-- Run this once in Supabase dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_research_memory
  ADD COLUMN IF NOT EXISTS technical_analysis   TEXT,
  ADD COLUMN IF NOT EXISTS fundamental_analysis TEXT,
  ADD COLUMN IF NOT EXISTS macro_environment    TEXT,
  ADD COLUMN IF NOT EXISTS risk_analysis        TEXT,
  ADD COLUMN IF NOT EXISTS key_reasons          TEXT,
  ADD COLUMN IF NOT EXISTS short_term_outlook   TEXT,
  ADD COLUMN IF NOT EXISTS timeframe            TEXT;
