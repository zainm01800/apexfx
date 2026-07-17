-- ══════════════════════════════════════════════════════════════════════════════
-- APEX Forward Paper Portfolio (engine-simulated, pre-registered 2026-07-17)
-- State of the frozen multi-asset trend book (Book C, lookback 126) as it is
-- stepped one daily bar at a time by engine/scripts/run_paper_portfolio.py
-- (daily GitHub Action: .github/workflows/paper-portfolio.yml).
--
--   apex_paper_positions — OPEN positions state, updated in place (upsert on
--                          instrument; rows for closed positions are deleted).
--   apex_paper_daily     — APPEND-ONLY daily snapshot of the whole book
--                          (primary key date; re-running a day merges).
--
-- The engine writes via the public anon key (same one the browser app ships
-- with) from apex_quant/storage/paper_store.py; the local JSON state
-- (engine/data_store/paper_portfolio/state.json) remains authoritative and the
-- Action restores from these tables on each ephemeral runner.
-- Run this once in Supabase dashboard -> SQL Editor.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS apex_paper_positions (
  instrument          TEXT PRIMARY KEY,
  updated_at          TIMESTAMPTZ DEFAULT now(),
  direction           TEXT NOT NULL,           -- long | short
  units               DOUBLE PRECISION,        -- current units (shrinks on TMS partials)
  initial_units       DOUBLE PRECISION,
  entry_price         DOUBLE PRECISION,        -- simulated fill, incl. modelled costs
  entry_time          TIMESTAMPTZ,
  entry_idx           INTEGER,                 -- bar index at entry (engine bookkeeping)
  stop                DOUBLE PRECISION,        -- live TradeManager stop (trails)
  initial_stop        DOUBLE PRECISION,
  target              DOUBLE PRECISION,
  risk_abs            DOUBLE PRECISION,        -- absolute risk in account currency at entry
  tf                  TEXT,                    -- '1d'
  last_px             DOUBLE PRECISION,        -- last mark (close of last processed bar)
  bars_open           INTEGER DEFAULT 0,
  tms_p1              BOOLEAN DEFAULT FALSE,   -- TradeManager partial 1 taken
  tms_p2              BOOLEAN DEFAULT FALSE,   -- TradeManager partial 2 taken
  tms_be              BOOLEAN DEFAULT FALSE,   -- breakeven stop move done
  realized_pnl_total  DOUBLE PRECISION DEFAULT 0,  -- incl. entry commission
  tms_log             JSONB                    -- TradeManager action trail
);

CREATE TABLE IF NOT EXISTS apex_paper_daily (
  date                DATE PRIMARY KEY,
  inserted_at         TIMESTAMPTZ DEFAULT now(),
  equity              NUMERIC,                 -- mark-to-market equity (GBP paper)
  cash                NUMERIC,                 -- realized cash (initial + closed pnl - costs)
  n_open              INTEGER,
  gross_exposure_x    NUMERIC,                 -- sum |notional| / equity
  day_pnl             NUMERIC,
  cum_pnl             NUMERIC,
  drawdown_from_peak  NUMERIC,                 -- fraction in [0, 1]; HALT rule at 0.15
  notes               TEXT,                    -- entries/exits/signal counts, HALT events
  metrics             JSONB,                   -- metrics-to-date (latest processed day only)
  state_extra         JSONB                    -- engine restore payload (pending, trades, caps log)
);

CREATE INDEX IF NOT EXISTS apd_inserted_idx ON apex_paper_daily (inserted_at DESC);

ALTER TABLE apex_paper_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE apex_paper_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select" ON apex_paper_positions;
DROP POLICY IF EXISTS "anon_insert" ON apex_paper_positions;
DROP POLICY IF EXISTS "anon_update" ON apex_paper_positions;
DROP POLICY IF EXISTS "anon_delete" ON apex_paper_positions;
CREATE POLICY "anon_select" ON apex_paper_positions FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert" ON apex_paper_positions FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "anon_update" ON apex_paper_positions FOR UPDATE TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_delete" ON apex_paper_positions FOR DELETE TO anon USING (true);

DROP POLICY IF EXISTS "anon_select" ON apex_paper_daily;
DROP POLICY IF EXISTS "anon_insert" ON apex_paper_daily;
DROP POLICY IF EXISTS "anon_update" ON apex_paper_daily;
CREATE POLICY "anon_select" ON apex_paper_daily FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert" ON apex_paper_daily FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "anon_update" ON apex_paper_daily FOR UPDATE TO anon USING (true) WITH CHECK (true);
