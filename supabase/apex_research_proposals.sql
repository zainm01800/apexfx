-- ══════════════════════════════════════════════════════════════════════════════
-- ApexFX Auto-Researcher — experiment proposal review queue
-- (Part 2 of the research loop: engine/scripts/run_auto_researcher.py drafts
-- 1-2 NEW experiment proposals per week into this table; the site's Progress
-- tab renders them via /api/progress.)
--
-- DISCIPLINE: every row is a DRAFT for human review (status='draft'). Nothing
-- in this table is ever executed by the engine — no auto-runs, no trades.
--
-- Run this once in Supabase dashboard → SQL Editor. Idempotent (safe to re-run).
-- Writes use the service-role key (anon is SELECT-only since the 2026-07-17
-- RLS lockdown, see supabase/lockdown_rls_2026-07-17.sql).
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS apex_research_proposals (
  id                TEXT PRIMARY KEY,                  -- slug(title) + run date; deterministic, so re-runs upsert
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  title             TEXT NOT NULL,
  summary           TEXT,
  mechanism         TEXT,                              -- the economic/behavioural reason the edge should exist
  evidence_links    JSONB DEFAULT '[]'::jsonb,         -- external references (may be empty)
  suggested_configs JSONB DEFAULT '[]'::jsonb,         -- <=2 concrete configs to test
  kill_criterion    TEXT,                              -- the pre-registered falsification rule
  status            TEXT DEFAULT 'draft',              -- 'draft' | 'accepted' | 'rejected' | 'parked'
  source            TEXT DEFAULT 'auto-researcher'
);

-- Fast lookups for the review queue (newest first / by status)
CREATE INDEX IF NOT EXISTS arp_created_idx ON apex_research_proposals (created_at DESC);
CREATE INDEX IF NOT EXISTS arp_status_idx  ON apex_research_proposals (status);

-- Row Level Security: public read (the site's Progress tab reads via the anon
-- key); writes go through the service-role key, which bypasses RLS by design.
ALTER TABLE apex_research_proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "apex_research_proposals_select" ON apex_research_proposals;
CREATE POLICY "apex_research_proposals_select" ON apex_research_proposals
  FOR SELECT TO anon, authenticated USING (true);
