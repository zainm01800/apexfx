"""Daily forward paper-trading stepper for the CHAMPION book (Book C).

Book C: Pure Multi-Horizon Trend Ensemble [63, 126, 252] on the 39-instrument
multi-asset panel (Equities, ETFs, Gold, FX Majors, Crypto), 0.85% maximum risk per trade,
managed exits with trailing stops.

State persists locally to engine/data_store/paper_portfolio_c/state.json and
mirrors to apex_paper_c_daily / apex_paper_c_positions Supabase tables.
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
from apex_quant.models.paper_readiness import require_daily_panel, require_restored_state

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

BOOK_LABEL = "book_c_champion_ensemble_63_126_252"
PREVIOUS_MRPT = 0.01
CERTIFIED_MRPT = 0.0085
RISK_PROMOTION = "book_c_risk_frontier_2026-08-20"

BOOK_PARAMS = {
    "carry_filter": False,
    **COMMON_PARAMS,
    "momentum_lookbacks": [63, 126, 252],
}

# Strategy construction consumes BOOK_PARAMS.  State metadata also records the
# promoted sizing so an old 1.0% pending-order book cannot silently resume.
STATE_PARAMS = {
    **BOOK_PARAMS,
    "max_risk_per_trade": CERTIFIED_MRPT,
    "risk_promotion": RISK_PROMOTION,
}

BOOK_EQUITIES = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR",
    "TSM", "NFLX", "UBER",
    "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX",
    "SGLD.L",
]
BOOK_CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
    "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD",
]
EXCLUDED = {"MATIC/USD"}

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_c" / "state.json"
LOG_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_c" / "decisions.log"


def _cfg():
    """Live config with the certified sizing pinned."""
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _migrate_promoted_risk_state(state: dict | None) -> tuple[dict | None, str | None]:
    """Invalidate legacy pending entries when promoting Book C from 1.0% to 0.85%.

    Open positions and genuine history are retained, because changing their
    original sizing after entry would falsify the paper record. Pending entries
    have not traded yet and must be regenerated under the promoted risk budget.
    A seed-only legacy state can be discarded completely and rebuilt cleanly.
    """
    if state is None:
        return None, None

    params = state.get("params") or {}
    stored_mrpt = float(params.get("max_risk_per_trade", PREVIOUS_MRPT))
    if abs(stored_mrpt - CERTIFIED_MRPT) < 1e-12:
        return state, None

    pending_count = len(state.get("pending") or {})
    has_history = bool(state.get("trades") or state.get("open_positions"))
    if not has_history:
        return None, (
            f"risk promotion {stored_mrpt:.2%} -> {CERTIFIED_MRPT:.2%}: "
            f"discarded seed-only state and {pending_count} stale pending entries"
        )

    migrated = copy.deepcopy(state)
    migrated["pending"] = {}
    migrated["params"] = copy.deepcopy(STATE_PARAMS)
    migrated["book"] = BOOK_LABEL
    return migrated, (
        f"risk promotion {stored_mrpt:.2%} -> {CERTIFIED_MRPT:.2%}: "
        f"preserved history/open positions and discarded {pending_count} stale pending entries"
    )


def _restore_from_supabase() -> dict | None:
    latest = paper_store.fetch_latest_daily(table=paper_store.DAILY_TABLE_C)
    if not latest:
        return None
    extra = latest.get("state_extra") or {}
    if extra.get("full_state"):
        return extra["full_state"]
    curve = paper_store.fetch_daily_curve(table=paper_store.DAILY_TABLE_C) or []
    pos_rows = paper_store.fetch_open_positions(table=paper_store.POSITIONS_TABLE_C) or []
    open_instruments = {r["instrument"] for r in pos_rows}
    return {
        "schema_version": 1,
        "accounting_version": extra.get("accounting_version"),
        "account_currency": extra.get("account_currency"),
        "book": extra.get("book", BOOK_LABEL),
        "params": extra.get("params", STATE_PARAMS),
        "initial_equity": float(extra.get("initial_equity", START_EQUITY)),
        "realized": float(latest.get("cash", latest.get("equity", START_EQUITY))),
        "peak": float(extra.get("peak", latest.get("equity", START_EQUITY))),
        "halted": bool(extra.get("halted", False)),
        "cost_total": float(extra.get("cost_total", 0.0)),
        "open_positions": {r["instrument"]: _posrow_to_posd(r) for r in pos_rows},
        # A partially completed mirror write can briefly contain an instrument
        # in both tables.  An open row is authoritative: never queue it again.
        "pending": {
            inst: payload for inst, payload in extra.get("pending", {}).items()
            if inst not in open_instruments
        },
        "trades": extra.get("trades", []),
        "per_inst": extra.get("per_inst", {}),
        "constraint_log": extra.get("constraint_log", {}),
        "equity_curve": [[r["date"], float(r["equity"])] for r in curve],
        "last_processed_date": extra.get("last_processed_date", latest.get("date")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily forward paper step for the CHAMPION "
                                             "book (Book C, Multi-Horizon [63, 126, 252]).")
    ap.add_argument("--as-of", default="",
                    help="process bars strictly before this date 00:00 UTC (default: today)")
    ap.add_argument("--state", default=str(STATE_PATH), help="local JSON state path")
    ap.add_argument("--no-supabase", action="store_true", help="skip all Supabase reads/writes")
    ap.add_argument("--prefer-supabase", action="store_true",
                    help="prefer mirrored state over the tracked local snapshot (CI mode)")
    ap.add_argument("--clear-halt", action="store_true",
                    help="clear the experiment HALT flag after a review, then exit")
    ap.add_argument("--dry-run", action="store_true", help="Simulate without ledger or mirror writes")
    ap.add_argument("--initialize-repaired", action="store_true", help="Initialize a separate local repaired paper ledger")
    args = ap.parse_args(argv)

    now = pd.Timestamp.now(tz="UTC")
    cutoff = _utc(args.as_of).normalize() if args.as_of else now.normalize()
    state_path = Path(args.state)
    cfg = _cfg()
    store = ParquetStore(cfg.store_path)
    instruments = [i for i in (BOOK_EQUITIES + BOOK_CRYPTO + FX_MAJORS_7)
                   if i not in EXCLUDED]

    print("=" * 72, flush=True)
    print(f"PAPER PORTFOLIO STEP | book={BOOK_LABEL} (CHAMPION ENSEMBLE [63, 126, 252]) "
          f"| risk {CERTIFIED_MRPT:.2%} | cutoff {cutoff.date()} "
          f"| now {now.isoformat(timespec='seconds')}")
    print(f"universe: {len(instruments)} instruments | state: {state_path}")
    print("=" * 72, flush=True)

    adapter = get_adapter("yahoo")
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

    require_daily_panel(panel, instruments, cutoff)
    latest = max(df.index[-1] for df in panel.values())
    print(f"panel: {len(panel)} instruments | latest closed bar {latest.date()}", flush=True)

    # Restore state. CI explicitly prefers Supabase because a checkout can
    # contain a tracked snapshot that is older than the nightly mirror.
    local_state = PaperPortfolio.load_state_file(state_path)
    state = local_state
    origin = "local" if state is not None else "fresh"
    if args.prefer_supabase and not args.no_supabase:
        remote_state = _restore_from_supabase()
        if remote_state is not None:
            state, origin = remote_state, "supabase"
        else:
            raise ValueError("Authoritative Supabase state unavailable; local fallback prohibited")
    elif state is None and not args.no_supabase:
        state = _restore_from_supabase()
        origin = "supabase" if state is not None else "fresh"

    state, migration_note = _migrate_promoted_risk_state(state)
    if migration_note:
        print(f"state migration: {migration_note}", flush=True)
        if state is None:
            origin = "fresh-after-risk-promotion"

    require_restored_state(state, initialize=args.initialize_repaired, no_remote=args.no_supabase,
                           state_path=state_path, original_path=STATE_PATH)
    model = TrendBook(panel, **BOOK_PARAMS)
    strategies = model.strategies()

    stepper = PaperPortfolio(
        panel, strategies, cfg=cfg,
        timeframes={k: "1d" for k in panel}, warmup=WARMUP,
        state=state, book=BOOK_LABEL, params=STATE_PARAMS,
        halt_drawdown=HALT_DRAWDOWN, initial_equity=START_EQUITY,
        account_currency="GBP", fx_panel=panel,
    )

    if args.clear_halt and args.dry_run:
        raise ValueError("--clear-halt cannot be combined with --dry-run")
    if args.clear_halt:
        stepper.set_halted(False)
        stepper.save_state(state_path)
        print(f"halt flag cleared ({origin} state); review noted. Exiting.", flush=True)
        return 0

    if state is None:
        if args.as_of:
            raise ValueError("Repaired initialization cannot backdate activation")
        stepper._last_processed = now.normalize()
        stepper._eq_points = [(now.normalize(), START_EQUITY)]
        if not args.dry_run:
            stepper.save_state(state_path)
        print("Separate repaired ledger initialized; no historical trades imported.", flush=True)
        return 0
    else:
        print(f"state restored from {origin} | last processed {stepper.last_processed} "
              f"| equity points {len(stepper.equity_series())}", flush=True)

    recs = stepper.advance(cutoff)
    if not recs:
        lp = stepper.last_processed
        print(f"no new closed bars since {lp.date() if lp is not None else '-'} "
              f"- nothing to do (idempotent no-op). State NOT rewritten.", flush=True)
        return 0

    if args.dry_run:
        print(f"DRY RUN: {len(recs)} new bars verified; no ledger or remote writes", flush=True)
        return 0

    lines = _log_lines(stepper, recs)
    for ln in lines:
        print(ln, flush=True)

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
        ok_pos = paper_store.upsert_positions(pos_rows, table=paper_store.POSITIONS_TABLE_C)
        ok_del = paper_store.delete_positions_not_open([r["instrument"] for r in pos_rows],
                                                       table=paper_store.POSITIONS_TABLE_C)
        ok_day = paper_store.upsert_daily(daily_rows, table=paper_store.DAILY_TABLE_C)
        print(f"supabase: positions upsert {'ok' if ok_pos else 'FAILED'}, "
              f"prune {'ok' if ok_del else 'FAILED'}, daily {'ok' if ok_day else 'FAILED'}", flush=True)
        if not (ok_pos and ok_del and ok_day):
            print("Durable mirror failed; forward run failed", flush=True)
            return 1

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
