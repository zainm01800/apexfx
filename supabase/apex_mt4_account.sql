-- Create the apex_mt4_account table to track live account statistics
create table if not exists public.apex_mt4_account (
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

-- Enable Row Level Security (RLS)
alter table public.apex_mt4_account enable row level security;

-- Policy to allow anonymous read
create policy "Allow public read access to apex_mt4_account" 
    on public.apex_mt4_account 
    for select 
    using (true);

-- Policy to allow anonymous upsert
create policy "Allow public upsert access to apex_mt4_account" 
    on public.apex_mt4_account 
    for all 
    using (true) 
    with check (true);
