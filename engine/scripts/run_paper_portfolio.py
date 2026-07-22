"""Daily forward paper-trading stepper for the FROZEN multi-asset trend book.

Pre-registered in engine/data_store/pre_registration_paper_trend_2026-07-17.md
(as amended 2026-07-17, change log #1: Book C 126 -> Book D 252 after the
clean-data re-run showed D dominates; selection documented, clock restarted):
the book is the gate's Book D ("book_d_multiasset_252") - 24 equities +
11 crypto + 7 FX majors, TrendBook signal stack (RegimeGatedMomentum wrapped in
MultiTimeframeMomentum, lookback 252 / vol 63 / hold 21 / rr 1.5 / rule_based
regime / HTF 1w x 50), managed exits, vol-scaled sizing, config risk caps
binding, v5 per-asset-class costs, start equity GBP 100,000. NO parameter
changes: any change restarts the experiment clock.

Parity with the backtester is by construction - this script is thin glue:
  * TrendBook / COMMON_PARAMS / WARMUP / MIN_BARS are IMPORTED from
    scripts/run_portfolio_gate.py (the gate's own adapter), FX_MAJORS_7 from
    scripts/run_portfolio_gate_multiasset.py;
  * the stepping itself is PaperPortfolio (apex_quant/backtest/paper.py), a
    PortfolioBacktester subclass whose step() ports run()'s loop body 1:1 over
    persisted state - same RiskManager, TradeManager, regime classifier and v5
    cost mechanics; proven by tests/test_paper_portfolio.py's parity test.

Each invocation (one per day from .github/workflows/paper-portfolio.yml):
  1. tops up the parquet cache with newly closed daily bars (Yahoo tail fetch;
     only bars strictly BEFORE today 00:00 UTC are processed, so a re-run on
     the same day is a no-op - idempotent);
  2. rebuilds the panel + TrendBook and restores the persisted paper portfolio
     (local JSON first, Supabase mirror second, fresh seed otherwise);
  3. advances it over every unprocessed closed bar (usually one; catch-up after
     downtime processes several, in order), logging every decision;
  4. persists state to engine/data_store/paper_portfolio/state.json and to
     Supabase (apex_paper_positions / apex_paper_daily - degradation is clean:
     a missing table or offline Supabase never fails the run).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_paper_portfolio.py                  # normal daily step
    .venv-mac/bin/python scripts/run_paper_portfolio.py --as-of 2026-07-17
    .venv-mac/bin/python scripts/run_paper_portfolio.py --no-supabase
    .venv-mac/bin/python scripts/run_paper_portfolio.py --clear-halt     # after a 15%-DD review

Exit code 0 on success / no-op, 1 on hard failure (e.g. no usable data).
"""

from __future__ import annotations

import argparse
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
from apex_quant.data import ParquetStore, clean, get_adapter, normalize_day_bars  # noqa: E402
from apex_quant.storage import paper_store  # noqa: E402

from run_portfolio_gate import COMMON_PARAMS, MIN_BARS, WARMUP, TrendBook  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

BOOK_LABEL = "book_d_multiasset_252"
# AMENDMENT 2026-07-17 (pre-reg change log #1): book upgraded C(126) -> D(252).
# Reason: the original selection was made on weekend-contaminated data; the
# clean-data re-run (portfolio_gate_multiasset_2026-07-17.md) showed D dominates
# (Sharpe 0.97 vs 0.68, PF 1.41 vs 1.26, 14/15+ positive CPCV paths both).
# Selection acknowledged as a one-time, documented, day-1 change (no fills had
# occurred); experiment clock restarts tonight. COMMON_PARAMS itself is left
# untouched — gate scripts reference it for Book C comparisons.
BOOK_PARAMS = {**COMMON_PARAMS, "momentum_lookback": 252, "carry_filter": False}

# The frozen gate universe is equities + crypto + FX majors with MATIC/USD
# dropped (no cached 1d data at gate time). Excluded explicitly so a future
# MATIC data fix cannot silently change the book mid-experiment.
EXCLUDED = {"MATIC/USD"}

# PINNED UNIVERSE (2026-07-22). These lists were previously read live from
# config.yaml, which meant ANY edit to cfg.data.equities/crypto silently changed
# what the frozen forward experiment traded on its next step — the same class of
# accident the MATIC exclusion above was written to prevent, but across the whole
# book. They are now pinned in code, byte-identical to the config values the
# experiment started with on 2026-07-17, so the research/scan universe in
# config.yaml can grow freely without touching the experiment of record.
# Changing EITHER list is a new pre-registered experiment, not an edit.
BOOK_EQUITIES = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR",
    "TSM", "NFLX", "UBER", "SPY", "QQQ", "IWM", "GLD", "TLT", "XLK", "XLE",
    "XLF", "ARKK", "SMH", "SOXX", "XBI",
]
BOOK_CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
    "AVAX/USD", "DOGE/USD", "MATIC/USD", "LINK/USD", "ARB/USD", "SUI/USD",
]

FETCH_START = "2014-01-01"          # same depth as scripts/run_backtests.py
HALT_DRAWDOWN = 0.15                # pre-registered experiment HALT rule
START_EQUITY = 100_000.0            # GBP paper equity

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"
LOG_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "decisions.log"


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


# ── data top-up (tail-only; the frozen history in the cache is never restated) ──
def _top_up(store: ParquetStore, adapter, inst: str, cutoff: pd.Timestamp,
            now: pd.Timestamp) -> pd.DataFrame:
    """Append newly published daily bars to the cache and return the full frame.

    Fetches only from the last cached bar forward (inclusive, so a possibly
    partial final bar is replaced by its settled version). On any fetch failure
    the stale cache is used as-is - the step simply processes whatever closed
    bars exist."""
    cached = store.load(inst, "1d")
    last = cached.index[-1] if not cached.empty else None
    if last is not None and _utc(last) >= cutoff:
        return cached
    fetch_start = _utc(last) if last is not None else _utc(FETCH_START)
    try:
        fetched = adapter.get_history(inst, fetch_start, now, "1d")
    except Exception as e:  # noqa: BLE001
        print(f"  warn: top-up failed for {inst} ({type(e).__name__}: {e}); using cache", flush=True)
        return cached
    if fetched.empty:
        return cached
    combined = pd.concat([cached, fetched])
    combined = normalize_day_bars(combined, "1d")
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    store.save(inst, combined, "1d")
    return combined


# ── Supabase state restore (the CI path: no local file, tables are the mirror) ──
def _posrow_to_posd(r: dict) -> dict:
    return {
        "symbol": r["instrument"], "direction": r["direction"],
        "units": float(r["units"]), "initial_units": float(r["initial_units"]),
        "entry_price": float(r["entry_price"]), "entry_time": r["entry_time"],
        "entry_idx": int(r.get("entry_idx") or 0),
        "stop": float(r["stop"]), "initial_stop": float(r["initial_stop"]),
        "target": float(r["target"]), "risk_abs": float(r["risk_abs"]),
        "tf": r["tf"], "last_px": float(r["last_px"]),
        "tms_p1": bool(r["tms_p1"]), "tms_p2": bool(r["tms_p2"]), "tms_be": bool(r["tms_be"]),
        "bars_open": int(r["bars_open"]), "tms_log": r.get("tms_log") or [],
        "realized_pnl_total": float(r.get("realized_pnl_total") or 0.0),
    }


def _restore_from_supabase() -> dict | None:
    latest = paper_store.fetch_latest_daily()
    if not latest:
        return None
    extra = latest.get("state_extra") or {}
    curve = paper_store.fetch_daily_curve() or []
    pos_rows = paper_store.fetch_open_positions() or []
    return {
        "schema_version": 1,
        "book": extra.get("book", BOOK_LABEL),
        "params": extra.get("params", BOOK_PARAMS),
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


# ── Supabase row builders ──────────────────────────────────────────────────────
def _position_rows(stepper: PaperPortfolio, now_iso: str) -> list[dict]:
    rows = []
    for inst, p in stepper.open_positions.items():
        rows.append({
            "instrument": inst,
            "direction": p["direction"].value,
            "units": p["units"], "initial_units": p["initial_units"],
            "entry_price": p["entry_price"],
            "entry_time": _utc(p["entry_time"]).isoformat(),
            "entry_idx": int(p.get("entry_idx", 0)),
            "stop": p["stop"], "initial_stop": p["initial_stop"], "target": p["target"],
            "risk_abs": p["risk_abs"], "tf": p["tf"], "last_px": p["last_px"],
            "bars_open": int(p.get("bars_open", 0)),
            "tms_p1": bool(p.get("tms_p1", False)),
            "tms_p2": bool(p.get("tms_p2", False)),
            "tms_be": bool(p.get("tms_be", False)),
            "realized_pnl_total": p.get("realized_pnl_total", 0.0),
            "tms_log": p.get("tms_log", []),
            "updated_at": now_iso,
        })
    return rows


def _daily_rows(stepper: PaperPortfolio, recs: list[dict], metrics: dict) -> list[dict]:
    st = stepper.to_state()
    extra = {k: st[k] for k in (
        "book", "params", "initial_equity", "peak", "halted", "cost_total",
        "pending", "trades", "per_inst", "constraint_log",
    )}
    rows, prev_eq = [], stepper.initial_equity
    eq_series = stepper.equity_series()
    if len(eq_series) > len(recs):
        prev_eq = float(eq_series.iloc[-len(recs) - 1])
    for j, rec in enumerate(recs):
        eq = rec["equity"]
        notes = (
            f"entries {len(rec['entries'])}, exits {len(rec['exits'])}, "
            f"signals {len([d for d in rec['decisions'] if d['permitted']])} permitted/"
            f"{len([d for d in rec['decisions'] if not d['permitted']])} vetoed/"
            f"{rec['n_flat_signals']} flat"
        )
        if rec.get("halt_triggered"):
            notes += f" | HALT TRIGGERED: drawdown >= {HALT_DRAWDOWN:.0%} from peak"
        elif rec.get("halted"):
            notes += " | halted (new entries blocked)"
        rows.append({
            "date": rec["date"],
            "equity": round(eq, 2),
            "cash": round(rec["cash"], 2),
            "n_open": rec["n_open"],
            "gross_exposure_x": round(rec["gross_exposure_x"], 4),
            "day_pnl": round(eq - prev_eq, 2),
            "cum_pnl": round(eq - stepper.initial_equity, 2),
            "drawdown_from_peak": round(max(0.0, 1.0 - eq / rec["peak"]), 6) if rec["peak"] else 0.0,
            "notes": notes,
            "metrics": metrics if j == len(recs) - 1 else None,
            "state_extra": extra,
        })
        prev_eq = eq
    return rows


# ── decision logging ───────────────────────────────────────────────────────────
def _log_lines(stepper: PaperPortfolio, recs: list[dict]) -> list[str]:
    lines = []
    for rec in recs:
        lines.append(
            f"{rec['date']} | eq {rec['equity']:.2f} cash {rec['cash']:.2f} "
            f"open {rec['n_open']} gross {rec['gross_exposure_x']:.2f}x "
            f"dd {max(0.0, 1.0 - rec['equity'] / rec['peak']) * 100:.1f}% "
            f"cost_day {rec['day_cost']:.2f}"
            + (" | HALT TRIGGERED" if rec.get("halt_triggered") else "")
            + (" | halted" if rec.get("halted") and not rec.get("halt_triggered") else "")
        )
        for e in rec["entries"]:
            lines.append(f"  ENTRY {e['instrument']} {e['direction']} "
                         f"{e['units']} @ {e['entry_price']}")
        for x in rec["exits"]:
            lines.append(f"  EXIT  {x['instrument']} {x['reason']} @ {x['exit_price']:.6f} "
                         f"trade_pnl {x['trade_pnl']:+.2f}")
        for d in rec["decisions"]:
            verdict = "PERMIT" if d["permitted"] else "VETO  "
            lines.append(f"  {verdict} {d['instrument']} {d['direction']} "
                         f"notional {d['notional']:.0f} risk {d['risk_fraction'] * 100:.2f}% "
                         f"caps [{', '.join(d['constraints']) or 'none'}]")
    return lines


# ── main ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily forward paper step for the frozen multi-asset trend book.")
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
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    instruments = [i for i in (BOOK_EQUITIES + BOOK_CRYPTO + FX_MAJORS_7)
                   if i not in EXCLUDED]

    print("=" * 72, flush=True)
    print(f"PAPER PORTFOLIO STEP | book={BOOK_LABEL} (FROZEN) | cutoff {cutoff.date()} "
          f"| now {now.isoformat(timespec='seconds')}")
    print(f"universe: {len(instruments)} instruments | state: {state_path}")
    print("=" * 72, flush=True)

    # 1. top up + build the panel (only fully-closed bars: strictly before cutoff)
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
    latest = max(df.index[-1] for df in panel.values())
    print(f"panel: {len(panel)} instruments | latest closed bar {latest.date()}", flush=True)

    # 2. restore state: local JSON -> Supabase mirror -> fresh seed
    state = PaperPortfolio.load_state_file(state_path)
    origin = "local"
    if state is None and not args.no_supabase:
        state = _restore_from_supabase()
        origin = "supabase" if state is not None else "fresh"
    elif state is None:
        origin = "fresh"

    model = TrendBook(panel, **BOOK_PARAMS)
    stepper = PaperPortfolio(
        panel, model.strategies(), cfg=cfg,
        timeframes={k: "1d" for k in panel}, warmup=WARMUP,
        state=state, book=BOOK_LABEL, params=BOOK_PARAMS,
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

    # 3. advance over all unprocessed closed bars (idempotent: none -> no-op)
    recs = stepper.advance(cutoff)
    if not recs:
        lp = stepper.last_processed
        print(f"no new closed bars since {lp.date() if lp is not None else '-'} "
              f"- nothing to do (idempotent no-op). State NOT rewritten.", flush=True)
        return 0

    lines = _log_lines(stepper, recs)
    for ln in lines:
        print(ln, flush=True)

    # 4. persist: local JSON + decisions log, then the Supabase mirror
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
        ok_pos = paper_store.upsert_positions(pos_rows)
        ok_del = paper_store.delete_positions_not_open([r["instrument"] for r in pos_rows])
        ok_day = paper_store.upsert_daily(daily_rows)
        print(f"supabase: positions upsert {'ok' if ok_pos else 'FAILED'}, "
              f"prune {'ok' if ok_del else 'FAILED'}, daily {'ok' if ok_day else 'FAILED'}", flush=True)
        if not (ok_pos and ok_del and ok_day):
            print("  (clean degradation: local JSON state is authoritative; "
                  "apply supabase/apex_paper_portfolio.sql if the tables are missing)", flush=True)

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
