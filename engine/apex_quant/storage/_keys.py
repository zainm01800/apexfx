"""Supabase auth-key resolution for engine writers.

The 2026-07-17 RLS lockdown (supabase/lockdown_rls_2026-07-17.sql) makes the
public anon key SELECT-only on the ``apex_*`` tables, so every writer must
authenticate with the service-role key (which bypasses RLS by design).
Resolution order:

1. ``SUPABASE_SERVICE_KEY`` env (engine/.env, GitHub/Vercel secrets) — preferred
2. ``SUPABASE_ANON_KEY`` env — legacy override
3. the hardcoded public anon key — fallback so nothing breaks before the
   service key is deployed (the anon key is shipped to the browser, not a secret)
"""

from __future__ import annotations

import os

# Public anon key for the dtiuwllodzqpbwohzrgj project (browser-visible, not a secret).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0."
    "fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
)


def service_or_anon_key() -> str:
    """Service-role key when set, else the anon key.

    Resolved per call (not at import time) so ``load_dotenv()`` ordering in
    the calling script never matters.
    """
    return (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ANON_KEY
    )
