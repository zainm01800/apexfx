-- ══════════════════════════════════════════════════════════════════════════════
-- One-off backfill: unify apex_research_memory.asset_type onto the WEB taxonomy
-- (Crypto / Forex / ETF / Stock). Run once in Supabase dashboard → SQL Editor.
--
-- Why: the engine wrote "Equity" for everything without a slash while the web
-- app writes Stock/ETF, so the "Learning by setup" panel (asset · style · regime)
-- split every equity stat into two buckets. New engine rows already write the
-- web labels (run_live_paper_trading.py:_web_asset_type, 2026-07-19); this fixes
-- the historical rows.
--
-- Scope discipline: DETERMINISTIC mapping from the symbol only.
--   * regime is deliberately NOT backfilled — it is time-dependent; old rows
--     keep setup_features->>'regime' = 'unknown'.
--   * symbols that match no rule keep their current label (an honest unknown
--     beats a guessed one).
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. ETFs — the exact symbol set the dashboard uses (public/dashboard.js ETFS).
UPDATE apex_research_memory
SET asset_type = 'ETF'
WHERE upper(symbol) IN (
  'SPY','QQQ','IWM','GLD','SLV','USO','TLT','HYG','LQD','XLF','XLE','XLK','XLV',
  'XLI','XLC','ARKK','VTI','VOO','VNQ','EEM','EFA','GDX','GDXJ','XBI','IBB','DIA',
  'SMH','SOXX'
)
AND asset_type IS DISTINCT FROM 'ETF';

-- 2. Crypto — BASE/<quote> where BASE is in the configured crypto universe
--    (engine/config.yaml data.crypto; mirrors the dashboard crypto quick-picks).
UPDATE apex_research_memory
SET asset_type = 'Crypto'
WHERE position('/' in symbol) > 0
  AND upper(split_part(symbol, '/', 1)) IN
      ('BTC','ETH','SOL','BNB','XRP','ADA','AVAX','DOGE','MATIC','LINK','ARB','SUI')
  AND asset_type IS DISTINCT FROM 'Crypto';

-- 3. Forex — 3-letter/3-letter pair shape whose base is NOT a known crypto base.
UPDATE apex_research_memory
SET asset_type = 'Forex'
WHERE symbol ~ '^[A-Za-z]{3}/[A-Za-z]{3}$'
  AND upper(split_part(symbol, '/', 1)) NOT IN
      ('BTC','ETH','SOL','BNB','XRP','ADA','AVAX','DOGE','MATIC','LINK','ARB','SUI')
  AND asset_type IS DISTINCT FROM 'Forex';

-- 4. Stocks — the engine's old catch-all "Equity" label on plain tickers
--    (no slash, not an ETF) becomes the web's "Stock". Rows already labeled
--    Stock/ETF/Crypto/Forex are untouched.
UPDATE apex_research_memory
SET asset_type = 'Stock'
WHERE asset_type = 'Equity'
  AND position('/' in symbol) = 0
  AND upper(symbol) NOT IN (
    'SPY','QQQ','IWM','GLD','SLV','USO','TLT','HYG','LQD','XLF','XLE','XLK','XLV',
    'XLI','XLC','ARKK','VTI','VOO','VNQ','EEM','EFA','GDX','GDXJ','XBI','IBB','DIA',
    'SMH','SOXX'
  );

-- Verify: remaining label distribution (expect only Crypto/Forex/ETF/Stock +
-- the odd symbol that matched no deterministic rule).
SELECT asset_type, count(*) AS n
FROM apex_research_memory
GROUP BY asset_type
ORDER BY n DESC;
