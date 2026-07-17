# APEX FX — Implementation round 2: operations, exits, learning, and two new alpha sleeves

This supersedes `CLINE_TASK.md` for this round. Nine tasks in three tiers, ordered by
dependency — do them **in order**. Every task ends with a verification step whose real
output you must include in your report.

## 0. Read the project first (do not skip)

1. `CLAUDE.md` — three-tier architecture (frontend `public/`, serverless `api/`, engine `engine/apex_quant/`).
2. `engine/apex_quant/validation/` — `report.py`, `portfolio_report.py`, `trials.py`. The gate everything must pass: **DSR > 0.95, PBO < 0.5, CPCV median OOS > 0 with majority of paths positive.**
3. `engine/apex_quant/risk/` — `manager.py` (9-stage pipeline), `bayesian_sizer.py`, `learning.py`, `trade_manager.py`, `circuit_breaker.py`.
4. `engine/apex_quant/backtest/` — `engine.py`, `portfolio.py` (note both now route exits through `TradeManager`).
5. `engine/apex_quant/execution/` — `mt4_clock.py`, `zmq_bridge.py` (two-way protocol), `mt4_executor.py`.
6. `engine/apex_quant/strategies/cross_sectional.py` — the pattern for universe-wide sleeves (`.strategies()` → dict for `PortfolioBacktester`).
7. `engine/scripts/run_live_paper_trading.py` — the LIVE loop (read-only awareness; see Hard Rules before touching).
8. `engine/config.yaml` — drives a **live MT4 account with real money**.

Baseline before any change:

```bash
cd engine && .venv-mac/bin/python -m pytest -q -p no:warnings
```

**Expect `310 passed`.** Use `engine/.venv-mac` (Python 3.12) — the tracked `.venv` is a Windows venv and will not run.

## Hard rules (each one is a scar, not a style preference)

- **NEVER import `scripts/run_live_paper_trading.py` from tests or package code.** It mutates the `lru_cache`d global config at module level (`cfg.risk.min_position = 15000.0`) — importing it silently rewrites risk config for the whole process and has broken the test suite twice. Pure logic goes in `apex_quant/`; Task 1 partially fixes this.
- **PostgREST caps responses at ~1000 rows** regardless of `limit=`. Never assume a big limit returned everything (Task 3 fixes the known cases).
- **MT4 timestamps are BROKER server time** (+3h in summer, +2h in winter), not UTC. Always go through `apex_quant/execution/mt4_clock.py`. Never `datetime.utcnow().timestamp()` (naive → interpreted as local); use `datetime.now(timezone.utc)`.
- **`(symbol, direction, SL, TP)` at 0.1-pip tolerance is the setup↔trade key.** Entry price slips and identifies nothing. Do not loosen tolerances — measured: 0.1 pip → 89% unique; 2.0 pips → 73% with 18 ambiguous.
- **The validation gate decides; nothing else does.** Count every configuration tried in `TrialLedger` and pass `n_trials=` honestly. **A candidate failing the gate is a successful result — report it and stop. Do not tune until it passes; that is the overfitting this engine exists to prevent.**
- **Do not loosen the risk caps** (`max_total_exposure: 3.0`, `max_correlated_exposure: 1.5`) or re-enable `AdaptiveWrapperStrategy`'s LLM veto in any live path.
- **`node --check` every JS file you touch** — nothing else compiles the frontend; one bad edit killed the whole MT4 tab before.
- If you change the lesson prompt in `scripts/update_lessons.py`, **bump `_LESSON_VERSION`** so history self-regenerates.
- **Never state a number you didn't run.** Show the command and its actual output.

## Things only the user can do — flag, don't attempt

- Run SQL in Supabase (Task 2 step 1).
- Restart the live trading process (fixes on disk don't apply until then — `get_config()` is cached).
- Compile/verify the MT4 EA on a live terminal (Task 4's `.mq4` half).

---

# TIER 1 — Operational integrity

## Task 1 — Startup banner + config-drift guard + kill the import-time mutation

**Why:** the live process ran for days on 100× caps that had been fixed on disk (verified live: 5.9× gross vs a 3.0 cap). Config drift between disk and memory has cost real money twice.

1. In `run_live_paper_trading.py`, replace the module-level `cfg.risk.min_position = 15000.0` mutation: add `live_min_position: 15000.0` under `execution:` in `config.yaml` + `ExecutionConfig` in `apex_quant/config.py`, and have the live scanner build its RiskManager with `cfg.risk.model_copy(update={"min_position": cfg.execution.live_min_position})` — the **shared singleton must remain untouched**. Preserve the effective live value (15000.0).
2. Startup banner: on launch print config `version`, `max_total_exposure`, `max_correlated_exposure`, effective `min_position`, `drawdown_breaker`/`reducing_limit`, and `mt4.server_utc_offset_hours`.
3. Drift guard: every N cycles (e.g. hourly), `load_config()` fresh from disk and compare the risk block to the in-memory config; log a loud warning listing each differing field ("restart required").
4. **Test** (in `tests/`): importing whatever module now holds the live-scanner logic does NOT change `get_config().risk.min_position`. Full suite still passes.

## Task 2 — Ticket column: SQL, backfill, verify

**Why:** the setup↔trade link is currently re-derived from the SL/TP signature; ~3% of cards are provably unresolvable without a recorded key (two NZD/USD setups with identical entry AND identical SL/TP produced two trades — no algorithm can split them).

1. **Ask the user to run** in Supabase SQL editor:
   ```sql
   ALTER TABLE apex_research_memory ADD COLUMN IF NOT EXISTS ticket bigint;
   CREATE INDEX IF NOT EXISTS idx_memory_ticket ON apex_research_memory(ticket);
   ```
2. `cd engine && .venv-mac/bin/python scripts/backfill_tickets.py` (dry run — include its output), then `--apply`.
3. All consuming code already prefers the column (`resolve_closed_mt4_setups` writes it, `update_lessons._match_mt4_trade` reads it first, `public/mt4-trades.js` joins on it first) — **verify, don't rebuild**.
4. **Verify:** re-run a card-level audit (match every closed `apex_mt4_trades` row to a setup; count CORRECT / unlinked / WRONG). Report before/after counts. Target: WRONG → 0 among ticket-linked rows.

## Task 3 — Paginate all Supabase reads

**Why:** PostgREST silently caps at ~1000 rows; `fetch_trades_for_learning` asks for 2000, gets ~1000, and the Bayesian sizer trains on truncated history.

1. Add `fetch_all_rows(url, headers, page_size=1000)` (offset loop or `Range` headers; stop when a page comes back short) in a shared module (e.g. `apex_quant/storage/` or a small `apex_quant/data/supabase_util.py` — NOT in the scripts).
2. Use it in: `fetch_trades_for_learning`, `fetch_resolved_trades_for_equity`, `update_lessons` fetches (incl. `_mt4_trades`), `backfill_tickets.py`, `backfill_outcomes.py`.
3. **Test:** unit-test the pagination loop with a stubbed fetcher (3 pages, last short). **Verify live:** print row counts before/after for the learning fetch — report the numbers.

## Task 4 — EA reports `TimeCurrent()` so the clock offset is exact

**Why:** the +3.0h broker offset is config (`execution.mt4.server_utc_offset_hours`) and goes stale at DST. The EA can report its own clock, making it exact.

1. `engine/scripts/apex_mt4_bridge_zmq.mq4`: include `"server_time": <TimeCurrent() as epoch>` in the heartbeat JSON. **Flag: needs on-terminal verification by the user — you cannot compile MQL4.**
2. Python: in `zmq_bridge.ExecutionProtocol.on_message`, on heartbeat compute `offset = server_time - utc_now` and expose it (e.g. `protocol.broker_offset_seconds`).
3. `mt4_clock.mt4_utc_offset_seconds()`: prefer a fresh live-reported offset when one is available; fall back to config otherwise. Keep the one-sided under-read alarm.
4. **Tests:** heartbeat with `server_time` sets the offset; absent → config fallback; stale live value → fallback.

# TIER 2 — The exit layer and honest sizing

## Task 5 — A/B validate the TradeManager exits (never been through the gate)

**Why:** the Chandelier/partial-exit `TradeManager` was wired into BOTH backtesters without ever being validated. Circumstantially, cross-sectional momentum's OOS went +0.49 → −0.29 around the time it landed. It touches every trade.

1. Add `exit_mode: Literal["managed","barrier"]` to `Backtester` and `PortfolioBacktester` (default `"managed"` = current behaviour). `"barrier"` = plain stop/target/time exit (the pre-TradeManager `_check_exit` logic — reinstate it cleanly, don't delete the managed path).
2. **Tests:** barrier mode produces trades and differs from managed mode on a fixture where a trailing stop would fire; existing parity test still passes (managed default).
3. `scripts/validate_exit_layer.py`: run **the same signal** (`CrossSectionalMomentum` lb=21 q0.30 min_universe=6 on the 22-pair daily panel, and `regime_gated_momentum` on EUR/USD) through the appropriate validator **twice** — barrier vs managed. Same params, same warmup, `n_trials` covering both modes × both signals.
4. **Report:** full-period Sharpe + gate verdict for each cell of the 2×2. Do not editorialise — the numbers answer whether the exit layer adds or destroys value.

## Task 6 — Bayesian sizer learns the *realized* payoff (depends on Task 2)

**Why:** the sizer Kelly-sizes with `b = signal.reward_risk` (1.5) but the engine exits early and realizes ~1.37:1. It sizes a hold-to-barrier strategy that isn't the one running.

1. `apex_quant/risk/bayesian_sizer.py`: track per-instrument realized payoff — decayed means of win sizes and loss sizes via an optional `pnl=` on `record_outcome(instrument, win, pnl=None)` (backward compatible).
2. `risk_fraction()`: when an instrument has ≥ `min_trades_for_adaptation` recorded PnLs, use `b = avg_win/avg_loss` (clamped to something sane like [0.3, 3.0]); otherwise fall back to `signal.reward_risk`. Record which was used in `describe()`.
3. Feed it: with the ticket column live, `initialize_bayesian_sizer_from_supabase` can join setups → `apex_mt4_trades.profit` exactly; pass that as `pnl` (hindsight-resolved trades without a broker fill: `win` only, no `pnl`).
4. **Tests:** payoff estimate follows recorded PnLs; fallback without data; a lower realized b produces a smaller risk fraction at the same win rate; existing sizer tests still pass.

# TIER 3 — New alpha (the only path to profit)

## Task 7 — Currency-leg cross-sectional momentum

**Why:** the existing sleeve ranks **pairs**, double-counting USD. The literature version ranks **currencies**. It is a different signal, not a tune of the rejected one.

1. `apex_quant/strategies/currency_momentum.py`: per-currency strength = average of vol-scaled momentum over every pair containing that currency, sign-flipped when it's the quote. Rank currencies; long pairs pairing top-k vs bottom-k currencies (bounded positions, `min_universe` gate). Mirror `CrossSectionalMomentum`'s shape: shared model + `.strategies()`. Rolling windows only.
2. **Tests:** ranking correctness on a synthetic panel (one engineered strong and weak currency), the **future-poison leakage test**, long/short bucket integrity, `PortfolioBacktester` integration.
3. Validate through `run_portfolio_validation` on the real 22-pair daily panel. Grid = every config you evaluate (lookbacks × k), all recorded in `TrialLedger`, `n_trials` honest. **Report the verdict — pass or fail — and stop there.**

## Task 8 — Carry sleeve (first honest pass)

**Why:** `features/carry.py` + `RateProvider` exist, `carry_enabled: false`. Carry is the most-documented FX factor there is.

1. v1 rate source: a bundled point-in-time CSV of central-bank policy rates (monthly, majors: USD EUR GBP JPY CHF AUD NZD CAD) under `engine/data_store/` — **each row usable only from its effective date** (no lookahead). Implement a `RateProvider` over it.
2. Cross-sectional carry sleeve (same shared-model shape): rank pairs by interest differential, long top / short bottom fraction.
3. **Tests:** point-in-time discipline (asking for a rate before its effective date returns the prior one / NaN), ranking, leakage, portfolio integration.
4. Validate through the gate, honest `n_trials`. **Report the verdict and stop.**

## Task 9 — Retire scalp/intraday from the live scanner

**Why:** scalp lost in every one of three independent 2-year backtest windows (−£34k/−£47k/−£72k) and live intraday is −£937. This is a straight config change, but it alters live behaviour — **state that clearly in your report and get user confirmation before merging.**

1. Add `data.live_timeframes: ["1d"]` (config + `DataConfig`); the live scanner skips any style/timeframe not listed. Default in code = all (backward compatible); set `["1d", "1w"]` in `config.yaml`.
2. **Test:** scanner-side filter skips a 15m/1h item when the config excludes it.

---

# Reporting format (so the review is fast)

Per task: **(a)** files changed, **(b)** exact commands run, **(c)** real output pasted, **(d)** for research tasks the gate verdict verbatim from the report's `summary()`.

Finish with:

```bash
cd engine && .venv-mac/bin/python -m pytest -q -p no:warnings   # expect >= 310 passed, 0 failed
node --check public/mt4-trades.js                                # + any other JS touched
```

Success ≠ "the engine is profitable." Success = Tier 1 shipped and verified, the exit-layer A/B answered with numbers, the sizer sizing the strategy that actually runs, and two new sleeves honestly implemented, leakage-tested, and judged by the gate — **whatever the verdict**. If both fail the gate, say so plainly; given the base rates here, that is the most likely outcome and it is a real result.
