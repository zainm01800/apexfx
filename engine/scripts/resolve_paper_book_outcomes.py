"""Resolve closed Book D paper-book positions into apex_research_memory outcomes.

The forward paper test (``scripts/run_paper_portfolio.py``, stepped nightly by the
GitHub Action) is the experiment of record. When one of its positions CLOSES, the
stepper appends a ``Trade`` record to its state (``trades`` in
``engine/data_store/paper_portfolio/state.json``, mirrored into the latest
``apex_paper_daily.state_extra.trades``) and DELETES the position row from
``apex_paper_positions`` — so after the fact the exact entry context (stop,
target, risk) exists only if something captured it while the position was open.

This resolver is that something. It is strictly READ-ONLY toward the paper book
(state.json, apex_paper_positions, apex_paper_daily) and only WRITES to
``apex_research_memory`` plus its own small state file:

    engine/data_store/paper_portfolio/resolved_outcomes.json
      - processed:   keys of closed trades already turned into memory rows
      - entry_context: per-position snapshot (stop/target/risk_abs) captured
                       from the OPEN positions on every run, so a later close
                       still gets its exact entry barriers

For each newly closed trade it upserts ONE ``apex_research_memory`` row with:
  * exact entry / exit / stop / target, outcome, outcome_date, R multiple
  * outcome mapped from the book's exit reason:  target -> tp_hit,
    stop -> sl_hit, time/other -> invalidated (the existing "managed out before
    SL/TP" bucket — keeps the row eligible for scripts/update_lessons.py, which
    only scans tp_hit / sl_hit / expired / invalidated; the raw reason is kept
    in setup_features.exit_reason)
  * asset_type in the WEB taxonomy (Crypto / Forex / ETF / Stock) so the
    dashboard "Learning by setup" panel groups these with the web rows
  * setup_features.source = "paper_book" and setup_features.book = the book label
  * regime derived at ENTRY time with the book's own RuleBasedRegime over the
    local parquet cache (best effort; "unknown" when data is unavailable)

CRITICAL constraints honoured here:
  * the `ticket` column is NEVER set on these rows — the Bayesian sizing
    posterior (initialize_bayesian_sizer_from_supabase) must keep excluding
    them: the paper book INFORMS lessons/panels, it never vetoes live orders.
  * apex_ibkr_trades (the IBKR mirror of this same book) is NEVER read as an
    outcome source — those fills duplicate the paper book and would double-count.
  * no change to the book's trading behaviour, parameters, state.json, the
    GitHub Action, or engine/config.yaml. Resolvers only READ the book.

Usage:
    cd engine
    .venv-mac/bin/python scripts/resolve_paper_book_outcomes.py              # DRY RUN (default)
    .venv-mac/bin/python scripts/resolve_paper_book_outcomes.py --apply      # write rows
    .venv-mac/bin/python scripts/resolve_paper_book_outcomes.py --apply --with-lessons
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env")

import httpx  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore  # noqa: E402
from apex_quant.data.point_in_time import PointInTimeAccessor  # noqa: E402
from apex_quant.regime.rule_based import RuleBasedRegime, regime_config_for  # noqa: E402
from apex_quant.storage._keys import service_or_anon_key  # noqa: E402
from apex_quant.storage import paper_store  # noqa: E402

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"
RESOLVER_STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "resolved_outcomes.json"

BOOK_LABEL = "book_d_multiasset_252"
BOOK_REWARD_RISK = 1.5          # frozen book params (rr 1.5) — display string only
BOOK_STYLE = "position"         # daily trend book, 252-bar lookback / 21-bar hold

# Web taxonomy ETF set — mirrors public/dashboard.js (the taxonomy authority).
ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "GLD", "SLV", "USO", "TLT", "HYG", "LQD", "XLF", "XLE",
    "XLK", "XLV", "XLI", "XLC", "ARKK", "VTI", "VOO", "VNQ", "EEM", "EFA", "GDX",
    "GDXJ", "XBI", "IBB", "DIA", "SMH", "SOXX",
}

_OUTCOME_FROM_REASON = {"target": "tp_hit", "stop": "sl_hit"}


def web_asset_type(symbol: str) -> str:
    """Web taxonomy asset label: Crypto / Forex / ETF / Stock."""
    sym = (symbol or "").strip().upper()
    if sym in ETF_SYMBOLS:
        return "ETF"
    cls = get_config().asset_class_of(symbol)
    return {"forex": "Forex", "crypto": "Crypto", "equity": "Stock"}.get(cls, "Stock")


# ── source reads (READ-ONLY) ────────────────────────────────────────────────────
def _load_local_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  warn: could not read {STATE_PATH}: {e}")
        return None


def _closed_trades(local_state: dict | None) -> tuple[list[dict], str]:
    """All closed Trade records, local state first, Supabase mirror second."""
    if local_state is not None:
        return list(local_state.get("trades") or []), "local state.json"
    latest = paper_store.fetch_latest_daily()
    if latest:
        extra = latest.get("state_extra") or {}
        return list(extra.get("trades") or []), "supabase apex_paper_daily.state_extra"
    return [], "unavailable"


def _open_positions(local_state: dict | None) -> dict:
    """Currently open positions with full records (stop/target/risk_abs)."""
    if local_state is not None:
        return dict(local_state.get("open_positions") or {})
    out = {}
    for r in paper_store.fetch_open_positions() or []:
        out[r["instrument"]] = {
            "symbol": r["instrument"], "direction": r["direction"],
            "entry_price": float(r["entry_price"]), "entry_time": r["entry_time"],
            "stop": float(r["stop"]), "initial_stop": float(r["initial_stop"]),
            "target": float(r["target"]), "risk_abs": float(r["risk_abs"]),
            "initial_units": float(r["initial_units"]), "tf": r.get("tf") or "1d",
        }
    return out


# ── resolver state (the only local file this script writes) ─────────────────────
def _load_resolver_state() -> dict:
    try:
        raw = json.loads(RESOLVER_STATE_PATH.read_text(encoding="utf-8"))
        raw.setdefault("processed", [])
        raw.setdefault("entry_context", {})
        return raw
    except FileNotFoundError:
        return {"version": 1, "processed": [], "entry_context": {}}
    except Exception as e:  # noqa: BLE001
        print(f"  warn: resolver state unreadable ({e}) — starting empty, "
              f"already-written rows are still de-duplicated by their deterministic id")
        return {"version": 1, "processed": [], "entry_context": {}}


def _save_resolver_state(st: dict) -> None:
    RESOLVER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = RESOLVER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
    tmp.replace(RESOLVER_STATE_PATH)


def _trade_key(t: dict) -> str:
    return f"{t.get('instrument')}|{t.get('entry_time')}|{t.get('exit_time')}|{t.get('exit_price')}"


def _ctx_key(instrument: str, entry_time: str, entry_price: float) -> str:
    return f"{instrument}|{entry_time}|{round(float(entry_price), 6)}"


# ── regime at entry (best effort, same classifier the book trades under) ────────
_REGIMES: dict = {}


def _regime_at_entry(instrument: str, entry_time: str) -> str:
    try:
        cfg = get_config()
        store = ParquetStore(cfg.store_path)
        df = store.load(instrument, "1d")
        if df is None or df.empty:
            return "unknown"
        entry_ts = df.index[df.index <= str(entry_time)[:10]]
        if len(entry_ts) < 30:
            return "unknown"
        df = df.loc[df.index <= entry_ts[-1]]
        key = ("1d", cfg.asset_class_of(instrument))
        reg = _REGIMES.get(key)
        if reg is None:
            reg = _REGIMES[key] = RuleBasedRegime(regime_config_for(*key))
        label = reg.classify(PointInTimeAccessor(df), df.index[-1])
        return label.name
    except Exception:  # noqa: BLE001
        return "unknown"


# ── row builder ─────────────────────────────────────────────────────────────────
def _memory_row(t: dict, ctx: dict | None, regime: str) -> dict:
    inst = str(t["instrument"])
    direction = str(t.get("direction", "")).lower()
    is_long = direction in ("long", "buy")
    entry = float(t["entry_price"])
    exit_px = float(t["exit_price"])
    reason = str(t.get("exit_reason") or "")
    pnl = float(t.get("pnl") or 0.0)
    units = float(t.get("units") or 0.0)

    if ctx:
        stop = float(ctx["initial_stop"])
        target = float(ctx["target"])
        risk_abs = float(ctx["risk_abs"])
        context_src = "snapshot"
    elif reason == "stop" and exit_px > 0:
        # exit_price IS the (possibly trailed) stop at exit — derive the missing
        # target from the book's frozen R:R. Marked derived; never silently exact.
        stop = exit_px
        risk_dist = abs(entry - stop)
        target = entry + BOOK_REWARD_RISK * risk_dist if is_long else entry - BOOK_REWARD_RISK * risk_dist
        risk_abs = 0.0
        context_src = "derived"
    elif reason == "target" and exit_px > 0:
        target = exit_px
        risk_dist = abs(target - entry) / BOOK_REWARD_RISK if BOOK_REWARD_RISK else 0.0
        stop = entry - risk_dist if is_long else entry + risk_dist
        risk_abs = 0.0
        context_src = "derived"
    else:
        stop = target = 0.0
        risk_abs = 0.0
        context_src = "missing"

    r_multiple = round(pnl / risk_abs, 3) if risk_abs > 0 else None
    outcome = _OUTCOME_FROM_REASON.get(reason, "invalidated")
    exit_day = str(t.get("exit_time"))[:10]

    return {
        "id": f"paper_book_{inst.replace('/', '_')}_{exit_day}",
        "symbol": inst,
        "asset_type": web_asset_type(inst),
        "analysis_date": str(t.get("entry_time"))[:10],
        "price": entry,
        "verdict": "BUY" if is_long else "SELL",
        "confidence": 50,
        "entry_zone": f"{entry:.6g}",
        "stop_loss": round(stop, 6) if stop else None,
        "target_price": round(target, 6) if target else None,
        "risk_reward": f"1:{BOOK_REWARD_RISK:.1f}",
        "timeframe": "1d",
        "summary": (f"Book D forward paper trade ({BOOK_LABEL}) — {direction} {inst}, "
                    f"entered {t.get('entry_time')}, exited {t.get('exit_time')} ({reason})."),
        "technical_analysis": ("Forward paper test position; TrendBook signal stack "
                               "(RegimeGatedMomentum x MultiTimeframeMomentum, lookback 252)."),
        "setup_features": {
            "auto": True,
            "source": "paper_book",
            "book": BOOK_LABEL,
            "style": BOOK_STYLE,
            "asset": web_asset_type(inst),
            "regime": regime,
            "exit_reason": reason,
            "r_multiple": r_multiple,
            "pnl_gbp": round(pnl, 2),
            "units": units,
            "entry_context": context_src,
        },
        "outcome": outcome,
        "outcome_price": exit_px,
        "outcome_date": exit_day,
        # `ticket` deliberately absent: paper outcomes must never feed the
        # Bayesian sizing posterior (it only learns from ticket-linked rows).
    }


# ── main ────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve closed Book D paper positions into apex_research_memory.")
    ap.add_argument("--apply", action="store_true", help="write rows (default: dry-run, log only)")
    ap.add_argument("--with-lessons", action="store_true",
                    help="after applying, run scripts/update_lessons.py so the new rows get post-mortems")
    args = ap.parse_args(argv)

    key = service_or_anon_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    local_state = _load_local_state()
    trades, trades_src = _closed_trades(local_state)
    open_pos = _open_positions(local_state)
    rst = _load_resolver_state()
    processed = set(rst["processed"])
    ctx_map: dict = rst["entry_context"]

    print("=" * 72, flush=True)
    print(f"PAPER BOOK OUTCOME RESOLVER | book={BOOK_LABEL} | "
          f"{'APPLY' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"closed trades: {len(trades)} (from {trades_src}) | open positions: {len(open_pos)} "
          f"| already processed: {len(processed)}")
    print("=" * 72, flush=True)

    # 1. snapshot entry context for everything currently OPEN so future closes
    #    resolve with exact barriers even though the stepper deletes the rows.
    snapped = 0
    for inst, p in open_pos.items():
        try:
            ck = _ctx_key(inst, str(p.get("entry_time"))[:10], p["entry_price"])
            if ck not in ctx_map:
                ctx_map[ck] = {
                    "initial_stop": float(p.get("initial_stop") or p.get("stop")),
                    "target": float(p["target"]),
                    "risk_abs": float(p.get("risk_abs") or 0.0),
                    "tf": p.get("tf") or "1d",
                }
                snapped += 1
        except Exception as e:  # noqa: BLE001
            print(f"  warn: could not snapshot {inst}: {e}")
    if snapped:
        print(f"snapshotted entry context for {snapped} open position(s)")

    # 2. build rows for newly closed trades
    new_rows = []
    for t in trades:
        tk = _trade_key(t)
        if tk in processed:
            continue
        try:
            ck = _ctx_key(str(t["instrument"]), str(t.get("entry_time"))[:10], t["entry_price"])
            ctx = ctx_map.get(ck)
            regime = _regime_at_entry(str(t["instrument"]), str(t.get("entry_time"))[:10])
            row = _memory_row(t, ctx, regime)
            new_rows.append((tk, row))
        except Exception as e:  # noqa: BLE001
            print(f"  warn: skipping trade {tk}: {type(e).__name__}: {e}")

    if not new_rows:
        print("no newly closed trades — nothing to do.")
    for tk, row in new_rows:
        sf = row["setup_features"]
        print(f"  {'WOULD WRITE' if not args.apply else 'WRITE'} {row['id']}: "
              f"{row['verdict']} {row['symbol']} {row['price']} -> {row['outcome_price']} "
              f"| {row['outcome']} ({sf['exit_reason']}) | R {sf['r_multiple']} "
              f"| ctx {sf['entry_context']} | regime {sf['regime']} | {row['asset_type']}")

    # 3. write (only with --apply)
    if args.apply and new_rows:
        rows = [r for _, r in new_rows]
        with httpx.Client(timeout=30) as c:
            r = c.post(MEMORY_ENDPOINT, headers=headers, json=rows)
        if r.status_code in (200, 201, 204):
            print(f"upserted {len(rows)} apex_research_memory row(s) (source=paper_book, ticket untouched)")
            for tk, _ in new_rows:
                processed.add(tk)
        else:
            print(f"ERROR: memory upsert failed: HTTP {r.status_code}: {r.text[:300]}")
            return 1
        rst["processed"] = sorted(processed)
        rst["entry_context"] = ctx_map
        _save_resolver_state(rst)
        print(f"resolver state saved ({len(processed)} processed trades)")

        if args.with_lessons:
            try:
                from scripts.update_lessons import update_lessons
                print("running update_lessons() for the new rows...")
                update_lessons()
            except Exception as e:  # noqa: BLE001
                print(f"  warn: lesson generation failed: {e}")
    elif not args.apply:
        print("dry-run: no rows written, resolver state untouched. Re-run with --apply to write.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
