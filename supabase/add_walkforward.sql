-- ══════════════════════════════════════════════════════════════════════════════
-- Migration: walk-forward / out-of-sample backtest columns
-- Run once in Supabase dashboard → SQL Editor (project dtiuwllodzqpbwohzrgj).
--
-- Each backtest now also reports how the SAME fixed strategy did on a held-out
-- recent slice of the data (70/30 split): the in-sample numbers always flatter a
-- strategy, so the OUT-OF-SAMPLE result is the honest "did it keep working on
-- unseen data?" read. The Backtest Lab surfaces it and Deep Analyse weights an
-- OOS-surviving strategy far more than an in-sample-only one. Until this runs the
-- app strips these fields on save (graceful), so nothing breaks — but OOS won't
-- accrue until the columns exist.
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE apex_strategy_backtests
  ADD COLUMN IF NOT EXISTS is_return    DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS is_sharpe    DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS oos_return   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS oos_sharpe   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS oos_win_rate DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS oos_n_trades INTEGER,
  ADD COLUMN IF NOT EXISTS oos_holds    BOOLEAN;
