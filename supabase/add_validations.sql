-- ══════════════════════════════════════════════════════════════════════════════
-- Migration: trade "validity re-checks" (validations)
-- Run once in Supabase dashboard → SQL Editor (project dtiuwllodzqpbwohzrgj).
--
-- The History "Update" button re-runs the full analysis on an existing trade WITHOUT
-- creating a new trade. Each re-check appends a record here describing whether the
-- original call still holds: the fresh verdict + confidence, how far price has moved
-- toward the target vs the stop, and an assessment (confirmed / weakening /
-- invalidated). Stored as an array per trade so the learning loop can later correlate
-- "a trade was flagged weakening" with how it actually resolved — i.e. measure whether
-- re-validation itself improves the edge.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_research_memory
  ADD COLUMN IF NOT EXISTS validations JSONB;
