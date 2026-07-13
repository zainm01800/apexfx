-- Create the apex_mt4_trades table to track live execution from MT4
create table if not exists public.apex_mt4_trades (
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
    synced_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Enable Row Level Security (RLS)
alter table public.apex_mt4_trades enable row level security;

-- Policy to allow anonymous read (so the public website can query active trades)
create policy "Allow public read access to apex_mt4_trades" 
    on public.apex_mt4_trades 
    for select 
    using (true);

-- Policy to allow anonymous upsert (using anon API key)
create policy "Allow public upsert access to apex_mt4_trades" 
    on public.apex_mt4_trades 
    for all 
    using (true) 
    with check (true);
