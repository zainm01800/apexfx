-- Enable Supabase Realtime push on the live-trading tables.
-- Run ONCE in the Supabase SQL editor (same place you ran apex_ibkr.sql).
-- After this, the website's IBKR Terminal / History pages receive row changes
-- instantly over a websocket — no refresh, no polling.
-- If any line errors with "already member of publication", that's fine — it
-- means that table was already enabled; the rest still apply.

alter publication supabase_realtime add table public.apex_ibkr_account;
alter publication supabase_realtime add table public.apex_ibkr_positions;
alter publication supabase_realtime add table public.apex_ibkr_trades;
alter publication supabase_realtime add table public.apex_paper_positions;
alter publication supabase_realtime add table public.apex_paper_daily;
