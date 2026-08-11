"""Daily forward paper-trading stepper for the CHALLENGER book (Book B).

Pre-registered in engine/data_store/pre_registration_paper_challenger_2026-08-11.md:
a 60-day forward A/B test against the frozen proof (Book D,
scripts/run_paper_portfolio.py). The challenger is the certified
momentum-spillover-gate config (scripts/run_momentum_spillover_gate.py,
verdict CONFIRMED 2026-08-08, best challenger spill50): the Book H gold
universe (scripts/run_portfolio_gate_book_h.py) + the book crypto list + the 7
FX majors, Book D's exact parameters (lookback 252 / vol 63 / hold 21 /
rr 1.5 / rule_based regime / HTF 1w x 50, carry_filter off), plus the spill50
gate — crypto/FX LONG entries only when SPY's trailing 50-day return > 0,
SHORT entries only when < 0 (apex_quant/strategies/spillover_gate.py).
Sizing pins the gate's certified max_risk_per_trade 0.01 (CERTIFIED_MRPT),
NOT the live config value.

Reuse vs the frozen proof (import, don't copy): _top_up, the Supabase row
builders, the decision logger, the halt rule, the seed equity and the
PaperPortfolio stepping machinery all come from scripts/run_paper_portfolio.py
— which is NOT modified; invoked with no new args it behaves exactly as
before. The deliberate differences live only in THIS file:
  * the universe (Book H gold, pinned below for the same reason the A book
    pins its lists — config drift must not silently change the experiment);
  * SPY is topped up alongside the universe — it is NOT traded (Book H
    dropped it) but the gate needs its daily closes;
  * crypto/FX strategies are wrapped in SpilloverGate before stepping;
  * state persists to engine/data_store/paper_portfolio_b/ (own decisions
    log) and mirrors to the apex_paper_b_* Supabase tables — the A book's
    state file and tables are never read or written.

First run: PaperPortfolio.seed_watermark marks every closed bar up to the
penultimate one as processed, so the first advance() steps over exactly ONE
bar (the most recent closed); no history is backfilled and that bar's
decisions become PENDING-ENTRY for the next bar.

Usage (mirrors run_paper_portfolio.py):
    cd engine
    .venv-mac/bin/python scripts/run_paper_portfolio_challenger.py
    .venv-mac/bin/python scripts/run_paper_portfolio_challenger.py --no-supabase
    .venv-mac/bin/python scripts/run_paper_portfolio_challenger.py --clear-halt

Exit code 0 on success / no-op, 1 on hard failure (e.g. no usable data).
"""

from __future__ import annotations

import argparse
import copy
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.paper import PaperPortfolio  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.storage import paper_store  # noqa: E402
from apex_quant.strategies.spillover_gate import SpilloverGate, risk_on_map  # noqa: E402

from run_paper_portfolio import (  # noqa: E402
    HALT_DRAWDOWN,
    START_EQUITY,
    _daily_rows,
    _log_lines,
    _position_rows,
    _posrow_to_posd,
    _top_up,
    _utc,
)
from run_portfolio_gate import COMMON_PARAMS, MIN_BARS, WARMUP, TrendBook  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

BOOK_LABEL = "book_h_gold_252_spill50"
SPILL_L = 50
GATE_SYMBOL = "SPY"   # gate reference only — NOT in the traded universe
# The certified gate run's sizing (run_momentum_spillover_gate.CERTIFIED_MRPT);
# pinned so a config.yaml edit cannot silently resize the experiment.
CERTIFIED_MRPT = 0.01

# Identical to the gate's GOLD_PARAMS and to run_paper_portfolio.BOOK_PARAMS
# (COMMON_PARAMS has neither key, so both orderings collapse to the same dict).
BOOK_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

# PINNED UNIVERSE (2026-08-11). The certified Book H gold universe exactly as
# the spillover gate ran it: EQUITY_CORE (12 screened stocks + 3 Islamic UCITS
# + 5 kept sector ETFs) + SGLD.L, plus the book crypto list and FX majors.
# Pinned in code — byte-identical to the config values at seed time — for the
# same reason the frozen proof pins its lists (see run_paper_portfolio.py):
# config.yaml is free to grow for research without touching this experiment.
# Changing EITHER list is a new pre-registered experiment, not an edit.
BOOK_EQUITIES = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR",
    "TSM", "NFLX", "UBER",
    "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX",
    "SGLD.L",
]
BOOK_CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
    "AVAX/USD", "DOGE/USD", "MATIC/USD", "LINK/USD", "ARB/USD", "SUI/USD",
]

# MATIC/USD has no cached 1d data; the gate dropped it via the MIN_BARS skip.
# Excluded explicitly here so a future data fix cannot silently change the
# book mid-experiment (same guard as the A book).
EXCLUDED = {"MATIC/USD"}

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_b" / "state.json"
LOG_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_b" / "decisions.log"


def _cfg():
    """Live config with the certified sizing pinned (mirrors the gate's _cfg)."""
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


# ── Supabase state restore (B tables — the CI path: no local file, tables are the mirror) ──
def _restore_from_supabase() -> dict | None:
    latest = paper_store.fetch_latest_daily(table=paper_store.DAILY_TABLE_B)
    if not latest:
        return None
    extra = latest.get("state_extra") or {}
    curve = paper_store.fetch_daily_curve(table=paper_store.DAILY_TABLE_B) or []
    pos_rows = paper_store.fetch_open_positions(table=paper_store.POSITIONS_TABLE_B) or []
    return {
        "schema_version": 1,
        "book": extra.get("book", BOOK_LABEL),
        "params": extra.get("params", {**BOOK_PARAMS, "spill_L": SPILL_L}),
        "initial_equity": float(extra.get("initial_equity", START_EQUITY)),
        "realized": float(latest["cash"]),
        "peak": float(extra.get("peak", latest["equity"])),
        "halted": bool(extra.get("halted", False)),
        "cost_total": float(extra.get("cost_total", 0.0)),
        "open_positions": {r["instrument"]: _posrow_to_posd(r) for r in pos_rows},
        "pending": extra.get("pending", {}),
        "trades": extra.get("trades", []),
        "per_inst": extra.get("per_inst", {}),
        "constraint_log": extra.get("constraint_log", {}),
        "equity_curve": [[r["date"], float(r["equity"])] for r in curve],
        "last_processed_date": latest["date"],
    }


# ── main ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily forward paper step for the CHALLENGER "
                                             "book (Book H gold 252 + spill50 gate).")
    ap.add_argument("--as-of", default="",
                    help="process bars strictly before this date 00:00 UTC (default: today)")
    ap.add_argument("--state", default=str(STATE_PATH), help="local JSON state path")
    ap.add_argument("--no-supabase", action="store_true", help="skip all Supabase reads/writes")
    ap.add_argument("--clear-halt", action="store_true",
                    help="clear the experiment HALT flag after a review, then exit")
    args = ap.parse_args(argv)

    now = pd.Timestamp.now(tz="UTC")
    cutoff = _utc(args.as_of).normalize() if args.as_of else now.normalize()
    state_path = Path(args.state)
    cfg = _cfg()
    store = ParquetStore(cfg.store_path)
    instruments = [i for i in (BOOK_EQUITIES + BOOK_CRYPTO + FX_MAJORS_7)
                   if i not in EXCLUDED]

    print("=" * 72, flush=True)
    print(f"PAPER PORTFOLIO STEP | book={BOOK_LABEL} (CHALLENGER, spill_L={SPILL_L}) "
          f"| cutoff {cutoff.date()} | now {now.isoformat(timespec='seconds')}")
    print(f"universe: {len(instruments)} instruments (+{GATE_SYMBOL} gate series, not traded) "
          f"| state: {state_path}")
    print("=" * 72, flush=True)

    # 1. top up + build the panel (only fully-closed bars: strictly before cutoff).
    #    SPY is topped up the same way but stays OUT of the traded panel — the
    #    gate only needs its closes.
    adapter = get_adapter("yahoo")   # keyless, covers all 3 asset classes
    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        df = _top_up(store, adapter, inst, cutoff, now)
        df = clean(df)
        df = df[df.index < cutoff]
        if len(df) < MIN_BARS:
            print(f"  skip {inst}: {len(df)} closed bars (< {MIN_BARS})", flush=True)
            continue
        panel[inst] = df
    if len(panel) < 2:
        print("need >= 2 instruments with data; aborting", flush=True)
        return 1

    spy_df = clean(_top_up(store, adapter, GATE_SYMBOL, cutoff, now))
    spy_df = spy_df[spy_df.index < cutoff]
    if len(spy_df) < SPILL_L + 2:
        print(f"{GATE_SYMBOL} history too short for the spill{SPILL_L} gate "
              f"({len(spy_df)} bars); aborting", flush=True)
        return 1

    gated = tuple(inst for inst in panel if inst in set(BOOK_CRYPTO) | set(FX_MAJORS_7))
    risk_on = risk_on_map(spy_df["close"], panel, gated, SPILL_L)

    latest = max(df.index[-1] for df in panel.values())
    print(f"panel: {len(panel)} instruments | latest closed bar {latest.date()} | "
          f"gated (crypto+FX): {len(gated)}", flush=True)

    # 2. restore state: local JSON -> Supabase mirror (B tables) -> fresh seed
    state = PaperPortfolio.load_state_file(state_path)
    origin = "local"
    if state is None and not args.no_supabase:
        state = _restore_from_supabase()
        origin = "supabase" if state is not None else "fresh"
    elif state is None:
        origin = "fresh"

    # 3. the certified TrendBook with the spill50 wrapper on crypto/FX
    model = TrendBook(panel, **BOOK_PARAMS)
    strategies = model.strategies()
    for inst in gated:
        if inst in strategies:
            strategies[inst] = SpilloverGate(strategies[inst], risk_on[inst], inst)
    stepper = PaperPortfolio(
        panel, strategies, cfg=cfg,
        timeframes={k: "1d" for k in panel}, warmup=WARMUP,
        state=state, book=BOOK_LABEL, params={**BOOK_PARAMS, "spill_L": SPILL_L},
        halt_drawdown=HALT_DRAWDOWN, initial_equity=START_EQUITY,
    )

    if args.clear_halt:
        stepper.set_halted(False)
        stepper.save_state(state_path)
        print(f"halt flag cleared ({origin} state); review noted. Exiting.", flush=True)
        return 0

    if state is None:
        wm = stepper.seed_watermark(cutoff)
        print(f"fresh seed: watermark {wm.date() if wm is not None else '-'}; "
              f"the most recent closed bar's decisions become PENDING-ENTRY", flush=True)
    else:
        print(f"state restored from {origin} | last processed {stepper.last_processed} "
              f"| equity points {len(stepper.equity_series())}", flush=True)

    # 4. advance over all unprocessed closed bars (idempotent: none -> no-op)
    recs = stepper.advance(cutoff)
    if not recs:
        lp = stepper.last_processed
        print(f"no new closed bars since {lp.date() if lp is not None else '-'} "
              f"- nothing to do (idempotent no-op). State NOT rewritten.", flush=True)
        return 0

    lines = _log_lines(stepper, recs)
    for ln in lines:
        print(ln, flush=True)

    # 5. persist: local JSON + decisions log, then the Supabase mirror (B tables)
    stepper.save_state(state_path)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    m = stepper.metrics()
    if not m.get("insufficient_data"):
        print(f"\nmetrics to date: ret {m['total_return'] * 100:+.2f}% sharpe {m['sharpe']:.2f} "
              f"maxDD {m['max_drawdown'] * 100:.1f}% trades {m['n_trades']} "
              f"win {m['win_rate'] * 100:.0f}% PF {m.get('profit_factor')} "
              f"expectancy {m['expectancy_pnl']:+.2f}/trade", flush=True)
    print(f"embedded cost total (model spread+slippage): {stepper.cost_total:.2f} | "
          f"dd from peak {stepper.drawdown_from_peak * 100:.1f}% "
          f"| halted {stepper.halted}", flush=True)

    if not args.no_supabase:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pos_rows = _position_rows(stepper, now_iso)
        daily_rows = _daily_rows(stepper, recs, m if not m.get("insufficient_data") else None)
        ok_pos = paper_store.upsert_positions(pos_rows, table=paper_store.POSITIONS_TABLE_B)
        ok_del = paper_store.delete_positions_not_open([r["instrument"] for r in pos_rows],
                                                       table=paper_store.POSITIONS_TABLE_B)
        ok_day = paper_store.upsert_daily(daily_rows, table=paper_store.DAILY_TABLE_B)
        print(f"supabase: positions upsert {'ok' if ok_pos else 'FAILED'}, "
              f"prune {'ok' if ok_del else 'FAILED'}, daily {'ok' if ok_day else 'FAILED'}", flush=True)
        if not (ok_pos and ok_del and ok_day):
            print("  (clean degradation: local JSON state is authoritative; "
                  "the apex_paper_b_* tables must exist with the same schema/RLS as "
                  "apex_paper_positions/apex_paper_daily)", flush=True)

    pend = stepper.pending_entries
    if pend:
        print(f"\nPENDING-ENTRY for next bar ({len(pend)}):", flush=True)
        for inst, d in pend.items():
            pos = d["pos"]
            print(f"  {inst} {pos.direction.value} notional {pos.notional:,.0f} "
                  f"risk {pos.risk_fraction * 100:.2f}% stop {pos.stop_price} target {pos.target_price}", flush=True)
    print(f"\nprocessed {len(recs)} bar(s): {recs[0]['date']} -> {recs[-1]['date']} | "
          f"state saved to {state_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
