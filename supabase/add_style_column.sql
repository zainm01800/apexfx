-- Add style column to apex_mt4_trades table
alter table public.apex_mt4_trades add column if not exists style text;
