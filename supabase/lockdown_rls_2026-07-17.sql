-- ══════════════════════════════════════════════════════════════════════════════
-- RLS LOCKDOWN — 2026-07-17 (consolidated audit, finding D-C2 / J-C1)
-- Today: every apex_* table is world-writable through the public anon key
-- (policies like `FOR ALL USING (true) WITH CHECK (true)`). The anon key is in
-- the PUBLIC repo, so anyone can UPDATE/DELETE live trades, falsify the track
-- record, inject fake paper positions, and poison the AI's memory/lessons.
--
-- This migration:  (1) drops ALL existing policies on the apex_* tables,
-- (2) recreates SELECT-only policies for anon (the public site reads these),
-- (3) leaves writes to the service-role key (bypasses RLS by design).
--
-- AFTER APPLYING, you must:
--   a) Put the service-role key in the env of every WRITER (GitHub Actions
--      secrets, Vercel env, engine/.env as SUPABASE_SERVICE_KEY) — writers
--      currently using the anon key will start failing until migrated.
--   b) Rotate the anon key (Supabase dashboard → Settings → API) since the
--      current one has been public in git history.
-- Apply in the Supabase SQL editor. Idempotent (safe to re-run).
-- ══════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t text;
    p record;
    tables text[] := ARRAY[
        'apex_research_memory',
        'apex_backtests',
        'apex_strategy_backtests',
        'apex_analyses',
        'apex_mt4_account',
        'apex_mt4_trades',
        'apex_paper_positions',
        'apex_paper_daily',
        'apex_symbol_knowledge'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        -- Skip tables that don't exist yet
        IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = t) THEN
            RAISE NOTICE 'skipping missing table: %', t;
            CONTINUE;
        END IF;

        -- Drop every existing policy on the table
        FOR p IN SELECT policyname FROM pg_policies
                 WHERE schemaname = 'public' AND tablename = t LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', p.policyname, t);
        END LOOP;

        -- Make sure RLS is on
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);

        -- anon + authenticated: read-only
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO anon, authenticated USING (true)',
            t || '_select', t);

        RAISE NOTICE 'locked down: % (select-only for anon, writes = service-role)', t;
    END LOOP;
END $$;
