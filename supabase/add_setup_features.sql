-- ══════════════════════════════════════════════════════════════════════════════
-- Migration: structured setup feature vector for meta-labeling / structural retrieval
-- Run once in Supabase dashboard → SQL Editor (project cuvchjhaojhmxfgczndy).
--
-- Stores a compact normalised vector describing each scan's market structure
-- (regime, trend, momentum, volatility, confluence, + verdict side/conviction).
-- As outcomes resolve via the triple-barrier rule (tp_hit / sl_hit / expired), the
-- app retrieves STRUCTURALLY-similar past setups and measures how often the
-- committee was right on that kind of setup. This column is the substrate for a
-- future ML meta-model — without it, no setup-conditioned learning is possible.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_research_memory
  ADD COLUMN IF NOT EXISTS setup_features JSONB;
