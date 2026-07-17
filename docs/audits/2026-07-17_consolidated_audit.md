# Consolidated Deep-Dive Audit — 2026-07-17

Five parallel audits (engine core, live path, data/storage, strategy/ML/AI, JS/API/DevOps),
all findings verified against code, many reproduced at runtime. This document is the single
prioritized record. Severity: CRITICAL = can lose money / corrupt data / security hole.
Effort: S/M/L.

---

## 0. What this changes about TODAY's gate results

- **All daily backtests ran on weekend-contaminated calendars** (Finding D-C1: every forex
  `*_1d.parquet` carries ~543 Sunday + up to 101 Saturday bars; 6–7-bar weeks). Lookbacks
  (21/63/126) are systematically ~10–20% shorter in effective span; CPCV folds are shifted.
  Today's REJECT verdicts are unlikely to flip (noise, not sign-changing bias), but the one
  borderline config — **vol-managed carry-trend EUR/USD (DSR 0.80–0.82, 15/15 CPCV paths,
  PF 2.02, maxDD 2.4%)** — deserves a re-run on rebuilt data (same pre-registered config;
  a data-bugfix re-run is not treadmill).
- The "EUR/USD DSR 0.869" quoted midday was a single-config sr0=0 artifact; the honest
  grid-deflated figure is **0.416**. FX final answer stands: **no certifiable FX
  configuration at retail or raw costs; FX directional iteration stops.**
- The TrialLedger race (D-H4) means concurrent gate runs may have *undercounted* trials →
  DSRs today are if anything **overstated** (passes would be harder, not easier, to earn).
  No verdict flips toward PASS.

## 1. CRITICAL — live money path (audit: live trading)

| # | Finding | Where |
|---|---|---|
| L1 | **No single-instance guard** — duplicate daemons both pass dedup → duplicate MT4 orders. Today's incident can recur. | run_live_paper_trading.py:3139 |
| L2 | **Single-slot signal file, last-write-wins** — two engine writes inside one EA poll (500ms) → first order silently lost; reversal path can lose the close and open the opposite position. | mt4_executor.py:104,124-152; apex_mt4_bridge.mq4:26,70 |
| L3 | **Symbol-level close kills ALL engine positions on that pair** — a 15m time-stop close also closes the 1h/1d/1w positions. Closes not ticket-scoped. | apex_mt4_bridge.mq4:149-167 |
| L4 | **TMS manages an arbitrary ticket** — both TMS paths match by symbol, take FIRST match; partials/SL moves can hit a sibling timeframe's position. | run_live_paper_trading.py:840-846,1091-1098 |
| L5 | **RESOLVED/AMENDED 15:10** — terminal runs **EA v3.00** (not repo's stale v1.00): ticket-scoped `modify_sl`/`partial_close` supported, per-ticket native TMS (TP1/BE/trail every 200ms). Real source now versioned at `engine/mql4/apex_mt4_bridge_v3.00_terminal_copy.mq4`. Remaining: `close` still symbol-scoped; v3.00 native TMS overlaps the new Python TradeManager — each trade needs ONE exit owner. | engine/mql4/apex_mt4_bridge_v3.00_terminal_copy.mq4 |
| L6 | **Sizing exceptions fail OPEN** — any risk-pipeline exception → order dispatched at default 0.10 lots. | :2424-2428; mt4_executor.py:207 |
| L7 | Dual-authority resolution flip-flop (Yahoo bars vs real MT4 position; outcome/lesson/sizer churn; resolution races the 5s daemon). | :1691-1704,3005-3016 |
| L8 | SL-modified trades mislabeled "expired" (exact-SL signature match; MT4 stores final trailed SL) → real P&L lost to the sizer (~11% of trades). | :2809-2834 |
| L9 | Stale MT4 files trusted blindly (no mtime check; dead EA → frozen book synced forever). | :688-704 |
| L10 | Order black hole: executor mkdirs a wrong common_dir silently; `filled_at` stamped without any ack. | mt4_executor.py:107 |
| L11 | Env/config path divergence (MT4_COMMON_DIR env honored for writes, not reads → total desync if set). | mt4_executor.py:64-67 |
| L12 | DST: live-offset fix is dead code in file mode; hardcoded 3.0 goes 1h wrong at DST. | mt4_clock.py; config.yaml:301 |

## 2. CRITICAL — data & security (audits: data/storage, JS/API)

| # | Finding | Where |
|---|---|---|
| D-C1 | **Weekend bars contaminate every daily forex cache** (~543 Sun + 53–101 Sat per pair; 6–7-bar weeks; today's dedup fixed time-of-day only, not session→date mapping). Quality checker can't see extra bars. | store.py:30-44; quality.py:99-117 |
| D-C2 | **World-writable production DB**: anon key (public repo, 22+ files) + RLS `FOR ALL USING (true)` → anyone can UPDATE/DELETE live trades, falsify the track record, inject fake paper positions (which the paper Action then ADOPTS as state), rewrite lesson outcomes. | supabase/apex_mt4_trades.sql:29-33, apex_paper_portfolio.sql:69, research_memory.sql:48-50 |
| D-C3 | **Fabricated metrics seeded** into apex_strategy_backtests ("sharpe = win_rate/10 − 5", max_dd hardcoded 12.5) — indistinguishable from real results in the UI. | scripts/seed_backtest_database.py:70-75 |
| J-C2 | **Stored XSS**: DB `lesson` fields injected into innerHTML and entity-DECODED → stranger plants a payload via D-C2 that executes for every site visitor. CSP has unsafe-inline. | public/history.js:26,715-722; mt4-trades.js:417,762-765 |
| J-C3 | **Prompt injection**: attacker-written lesson rows are injected into the public committee prompt ("treat as hard-won feedback") → steers every published verdict. | dashboard.js:1551-1561,3460-3467 |
| D-H1 | Label-convention contract false: schema claims close-time labels; all adapters deliver open-time → structural lookahead for any consumer trusting the docstring; forming bars cached (OANDA `complete:false` ignored). | schema.py:5-8; oanda_adapter.py:176-181 |
| D-H4 | TrialLedger race: non-atomic writes, no lock, torn JSON kills future runs; concurrent gates undercount N → overstated DSR. | trials.py:62-78 (9 scripts share it) |
| D-H3 | ParquetStore: non-atomic writes (torn file = permanent breakage), no mid-range hole repair, no lock. | store.py:55-104 |
| D-H5 | Schema drift ×3 stores: apex_backtests key omits timeframe; JSON cache key omits config_label (sweep configs overwrite each other); n_trials/params dropped online; upsert errors swallowed → green CI on 100% failure. | supabase_store.py:36-74; service.py:176-180 |
| D-M2 | central_bank_rates.csv ends 2025-01 → all 2026 carry lookups use Jan-2025 rates. | data/rates.py:49-54 |

## 3. CRITICAL — strategy/ML/AI layer

| # | Finding | Where |
|---|---|---|
| A-C1 | **LLM structural veto still live on real trades with NO off-switch** — up to 3 n=1 lessons + LLM "knowledge" (two layers of LLM telephone) can flatten any signal; any single same-symbol lesson FORCES a consult. The DROP verdict from today's research is unimplemented. | run_live_paper_trading.py:1789-1897,2092-2100 |
| A-C2 | Hardcoded Supabase JWT in update_lessons.py:17, build_symbol_knowledge.py:15-19, consolidate_lessons.py:21 → attacker can inject forged "lessons" into the live veto prompt. | (rotate key) |
| A-H1 | **"Calibrated probabilities" are hand-invented** (`0.52 + 0.06|score|` in every sleeve, bypass_calibration=True) → Kelly sees p≥0.52 everywhere → nearly every signal lands at the 2% cap; the no-edge gate almost never fires. Sizing looks disciplined, discriminates nothing. | baseline.py:84,240; risk/manager.py:218 |
| A-H2 | Bayesian sizer floors negative Kelly to min_risk instead of vetoing — demonstrated-loser instruments keep getting bet on (live). | bayesian_sizer.py:328-331 |
| A-H3 | Veto risk flags mis-annualized on intraday (252 for all TFs; rvol understated ~4.9× on 1h) and asset-class-blind (crypto always "vol spike"). | run_live_paper_trading.py:1816-1819; ml/dataset.py:55 |
| A-M2 | AdaptiveBacktester mines rules in-sample and re-scores same data; `experimental=True` enforced only by a unit test. | backtest/adaptive.py:151-240 |
| (good) | AI can never create a position; CPCV purge/embargo correct; probation discipline held; sizer posterior fed by hindsight outcomes, not lessons. | verified |

## 4. HIGH — engine core

| # | Finding | Where |
|---|---|---|
| E1 | Portfolio/paper time-stops ignore holding_horizon (parity test root cause; managed mode falls back to defaults). | portfolio.py:176 vs engine.py:100-102 |
| E2 | News filter reads WALL CLOCK in backtests (engines never pass bar time) → non-deterministic backtests; dynamic calendar dead (app_url ""). | manager.py:136 |
| E3 | `_global_bb_cache` cross-contamination still live in baseline (fixed elsewhere); unbounded memory in live process. | baseline.py:208 |
| E4 | Engine-level regime uses unscaled eps → reads "ranging" on intraday → damps sizes 40-50%; live passes no regime → backtest/live sizing mismatch. | engine.py:50; portfolio.py:89; baseline.py:110-129 |
| E5 | Intraday annualization wrong (252 for all bar sizes) → 1h Sharpe understated ~4.9×; ALL 15m/1h artifacts mis-scaled. | engine.py:216; result.py:47-54 |
| E6 | Book risk overstated after partials/BE → portfolio cap blocks entries it shouldn't. | portfolio.py:362-369 |
| E7 | BE buffer math broken (3e-8 vs intended 3 pips) → managed BE exits book −cost every time. | trade_manager.py:134 |
| E8 | Managed-mode exit prices recorded cost-free; cross-mode inconsistency feeding post-mortems. | engine.py:188; portfolio.py:249 |

## 5. HIGH — JS/API/DevOps

| # | Finding | Where |
|---|---|---|
| J-H1 | Two divergent outcome graders (history.js admits entry bar; proximity-watch requires full bar) — first writer wins. | history.js:182; proximity-watch.mjs:76 |
| J-H2 | Optimistic TP-before-SL grading flatters the public track record vs both backtests (which are stop-first). | history.js:217-223 |
| J-H3 | JS Lab charges forex 2bps vs engine ~9-10bps → same strategy "profitable" in Lab, fails validation. | strategies.js:108-115 |
| J-H4 | /api/ai unauthenticated + open-proxy SSRF via client-supplied localLlmUrl; /api/ws-token leaks Finnhub key. | api/ai.js:37-45,128-146; ws-token.js:39 |
| J-M6 | MT4_BROKER_OFFSET_HOURS=2 hardcoded (EET is UTC+3 summer) → displayed times off 1h right now. | mt4-trades.js:10 |

## 6. FIX PLAN (priority order)

**Tier 1 — money & security (this week):**
1. Live-path hardening: startup lockfile; ticket-scoped close/partial/modify (fills handshake);
   fail-closed sizing; freshness checks on MT4 files; verify deployed EA version (USER: check MT4).
1a. ✅ DONE (2026-07-17): IBKR live-paper provider — `execution.provider: "ibkr"` routes the live FX
   book to the IBKR paper account (DUQ278370) via IBKRLiveBridge (virtual tickets = permIds, venue-side
   OCA STP+LMT brackets, ledger in `engine/data_store/ibkr_live_book.json`, sync to `apex_ibkr_*`);
   rollback = provider `mt4`. Offline proof: `engine/scratch/smoke_live_ibkr.py` 66/66,
   `smoke_live_hardening.py` 40/40 (mt4 path unchanged). USER flips the provider at the maintenance window.
2. Supabase lockdown: service-role key for all writes (env secrets), anon = SELECT-only;
   rotate keys (USER: dashboard + apply SQL + GitHub/Vercel secrets).
3. Data integrity: session-calendar layer (reject weekend FX bars, one session→date
   convention), atomic writes + locks, forming-bar filter; then REBUILD all 1d caches from
   OANDA + backfill 1h holes; re-run the borderline vol-managed EUR/USD gate on clean data.
4. Kill the LLM structural veto in live (config flag, default off) — implements the DROP verdict.
5. Bayesian sizer: veto on non-positive Kelly after adaptation.

**Tier 2 — correctness:** time-stop parity (per-call max_bars); pass bar-time into permit();
BB cache → WeakKeyDict; regime eps alignment; per-TF annualization; BE buffer fix;
book-risk-after-partials fix; rates CSV update + staleness warning; one pessimistic grader
shared by JS; JS cost model aligned to engine; probability calibration or Bayesian-only sizing.

**Tier 3 — hygiene:** unified results store (key incl. timeframe + params + n_trials);
purge fabricated seed rows; XSS escape + CSP; API auth/rate-limit; .gitignore generated
data files; dead code removal (engine-logs.js, historical-scan.js, check_single_trade);
startup fail-closed config validation; weekly Action honesty (fail on upsert failure,
no "fresh data" claim).

## 7. USER ACTION LIST
1. Apply `supabase/apex_paper_portfolio.sql` in the Supabase SQL editor (paper Action fails fast without it).
2. Check which EA version the MT4 terminal is actually running (repo v1.00 silently drops modify_sl/partial_close).
3. Supabase: apply lockdown migration (to be written), move writers to service-role key, rotate the anon key.
4. Do NOT start a second daemon — ever (check `ps aux | grep run_live_paper_trading` first).
