-- Create the apex_ibkr_account table: singleton snapshot (id=1) of the IBKR
-- paper account (DUQ278370) pushed by engine/scripts/run_ibkr_mirror.py.
create table if not exists public.apex_ibkr_account (
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

-- Enable Row Level Security (RLS)
alter table public.apex_ibkr_account enable row level security;

-- Policy to allow anonymous read
create policy "Allow public read access to apex_ibkr_account"
    on public.apex_ibkr_account
    for select
    using (true);

-- Policy to allow anonymous upsert
create policy "Allow public upsert access to apex_ibkr_account"
    on public.apex_ibkr_account
    for all
    using (true)
    with check (true);

-- Create the apex_ibkr_positions table: currently-open positions on the IBKR
-- paper account. One row per instrument; stale rows are deleted on sync.
create table if not exists public.apex_ibkr_positions (
    instrument text primary key,
    direction text not null, -- 'long' or 'short'
    units double precision not null,
    avg_price double precision,
    market_value double precision,
    unrealized_pnl double precision,
    asset_class text not null, -- 'forex', 'stocks' or 'crypto'
    updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Enable Row Level Security (RLS)
alter table public.apex_ibkr_positions enable row level security;

-- Policy to allow anonymous read (so the public website can query positions)
create policy "Allow public read access to apex_ibkr_positions"
    on public.apex_ibkr_positions
    for select
    using (true);

-- Policy to allow anonymous upsert/delete (using anon API key)
create policy "Allow public upsert access to apex_ibkr_positions"
    on public.apex_ibkr_positions
    for all
    using (true)
    with check (true);

-- Create the apex_ibkr_trades table: fill records reported by the IBKR paper
-- mirror. exec_id is the IBKR permanent execution id (or a synthesised
-- fallback), so re-syncing a run merges instead of duplicating.
create table if not exists public.apex_ibkr_trades (
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

-- Enable Row Level Security (RLS)
alter table public.apex_ibkr_trades enable row level security;

-- Policy to allow anonymous read (so the public website can query fills)
create policy "Allow public read access to apex_ibkr_trades"
    on public.apex_ibkr_trades
    for select
    using (true);

-- Policy to allow anonymous upsert (using anon API key)
create policy "Allow public upsert access to apex_ibkr_trades"
    on public.apex_ibkr_trades
    for all
    using (true)
    with check (true);
