# APEX FX — Round 3: finish what round 2 started

Round 2 review verdict: **Tier 1 shipped well (Tasks 1–3 verified, Task 4 has one gap).
Tier 2/3 mostly did not happen** — the exit A/B was built but never run, and Tasks 6–9
were skipped entirely. This round closes those. Numbering below continues from
`CLINE_TASK_2.md` (keep it for full specs; compact versions are inlined here).

## 0. Read first, then baseline

Read `CLAUDE.md`, then skim: `apex_quant/validation/portfolio_report.py`,
`apex_quant/risk/bayesian_sizer.py`, `apex_quant/execution/mt4_clock.py`,
`apex_quant/strategies/cross_sectional.py`, `scripts/validate_exit_layer.py`,
`engine/config.yaml`.

```bash
cd engine && .venv-mac/bin/python -m pytest -q -p no:warnings   # expect 314 passed
```

## Hard rules (unchanged from round 2, plus two new ones)

All of `CLINE_TASK_2.md` §Hard Rules still apply verbatim: never import
`scripts/run_live_paper_trading.py` from package/test code (except the dedicated
`test_config_mutation.py`), PostgREST ~1000-row cap (use
`apex_quant/storage/supabase_util.fetch_all_rows`), broker clock via `mt4_clock`,
0.1-pip SL/TP signature, the validation gate decides, honest `TrialLedger` counts,
**a failed verdict is a successful result**, don't touch risk caps, don't re-enable
the adaptive-LLM veto, `node --check` any JS, never state a number you didn't run.

**New rule 1 — BUILT ≠ DONE.** Round 2 built the exit A/B and never ran it. For any
research task, the deliverable is the *numbers from the run*, pasted verbatim. Code
without its result is an incomplete task.

**New rule 2 — process-global state needs an expiry.** The live broker offset taught
this: a value pushed in from outside (heartbeat, sync, cache) must carry a timestamp
and a fallback when stale. Apply this pattern anywhere you add similar state.

## Things only the user can do — flag, don't attempt
- Restart the live trading process (still required for round-1/2 config to apply).
- Verify the modified EA (`apex_mt4_bridge_zmq.mq4` now sends `server_time`) on a
  live/demo MT4 terminal.
- Confirm Task F (live timeframe retirement) before merge — it changes live behaviour.

---

## Task A — Report the exit-layer A/B numbers (was Task 5; build done, run missing)

A run of `scripts/validate_exit_layer.py` may already be in progress with output at
`engine/scratch/exit_layer_results.txt`. If that file exists and is complete, verify
it (spot-check one cell) and report; otherwise run it to completion (budget 1–2h):

```bash
cd engine && .venv-mac/bin/python scripts/validate_exit_layer.py | tee scratch/exit_layer_results.txt
```

Deliverable: the full 2×2 (RGM on EUR/USD, cross-sectional on the panel; barrier vs
managed) — each cell's gate `summary()` line plus full-period Sharpe. **No
editorialising, no re-runs with tweaked params.** If managed exits destroy value,
that is the finding; do not "fix" TradeManager in this task.

## Task B — Live broker offset must expire (closes Task 4's gap)

`mt4_clock.set_live_broker_offset()` stores a bare float forever. If the EA
disconnects, a stale offset silently outlives it — and after a DST change it would be
*confidently wrong*, which is worse than the config fallback.

1. Store `(offset_seconds, monotonic_received_at)`. `mt4_utc_offset_seconds()` uses
   the live value only if fresher than `max_age_s` (default 300s — heartbeats arrive
   every 5s, so 60 missed beats = dead EA); otherwise config fallback.
2. Log once on expiry ("live broker offset stale, falling back to config").
3. Tests: fresh live offset wins; expired falls back to config; `None` reset works;
   `test_zmq_bridge.py` heartbeat test still passes.

## Task C — Bayesian sizer learns realized payoff (was Task 6; now unblocked)

The ticket column is live (101 rows linked), so setups join to `apex_mt4_trades.profit`
exactly. Full spec in `CLINE_TASK_2.md` Task 6. Compact:

1. `record_outcome(instrument, win, pnl=None)` — decayed per-instrument means of
   |win| and |loss| sizes (backward compatible).
2. `risk_fraction()`: with ≥ `min_trades_for_adaptation` recorded PnLs, use
   `b = avg_win/avg_loss` clamped to [0.3, 3.0]; else `signal.reward_risk`.
   `describe()` says which was used.
3. Feed real profits in `initialize_bayesian_sizer_from_supabase` via the ticket
   join (hindsight-resolved trades without a fill: `win` only, no `pnl`).
4. Tests: payoff follows recorded PnLs; fallback path; lower realized b ⇒ smaller
   fraction at equal win rate; existing sizer tests untouched.

## Task D — Currency-leg cross-sectional momentum (was Task 7)

Full spec in `CLINE_TASK_2.md` Task 7. Compact: per-currency strength = mean of
vol-scaled momentum across all pairs containing it (sign-flipped when quote); rank
currencies; express top-k vs bottom-k through available pairs; shared model +
`.strategies()` mirroring `CrossSectionalMomentum`; rolling windows only.
Tests MUST include the future-poison leakage test. Then
`run_portfolio_validation` on the real 22-pair panel, every config swept recorded in
`TrialLedger`, honest `n_trials`. **Paste the verdict. Stop there either way.**

## Task E — Carry sleeve, first honest pass (was Task 8)

Full spec in `CLINE_TASK_2.md` Task 8. Compact: point-in-time CSV of central-bank
policy rates (majors, monthly, each row valid only from its effective date) under
`engine/data_store/`; a `RateProvider` over it; cross-sectional carry (rank pairs by
rate differential, long top / short bottom). Tests: point-in-time discipline +
leakage + portfolio integration. Validate through the gate, honest `n_trials`.
**Paste the verdict. Stop there either way.**

## Task F — ❌ CANCELLED BY USER DECISION (2026-07-17)

The user has explicitly instructed that 15m and 1h live trading stay ENABLED
("at any cost"). `config.yaml` records this decision next to `live_timeframes`.
**Do not remove those timeframes again without asking the user directly.** The
filter plumbing itself (config field + scanner skip + test) stays — it is the
mechanism, and the user may change the list themselves at any time.

## ~~Task F — Retire scalp/intraday from the live scanner (was Task 9)~~

1. `data.live_timeframes` (config.py `DataConfig` + `config.yaml`, set
   `["1d", "1w"]`); code default = all timeframes (backward compatible).
2. Live scanner skips any style/timeframe not listed; log each skip once per cycle,
   not per symbol.
3. Test the filter. **Changes live behaviour — present it and wait for the user's
   explicit OK before considering it merged.**

Why (measured): scalp lost in all three independent 2-year windows (−£34k/−£47k/−£72k);
live intraday −£937.

## Task G — Housekeeping

1. Promote `scratch/card_audit.py` → `scripts/audit_cards.py` (it earned its keep as
   Task 2's verification) with a docstring and a `__main__` guard.
2. Frontend: confirm unlinked cards (7 today) render the honest "unlinked/pending"
   state, never a guessed lesson. `node --check` after any change.
3. The unrequested scripts that appeared (`build_symbol_knowledge.py`,
   `bulk_regenerate_lessons.py`, `start_qwen.sh`): give each a one-line docstring
   saying what it is and whether the live loop calls it. Do not expand them.

---

## Report format

Per task: files changed → exact commands → **real pasted output** → for research
tasks the gate verdict verbatim. Finish with the full suite count (≥ 314, 0 failed)
and `node --check` on any JS touched.

Order: **B and A first** (B is 30 minutes; A's run can proceed while you do B/C),
then C, then D/E, then F/G. If D and E both fail the gate — the likely outcome —
say so plainly. That result is the system working.
