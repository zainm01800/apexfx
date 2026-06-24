# APEX FX

Multi-asset (FX / equities / crypto) trading-analysis dashboard. Three tiers:

1. **Frontend** (`public/`) — static app on Vercel (`apexfx.vercel.app`). Tabs: Research (`dashboard.html`), Charts (`index.html`), History, Backtest. (The standalone Quant page was removed 2026-06-04 — the quant engine still feeds Deep Analyse inline via `fetchEngineData` + the `/api/quant` proxy.) Shared pure libs in `public/lib/*`.
2. **Vercel serverless API** (`api/`) — keeps keys server-side; proxies Groq (AI), Finnhub, Yahoo; Supabase-backed `memory`/`backtests`/`backtest-runs`/`quality-scores`.
3. **Python quant engine** (`engine/apex_quant/`) — FastAPI on Render (`apex-quant-engine.onrender.com`). Signal/risk/**validation** engine; edge = risk mgmt + regime + validation, NOT prediction. Tests: `cd engine && .venv\Scripts\python.exe -m pytest -q`.

## ALWAYS: keep memory up to date

Memory dir: `C:\Users\zainm\.claude\projects\C--Users-zainm-OneDrive-Documents-GitHub\memory\`

On **every change you make** to this project (code edit, commit, deploy, config change, decision), append a dated entry to `apexfx-recent-work.md` (newest first) describing what changed and why, and update the "Working-tree state" section. Keep `apexfx-overview.md` current if the architecture changes. Update `MEMORY.md` if you add a new memory file. Do this as part of completing the change — not only when asked.
