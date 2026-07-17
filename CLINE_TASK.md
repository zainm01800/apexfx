# APEX FX — Task: find a real edge, and make the learning loop real

## 0. Read the project before you touch anything

Read these, in this order, and don't propose changes until you have:

1. `CLAUDE.md` — architecture. Three tiers: `public/` (static frontend on Vercel), `api/` (Vercel serverless), `engine/apex_quant/` (Python quant engine on Render).
2. `engine/apex_quant/validation/` — `report.py`, `portfolio_report.py`, `metrics.py`, `cpcv.py`, `trials.py`. **This is the heart of the project.** The edge of this engine is validation and risk, NOT prediction.
3. `engine/apex_quant/strategies/` — `baseline.py`, `ml_strategy.py`, `meta_labeling.py`, `cross_sectional.py`.
4. `engine/apex_quant/risk/manager.py` — the 9-stage risk pipeline. The signal proposes; the risk layer disposes.
5. `engine/apex_quant/backtest/` — `engine.py` (single-instrument), `portfolio.py` (multi-instrument, one shared book).
6. `engine/scripts/update_lessons.py` — the AI post-mortem generator.
7. `engine/config.yaml` — every tunable. No magic numbers in code.

Then establish a green baseline so you know what you're breaking:

```bash
cd engine && .venv-mac/bin/python -m pytest -q -p no:warnings
```

**Expect `292 passed`.**

> ⚠️ **Environment**: the tracked `engine/.venv` is a **Windows** venv (`Scripts/`, `.exe`) and does not run on macOS. Use **`engine/.venv-mac`** (Python 3.12). `-p no:warnings` silences a pre-existing sklearn deprecation in `ml/models.py`.

---

## 1. The single most important fact

**This engine has no validated trading edge. That is measured, not suspected.**

- **Live MT4 account**: 87 closed trades, **−£1,613**. Win rate **36.8%**, avg win £200 / avg loss £146 = **1.37:1 payoff**. A 36.8% win rate needs **1.72:1** to break even. Expectancy **−£18.54/trade**. It loses *by construction* — more trading = more loss.
- **`regime_gated_momentum`** (the live signal): CPCV/DSR/PBO on real EUR/USD, GBP/USD, USD/JPY → **negative OOS Sharpe on all 15 paths for all 3 pairs**. DSR ≈ 0.03 (needs > 0.95). PBO 0.61–0.95.
- **`cross_sectional_momentum`**: portfolio-level CPCV/DSR/PBO → **DSR 0.006, PBO 0.868**, full-period Sharpe −0.302. Rejected.

**Your job is NOT to make a backtest look good.** That is trivially easy by tuning, and this project has already been burned by it twice (§2). Your job is to find something that survives the gate — or to demonstrate that nothing does. Both are valuable. Only one is honest.

---

## 2. The failure mode you must avoid (this project's actual history)

Two previous attempts to "improve" this engine were pure overfitting:

1. **The adaptive-LLM rule generator** (`backtest/adaptive.py`). An LLM read losing trades and generated filter rules. Result: it vetoed **219 of 222** EUR/USD trades (and **all 263** on USD/CHF) to leave a few in-sample winners. That is memorising the answer key, not finding an edge. It is now quarantined behind `enable_llm_veto=False`. **Do not re-enable it in any live path.**
2. **The post-mortem lessons**. The prompt *required* an "action plan" on every trade, so it invented thresholds from n=1 ("trail at 30 pips, hold 2-3 bars"). Worse, its advice was **backwards**: it said "hold longer", but managed exits are the only thing not losing money (80 trades, **+£420**) while the 5 stop-hits cost **−£3,689**.

**Also: beware circular calibration.** A previous analysis measured "SL/TP drifts up to 68 pips" by calibrating against the existing ticket links — but those links were themselves corrupt, so the measurement was meaningless. The truth was the opposite: SL/TP match *exactly*. Whenever you calibrate a tolerance, **verify your ground truth is not the very thing you're trying to fix.**

---

## 3. Hard rules (non-negotiable)

- **Nothing counts until it clears the gate.** `DSR > 0.95`, `PBO < 0.5`, `CPCV median OOS Sharpe > 0` with a majority of paths positive. Use `validation.run_validation` (single-instrument) or `validation.run_portfolio_validation` (universe-wide sleeves like cross-sectional).
- **Count every trial honestly.** Use `validation.trials.TrialLedger` and pass `n_trials=` into the validation. Deflating by only the configs you kept is how a sweep flatters itself. Sweep 60 configs → DSR must know it's 60.
- **"It failed" is a successful outcome.** Most candidates should fail. Report failures plainly and move on. **Do not tune until something passes** — that *is* the overfitting.
- **Never touch live config without asking.** `engine/config.yaml` drives a **live MT4 account with real money**. `max_total_exposure: 3.0` and `max_correlated_exposure: 1.5` were restored from 100×/50× after that caused ~43% of the realised loss. **Do not loosen them.**
- **`node --check` every JS file you edit.** Nothing compiles the frontend — a syntax error silently kills the entire tab. This has already happened once.
- **Never state a number you haven't run.** Show the command and its real output.
- **An LLM may propose; only the validation gate may decide.** No LLM directly tunes a parameter, vetoes a signal, or sizes a position.

---

## 4. Workstream A — find a real edge (the only path to profit)

**Do not tune `regime_gated_momentum`.** It is measured dead. The bottleneck is the **primary signal** — not the gate, not the sizing, not the execution latency. Everything downstream is already good.

Find a genuinely different, **economically motivated** signal. Candidates, in rough order of prior:

- **Currency-leg cross-sectional momentum** *(most promising unexplored variant)*. `strategies/cross_sectional.py` ranks **pairs**, which double-counts USD exposure. Textbook FX cross-sectional ranks **currencies**: decompose each pair into two legs, build per-currency strength baskets, rank those, then express the trade in pairs.
- **Carry.** `features/carry.py` exists but `carry_enabled: false`. Carry is one of the few genuinely documented FX factors. Needs a rate source.
- **Value / long-horizon mean reversion** (PPP-style).
- **Positioning.** `features/cot.py` exists, `cot_enabled: false`.
- **An ensemble of weakly-correlated sleeves**, rather than one signal turned up louder.

For each candidate:

1. Implement as a `Strategy`, or a shared model + per-instrument adapters (mirror `CrossSectionalMomentum.strategies()`).
2. Unit-test it — especially **leakage safety**: poison future bars and assert the point-in-time value at `t` is unchanged.
3. Run it through the gate with an honest `n_trials`.
4. Report the DSR / PBO / CPCV verdict. Pass or fail.

---

## 5. Workstream B — make the learning mechanics actually learn

**Current truth: the lessons feed nothing.** Verify it yourself — search `engine/apex_quant/` for anything that *reads* the `lesson` field. You will find **zero consumers**. `run_live_paper_trading.py` has no feedback path (the live signal is pure quant). `scripts/consolidate_lessons.py` would build per-symbol playbooks but is **never invoked by anything**. The "AI LEARNING" badge on the dashboard cards is decorative.

So pick one and implement it honestly:

- **(a) Be honest.** Keep lessons as a human-facing review tool and stop implying the engine learns from them.
- **(b) Build a real, validated loop.** The only legitimate design:

  ```
  trade history + features
      → hypothesis (a concrete, TESTABLE config/rule)
      → run_validation / run_portfolio_validation
      → ONLY configs clearing DSR>0.95 & PBO<0.5 become eligible
      → live
  ```

  The pieces already exist: `ai/hypothesis.py`, `ai/retrieval.py` (EvidencePack of the engine's own computed facts), `strategies/meta_labeling.py`, `validation/trials.py`. This learns from **validated statistics**, not from prose about individual trades. That distinction is the entire difference between this and the 219/222 disaster.

Lesson-quality rules for `scripts/update_lessons.py` (already fixed — **keep them**):

- **Facts are templated** from the broker record. Never let the model restate a number — that's how a lesson ended up quoting £−60.85 for an £−84.60 trade.
- **The counterfactual is computed** (MFE vs distance-to-TP) and authoritative. The model previously claimed a trade "would have hit TP" on +42 pips of a 138-pip target, because it was never told the distance.
- **Base rates are injected** via `_base_rates_text()` so a single trade is judged against the book, not in a vacuum.
- **Single-trade parameter tuning is banned.** "No change warranted — n=1" is an explicitly valid answer.
- **`_LESSON_VERSION`** (`"LESSON_V2"`) gates regeneration: **bump it whenever you change the prompt** and the whole history self-upgrades at 20/cycle via the live loop.
- Consider **aggregate** lessons over per-trade ones. The real signal is in the distribution ("managed exits +£420 over 80 trades; 5 stop-hits −£3,689"), not in any single card.

---

## 6. Known outstanding items

- **`apex_research_memory` has no `ticket` column.** The setup↔trade link is currently inferred from an SL+TP signature: `(symbol, direction, SL, TP)` at **0.1 pip** uniquely identifies ~89% of trades — and **tighter is better** (loosening to 2 pips drops it to 73% with 18 ambiguous), because the engine sends SL/TP and MT4 stores them verbatim. Entry price is *not* a key; it slips. The permanent fix needs SQL only the user can run:
  ```sql
  ALTER TABLE apex_research_memory ADD COLUMN IF NOT EXISTS ticket bigint;
  CREATE INDEX IF NOT EXISTS idx_memory_ticket ON apex_research_memory(ticket);
  ```
  then `cd engine && .venv-mac/bin/python scripts/backfill_tickets.py --apply` (dry-run by default). All code already degrades safely while the column is absent.
- **The live process must be RESTARTED** for the restored exposure caps to take effect — `get_config()` is `lru_cache`d, so a running instance still holds the old 100× values.
- `scripts/apex_mt4_bridge_zmq.mq4` (the two-way ZMQ EA) is **untested on a live terminal**.
- The drawdown breaker's amber zone now **scales size down** (ramp 100%→0% between 10% and 20% drawdown) instead of blocking. It previously deadlocked — vetoing every entry, freezing trading permanently at 10%. Note that deadlock was *accidentally* limiting losses; that protection is gone.

---

## 7. How to report

For every change: **what you changed, the exact command, the real output, and the gate verdict.** If a candidate fails, say so and move on — do not tune it until it passes.

**Success is not "the engine is profitable."** Success is:

1. At least one genuinely new primary signal, honestly implemented, leakage-tested, and put through the gate with an honest trial count.
2. The learning mechanics either made real (validated loop) or honestly labelled.
3. **No regressions** — `292 passed`, and `node --check` clean on any JS touched.

If everything you try fails the gate, **say that clearly**. That is a real, useful result — and given the evidence above, it is the most likely one.
