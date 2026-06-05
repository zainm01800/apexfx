-- ══════════════════════════════════════════════════════════════════════════════
-- Migration: per-trade post-mortem "lesson"
-- Run once in Supabase dashboard → SQL Editor (project dtiuwllodzqpbwohzrgj).
--
-- When a scan resolves via the triple-barrier rule (tp_hit / sl_hit / expired) the
-- app generates a short AI post-mortem: what the thesis got right or wrong and what
-- to watch next time. It is stored here, shown on the History card, and — most
-- importantly — the live committee retrieves lessons from STRUCTURALLY-similar past
-- setups (same regime/trend/momentum/vol/confluence, not the same ticker) and feeds
-- them into the verdict so the engine stops repeating the same mistake. This is the
-- qualitative complement to the statistical calibration + setup-reliability loops.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_research_memory
  ADD COLUMN IF NOT EXISTS lesson TEXT;
