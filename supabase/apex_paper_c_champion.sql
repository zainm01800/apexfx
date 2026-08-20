-- Book C (Champion 63/126/252) paper-book mirror.
-- Run once in the Supabase SQL editor. Engine writers use the service role;
-- the browser receives SELECT-only access.

CREATE TABLE IF NOT EXISTS apex_paper_c_positions (
  instrument          TEXT PRIMARY KEY,
  updated_at          TIMESTAMPTZ DEFAULT now(),
  direction           TEXT NOT NULL,
  units               DOUBLE PRECISION,
  initial_units       DOUBLE PRECISION,
  entry_price         DOUBLE PRECISION,
  entry_time          TIMESTAMPTZ,
  entry_idx           INTEGER,
  stop                DOUBLE PRECISION,
  initial_stop        DOUBLE PRECISION,
  target              DOUBLE PRECISION,
  risk_abs            DOUBLE PRECISION,
  tf                  TEXT,
  last_px             DOUBLE PRECISION,
  bars_open           INTEGER DEFAULT 0,
  tms_p1              BOOLEAN DEFAULT FALSE,
  tms_p2              BOOLEAN DEFAULT FALSE,
  tms_be              BOOLEAN DEFAULT FALSE,
  realized_pnl_total  DOUBLE PRECISION DEFAULT 0,
  tms_log             JSONB
);

CREATE TABLE IF NOT EXISTS apex_paper_c_daily (
  date                DATE PRIMARY KEY,
  inserted_at         TIMESTAMPTZ DEFAULT now(),
  equity              NUMERIC,
  cash                NUMERIC,
  n_open              INTEGER,
  gross_exposure_x    NUMERIC,
  day_pnl             NUMERIC,
  cum_pnl             NUMERIC,
  drawdown_from_peak  NUMERIC,
  notes               TEXT,
  metrics             JSONB,
  state_extra         JSONB
);

CREATE INDEX IF NOT EXISTS apex_paper_c_daily_inserted_idx
  ON apex_paper_c_daily (inserted_at DESC);

ALTER TABLE apex_paper_c_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE apex_paper_c_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select" ON apex_paper_c_positions;
CREATE POLICY "anon_select" ON apex_paper_c_positions
  FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "anon_select" ON apex_paper_c_daily;
CREATE POLICY "anon_select" ON apex_paper_c_daily
  FOR SELECT TO anon USING (true);

GRANT SELECT ON apex_paper_c_positions, apex_paper_c_daily TO anon, authenticated;
GRANT ALL ON apex_paper_c_positions, apex_paper_c_daily TO service_role;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE apex_paper_c_positions;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE apex_paper_c_daily;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
