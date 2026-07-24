-- ══════════════════════════════════════════════════════════════════════════════
-- APEXFX — FULL PROJECT MIGRATION (2026-07-24)
--
-- Target: the NEW Supabase project  cuvchjhaojhmxfgczndy
-- (the old project dtiuwllodzqpbwohzrgj is egress-quota-blocked and abandoned;
--  do NOT run this there).
--
-- HOW TO RUN: Supabase dashboard → SQL Editor → paste this ENTIRE file → Run.
-- It is fully idempotent (CREATE TABLE IF NOT EXISTS, DROP POLICY IF EXISTS via
-- a wipe-and-recreate DO block, publication membership checks), so re-running
-- it is safe and simply re-asserts the same end state.
--
-- WHAT IT BUILDS (consolidates every supabase/*.sql the system needs, in
-- dependency order):
--   1. all 12 apex_* tables (schemas merged with every add_*.sql column
--      migration, so no follow-up column migrations are needed)
--   2. indexes
--   3. table grants
--   4. the RLS lockdown: anon + authenticated get SELECT-ONLY on every table;
--      writes go through the service-role key (which bypasses RLS by design)
--   5. the realtime publication for the live-trading tables
--
-- AFTER RUNNING THIS FILE, ALSO RUN (same SQL editor):
--   • supabase/fix_asset_taxonomy_2026-07-19.sql  — one-off asset_type backfill
--     on apex_research_memory. It is a harmless no-op while that table is empty;
--     run it anyway so it is not forgotten if old data is ever restored.
--
-- WRITERS need the service-role key in their env (never in git, never in the
-- browser): engine/.env (SUPABASE_SERVICE_KEY), GitHub Actions secret
-- SUPABASE_SERVICE_KEY, and the Vercel project env SUPABASE_SERVICE_KEY.
-- ══════════════════════════════════════════════════════════════════════════════


-- ──────────────────────────────────────────────────────────────────────────────
-- 1. TABLES
-- ──────────────────────────────────────────────────────────────────────────────

-- AI scan memory + outcome tracking + post-mortem lessons (learning loop).
-- Base: research_memory.sql; merged: add_richer_fields, add_lesson,
-- add_setup_features, add_validations, alter_outcome_date_type.
CREATE TABLE IF NOT EXISTS public.apex_research_memory (
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

  -- richer analysis fields (add_richer_fields.sql)
  technical_analysis   TEXT,
  fundamental_analysis TEXT,
  macro_environment    TEXT,
  risk_analysis        TEXT,
  key_reasons          TEXT,
  short_term_outlook   TEXT,
  timeframe            TEXT,

  -- outcome tracking (outcome_date is TIMESTAMPTZ per alter_outcome_date_type.sql)
  outcome       TEXT DEFAULT 'pending',  -- 'pending' | 'tp_hit' | 'sl_hit' | 'expired'
  outcome_price NUMERIC,
  outcome_date  TIMESTAMPTZ,

  -- learning loop (add_lesson.sql, add_setup_features.sql, add_validations.sql)
  lesson         TEXT,
  setup_features JSONB,
  validations    JSONB,

  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Engine backtest knowledge base (DSR/PBO/CPCV validation results).
-- Base: backtests.sql.
CREATE TABLE IF NOT EXISTS public.apex_backtests (
  id                  TEXT PRIMARY KEY,   -- "{instrument}|{strategy}|{config_label}"
  instrument          TEXT NOT NULL,
  strategy            TEXT NOT NULL,
  config_label        TEXT,
  timeframe           TEXT,
  passed              BOOLEAN,
  dsr                 NUMERIC,
  pbo                 NUMERIC,
  oos_sharpe_median   NUMERIC,
  frac_positive       NUMERIC,
  n_paths             INTEGER,
  observed_sharpe_ann NUMERIC,
  config_version      INTEGER,
  generated_for       TEXT,
  inserted_at         TIMESTAMPTZ DEFAULT now()
);

-- Client-side multi-strategy backtest results (append-only, one row per
-- pair × timeframe × strategy × run). Base: apex_strategy_backtests.sql;
-- merged: add_walkforward.sql (IS/OOS columns).
CREATE TABLE IF NOT EXISTS public.apex_strategy_backtests (
  id                TEXT PRIMARY KEY,        -- "{pair}_{tf}_{strategy}_{runTs}"
  run_id            TEXT,
  inserted_at       TIMESTAMPTZ DEFAULT now(),
  instrument        TEXT NOT NULL,
  asset_class       TEXT,
  timeframe         TEXT NOT NULL,
  strategy          TEXT NOT NULL,
  strategy_family   TEXT,
  regime_filtered   BOOLEAN DEFAULT FALSE,
  data_from         TIMESTAMPTZ,
  data_to           TIMESTAMPTZ,
  n_bars            INTEGER,
  n_trades          INTEGER,
  total_return      NUMERIC,
  sharpe            NUMERIC,
  max_drawdown      NUMERIC,
  win_rate          NUMERIC,
  avg_win_pct       NUMERIC,
  avg_loss_pct      NUMERIC,
  avg_win_pips      NUMERIC,
  avg_loss_pips     NUMERIC,
  expectancy        NUMERIC,
  profit_factor     NUMERIC,
  low_sample        BOOLEAN,
  shallow_sharpe    BOOLEAN,
  regime_breakdown  JSONB,
  signal_lift       JSONB,
  threshold_sweep   JSONB,
  params            JSONB,
  app_version       TEXT,
  -- walk-forward / out-of-sample columns (add_walkforward.sql)
  is_return         DOUBLE PRECISION,
  is_sharpe         DOUBLE PRECISION,
  oos_return        DOUBLE PRECISION,
  oos_sharpe        DOUBLE PRECISION,
  oos_win_rate      DOUBLE PRECISION,
  oos_n_trades      INTEGER,
  oos_holds         BOOLEAN
);

-- Deep Analyse persistence (website writes via the anon key + its own policy
-- on the OLD project; here it is SELECT-only like everything else — writes go
-- through the service-role key). Base: apex_analyses.sql.
CREATE TABLE IF NOT EXISTS public.apex_analyses (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  symbol          TEXT,
  timeframe       TEXT,
  direction       TEXT,
  feature_vector  JSONB NOT NULL,
  analysis_text   TEXT NOT NULL,
  scorecard       JSONB,
  verdict         TEXT,
  combined_score  INTEGER,
  probability     INTEGER,
  entry_price     NUMERIC,
  sl_price        NUMERIC,
  tp_price        NUMERIC,
  method_detected TEXT,
  outcome         TEXT DEFAULT 'pending',
  verdict_correct BOOLEAN,
  outcome_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Legacy MT4 account snapshot (singleton row id=1). Base: apex_mt4_account.sql.
CREATE TABLE IF NOT EXISTS public.apex_mt4_account (
  id integer primary key default 1,
  balance double precision not null,
  equity double precision not null,
  profit double precision not null,
  free_margin double precision not null,
  leverage integer not null,
  currency text not null,
  name text,
  company text,
  start_balance double precision not null,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Legacy MT4 trade ledger. Base: apex_mt4_trades.sql; merged: add_style_column.sql.
CREATE TABLE IF NOT EXISTS public.apex_mt4_trades (
  ticket bigint primary key,
  symbol text not null,
  cmd integer not null, -- 0 = BUY, 1 = SELL
  volume double precision not null,
  open_price double precision not null,
  sl double precision,
  tp double precision,
  close_price double precision,
  profit double precision not null,
  magic bigint,
  open_time bigint not null,
  close_time bigint,
  status text not null, -- 'open' or 'closed'
  style text,
  synced_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- IBKR paper mirror: singleton account snapshot (id=1). Base: apex_ibkr.sql.
CREATE TABLE IF NOT EXISTS public.apex_ibkr_account (
  id integer primary key default 1,
  net_liquidation double precision,
  cash double precision,
  buying_power double precision,
  daily_pnl double precision,
  unrealized_pnl double precision,
  realized_pnl double precision,
  currency text,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- IBKR paper mirror: open positions (one row per instrument). Base: apex_ibkr.sql.
CREATE TABLE IF NOT EXISTS public.apex_ibkr_positions (
  instrument text primary key,
  direction text not null, -- 'long' or 'short'
  units double precision not null,
  avg_price double precision,
  market_value double precision,
  unrealized_pnl double precision,
  asset_class text not null, -- 'forex', 'stocks' or 'crypto'
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- IBKR paper mirror: fill records (exec_id = IBKR permanent execution id).
-- Base: apex_ibkr.sql.
CREATE TABLE IF NOT EXISTS public.apex_ibkr_trades (
  exec_id text primary key,
  instrument text not null,
  asset_class text not null, -- 'forex', 'stocks' or 'crypto'
  side text not null, -- 'BUY' or 'SELL'
  qty double precision not null,
  price double precision not null,
  commission double precision,
  exec_time timestamp with time zone not null,
  synced_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Forward paper portfolio: open positions (upsert on instrument; rows for
-- closed positions are deleted). Base: apex_paper_portfolio.sql.
CREATE TABLE IF NOT EXISTS public.apex_paper_positions (
  instrument          TEXT PRIMARY KEY,
  updated_at          TIMESTAMPTZ DEFAULT now(),
  direction           TEXT NOT NULL,           -- long | short
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

-- Forward paper portfolio: APPEND-ONLY daily snapshot (primary key date).
-- state_extra carries the engine restore payload — the nightly GitHub Action
-- restores the stepper from these tables on each ephemeral runner, so these
-- two tables must be seeded from engine/data_store/paper_portfolio/state.json
-- before the first CI run against this project. Base: apex_paper_portfolio.sql.
CREATE TABLE IF NOT EXISTS public.apex_paper_daily (
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

-- Per-symbol strategic knowledge summaries (build_symbol_knowledge.py writes;
-- the live engine reads before opening a trade). The CREATE for this table was
-- never checked in on the old project; schema derived from the code that uses
-- it (symbol upsert key + summary/n_trades/win_rate read by the engine).
CREATE TABLE IF NOT EXISTS public.apex_symbol_knowledge (
  symbol     TEXT PRIMARY KEY,
  summary    TEXT,
  n_trades   INTEGER,
  win_rate   DOUBLE PRECISION,
  updated_at TIMESTAMPTZ DEFAULT now()
);


-- ──────────────────────────────────────────────────────────────────────────────
-- 2. INDEXES
-- ──────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS arm_sym_date_idx ON public.apex_research_memory (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS arm_outcome_idx  ON public.apex_research_memory (outcome) WHERE outcome = 'pending';
CREATE INDEX IF NOT EXISTS apex_bt_inst_idx ON public.apex_backtests (instrument, inserted_at DESC);
CREATE INDEX IF NOT EXISTS asb_instrument_idx ON public.apex_strategy_backtests (instrument);
CREATE INDEX IF NOT EXISTS asb_run_idx        ON public.apex_strategy_backtests (run_id);
CREATE INDEX IF NOT EXISTS asb_lookup_idx     ON public.apex_strategy_backtests (instrument, timeframe, strategy);
CREATE INDEX IF NOT EXISTS asb_inserted_idx   ON public.apex_strategy_backtests (inserted_at DESC);
CREATE INDEX IF NOT EXISTS apex_analyses_user_id_idx    ON public.apex_analyses (user_id);
CREATE INDEX IF NOT EXISTS apex_analyses_user_sym_idx   ON public.apex_analyses (user_id, symbol);
CREATE INDEX IF NOT EXISTS apex_analyses_created_at_idx ON public.apex_analyses (created_at DESC);
CREATE INDEX IF NOT EXISTS apd_inserted_idx ON public.apex_paper_daily (inserted_at DESC);


-- ──────────────────────────────────────────────────────────────────────────────
-- 3. GRANTS (explicit, so API access does not depend on default privileges)
-- ──────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    t text;
    tables text[] := ARRAY[
        'apex_research_memory', 'apex_backtests', 'apex_strategy_backtests',
        'apex_analyses', 'apex_mt4_account', 'apex_mt4_trades',
        'apex_ibkr_account', 'apex_ibkr_positions', 'apex_ibkr_trades',
        'apex_paper_positions', 'apex_paper_daily', 'apex_symbol_knowledge'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('GRANT SELECT ON public.%I TO anon, authenticated', t);
        EXECUTE format('GRANT ALL ON public.%I TO service_role', t);
    END LOOP;
END $$;


-- ──────────────────────────────────────────────────────────────────────────────
-- 4. RLS LOCKDOWN — anon + authenticated are SELECT-ONLY on every table.
--    (same posture as lockdown_rls_2026-07-17.sql on the old project, extended
--     to the IBKR tables, which were created after that lockdown and were
--     still world-writable there). Writers use the service-role key, which
--     bypasses RLS by design.
-- ──────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    t text;
    p record;
    tables text[] := ARRAY[
        'apex_research_memory', 'apex_backtests', 'apex_strategy_backtests',
        'apex_analyses', 'apex_mt4_account', 'apex_mt4_trades',
        'apex_ibkr_account', 'apex_ibkr_positions', 'apex_ibkr_trades',
        'apex_paper_positions', 'apex_paper_daily', 'apex_symbol_knowledge'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        -- Drop every existing policy on the table (idempotent re-run path)
        FOR p IN SELECT policyname FROM pg_policies
                 WHERE schemaname = 'public' AND tablename = t LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', p.policyname, t);
        END LOOP;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);

        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO anon, authenticated USING (true)',
            t || '_select', t);

        RAISE NOTICE 'locked down: % (select-only for anon/authenticated)', t;
    END LOOP;
END $$;


-- ──────────────────────────────────────────────────────────────────────────────
-- 5. REALTIME — postgres_changes push for the live-trading tables
--    (supabase/enable_realtime.sql, made idempotent).
-- ──────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    t text;
    tables text[] := ARRAY[
        'apex_ibkr_account', 'apex_ibkr_positions', 'apex_ibkr_trades',
        'apex_paper_positions', 'apex_paper_daily'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_publication_tables
                       WHERE pubname = 'supabase_realtime'
                         AND schemaname = 'public' AND tablename = t) THEN
            EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
            RAISE NOTICE 'realtime enabled: %', t;
        ELSE
            RAISE NOTICE 'realtime already on: %', t;
        END IF;
    END LOOP;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE. Reminder: now run supabase/fix_asset_taxonomy_2026-07-19.sql (no-op on
-- an empty apex_research_memory, but keeps the migration chain complete).
-- ══════════════════════════════════════════════════════════════════════════════
