"""Nightly risk & ops pack — the institutional "morning risk report" for the paper book.

STRICTLY READ-ONLY toward every input (state.json, mirror day-records, parquet
cache, resolver state). Writes ONLY its own artifacts under
``data_store/reports/`` (gitignored) and prints the markdown to stdout so the
nightly GitHub Action log carries the full pack.

Sections
  1. Equity & drawdown state — equity, peak, DD%, which config drawdown zone the
     book sits in (OK / REDUCING ramp / HALT), halted flag.
  2. Exposures — per open position notional at last cached close, gross/net by
     asset class, gross as x equity, top-5 concentration share of gross.
  3. Execution divergence review — per instrument mean |divergence bps| across
     mirror day-records vs the MODELED per-side cost for its class; flags
     anything above FLAG_MULTIPLE x modeled. Flag-only: nothing acts on it.
     (Known annotation: the mirror fills the previous bar's engine prices, so
     overnight gap contaminates divergence on gap days — read flags with that.)
  4. Data staleness — last cached 1d bar per book instrument vs the book's
     last_processed_date; anything older is a hole the stepper will trip on.
  5. Outcome drift stub — realized win rate of resolved paper-book closes vs the
     gate-window expectation; explicitly insufficient_data below MIN_N.

Usage:
    cd engine
    .venv-mac/bin/python scripts/nightly_risk_report.py            # print + write artifacts
    .venv-mac/bin/python scripts/nightly_risk_report.py --no-write # print only
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore  # noqa: E402

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"
RESOLVER_STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "resolved_outcomes.json"
MIRROR_DIR = ENGINE_DIR / "data_store" / "ibkr_mirror"
REPORTS_DIR = ENGINE_DIR / "data_store" / "reports"

# Modeled per-side cost by class, bps of price — mirrors the backtest mechanics
# (equity: 0.5*2.0 spread + 1.0 slippage = 2.0 bps/side; crypto 1.25 bps/side).
# Forex is per-pair pips in the engine; excluded from bps flagging here and
# annotated as such rather than approximated wrongly.
MODELED_COST_BPS = {"equity": 2.0, "crypto": 1.25}
FLAG_MULTIPLE = 3.0          # flag instruments with realized > 3x modeled
GATE_WIN_RATE = 0.558        # book_h/book_d gate-window win rate (2026-07-19 gate JSON)
DRIFT_MIN_N = 20             # below this, report insufficient_data, no verdict
DRIFT_FLAG_DELTA = 0.15      # |realized - expected| above this (with n>=MIN_N) flags


# ── pure sections (unit-tested) ────────────────────────────────────────────────
def dd_state(equity_curve: list, peak: float | None, halted: bool,
             reducing_limit: float, breaker: float) -> dict:
    """Equity, drawdown from peak, and which config zone the book is in."""
    equity = float(equity_curve[-1][1]) if equity_curve else None
    pk = float(peak) if peak else (equity or 0.0)
    dd = (pk - equity) / pk if (equity is not None and pk > 0) else None
    if halted:
        zone = "HALTED"
    elif dd is None:
        zone = "UNKNOWN"
    elif dd >= breaker:
        zone = "BREAKER"
    elif dd >= reducing_limit:
        zone = "REDUCING"
    else:
        zone = "OK"
    return {"equity": equity, "peak": pk, "drawdown_pct": None if dd is None else round(dd * 100, 3),
            "zone": zone, "reducing_limit_pct": reducing_limit * 100, "breaker_pct": breaker * 100,
            "n_curve_points": len(equity_curve)}


def exposures(open_positions: dict, last_prices: dict, equity: float | None,
              class_of) -> dict:
    """Gross/net notional by class + concentration, at last cached closes."""
    rows = []
    for inst, p in open_positions.items():
        px = last_prices.get(inst)
        if px is None:
            rows.append({"instrument": inst, "notional": None, "note": "no cached price"})
            continue
        notional = abs(float(p["units"])) * float(px)
        sign = 1.0 if str(p.get("direction", "long")).lower() != "short" else -1.0
        rows.append({"instrument": inst, "direction": "long" if sign > 0 else "short",
                     "units": round(float(p["units"]), 6), "last_close": px,
                     "notional": round(notional, 2), "signed": round(sign * notional, 2),
                     "asset_class": class_of(inst)})
    priced = [r for r in rows if r.get("notional")]
    gross = sum(r["notional"] for r in priced)
    net = sum(r["signed"] for r in priced)
    by_class: dict = {}
    for r in priced:
        c = by_class.setdefault(r["asset_class"], {"gross": 0.0, "net": 0.0, "n": 0})
        c["gross"] += r["notional"]; c["net"] += r["signed"]; c["n"] += 1
    top = sorted(priced, key=lambda r: -r["notional"])[:5]
    return {"positions": rows, "gross": round(gross, 2), "net": round(net, 2),
            "gross_x_equity": round(gross / equity, 3) if equity else None,
            "by_class": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_class.items()},
            "top5_share_of_gross": round(sum(r["notional"] for r in top) / gross, 3) if gross else None}


def divergence_review(mirror_records: list[dict]) -> dict:
    """Per-instrument realized |divergence bps| vs modeled class cost. Flag-only."""
    per_inst: dict = {}
    for rec in mirror_records:
        for o in rec.get("orders") or []:
            b = o.get("divergence_bps")
            if b is None or o.get("status") != "filled":
                continue
            d = per_inst.setdefault(o["instrument"], {"cls": o.get("asset_class"), "vals": []})
            d["vals"].append(abs(float(b)))
    out, flags = {}, []
    for inst, d in sorted(per_inst.items()):
        mean_abs = sum(d["vals"]) / len(d["vals"])
        modeled = MODELED_COST_BPS.get(d["cls"])
        flagged = modeled is not None and mean_abs > FLAG_MULTIPLE * modeled
        out[inst] = {"n_fills": len(d["vals"]), "mean_abs_bps": round(mean_abs, 2),
                     "modeled_bps_per_side": modeled, "flagged": flagged}
        if flagged:
            flags.append(inst)
    return {"per_instrument": out, "flagged": flags,
            "note": ("mirror fills lag the engine bar (gap days contaminate bps); forex modeled "
                     "in pips, excluded from bps flags")}


def data_staleness(last_bars: dict, last_processed: str | None) -> dict:
    """Instruments whose last cached 1d bar is older than the book's last step."""
    stale = {i: str(d) for i, d in sorted(last_bars.items())
             if last_processed and d is not None and str(d)[:10] < last_processed}
    missing = sorted(i for i, d in last_bars.items() if d is None)
    return {"reference_date": last_processed, "n_checked": len(last_bars),
            "stale": stale, "missing": missing}


def outcome_drift(outcomes: list[str], expected_win_rate: float = GATE_WIN_RATE,
                  min_n: int = DRIFT_MIN_N) -> dict:
    """Realized tp/sl win rate vs gate expectation. Honest below-min_n stub."""
    resolved = [o for o in outcomes if o in ("tp_hit", "sl_hit")]
    n = len(resolved)
    if n < min_n:
        return {"n_resolved": n, "min_n": min_n, "insufficient_data": True,
                "expected_win_rate": expected_win_rate}
    wr = sum(1 for o in resolved if o == "tp_hit") / n
    return {"n_resolved": n, "win_rate": round(wr, 4), "expected_win_rate": expected_win_rate,
            "insufficient_data": False, "drift_flag": abs(wr - expected_win_rate) > DRIFT_FLAG_DELTA}


# ── rendering ──────────────────────────────────────────────────────────────────
def render_md(r: dict) -> str:
    dd, ex, dv, ds, dr = r["dd"], r["exposures"], r["divergence"], r["staleness"], r["drift"]
    lines = [
        f"# Nightly risk pack — {r['book']} — {r['as_of']}",
        "",
        f"## 1. Equity & drawdown  — **{dd['zone']}**",
        f"equity {dd['equity']} | peak {round(dd['peak'], 2)} | DD {dd['drawdown_pct']}% "
        f"(ramp at {dd['reducing_limit_pct']}%, breaker at {dd['breaker_pct']}%)",
        "",
        f"## 2. Exposures — gross {ex['gross']} ({ex['gross_x_equity']}x equity), net {ex['net']}",
        f"top-5 concentration: {ex['top5_share_of_gross']} of gross | by class: {json.dumps(ex['by_class'])}",
        "",
        f"## 3. Execution divergence — {len(dv['flagged'])} flagged ({', '.join(dv['flagged']) or 'none'})",
    ]
    for inst, d in dv["per_instrument"].items():
        mark = " ⚠️" if d["flagged"] else ""
        lines.append(f"- {inst}: {d['mean_abs_bps']} bps mean|realized| over {d['n_fills']} fills "
                     f"(modeled {d['modeled_bps_per_side']}/side){mark}")
    lines += [f"  note: {dv['note']}", "",
              f"## 4. Data staleness — {len(ds['stale'])} stale, {len(ds['missing'])} missing "
              f"(ref {ds['reference_date']}, {ds['n_checked']} checked)"]
    if ds["stale"]:
        lines.append(f"stale: {json.dumps(ds['stale'])}")
    if ds["missing"]:
        lines.append(f"missing: {', '.join(ds['missing'])}")
    if dr.get("insufficient_data"):
        drift_txt = f"insufficient data ({dr['n_resolved']}/{dr['min_n']} resolved closes)"
    else:
        drift_txt = (f"win rate {dr['win_rate']} vs gate {dr['expected_win_rate']}"
                     + (" ⚠️ DRIFT" if dr.get("drift_flag") else " — within band"))
    lines += ["", f"## 5. Outcome drift — {drift_txt}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only nightly risk & ops pack for the paper book.")
    ap.add_argument("--no-write", action="store_true", help="print only; no artifact files")
    args = ap.parse_args(argv)

    cfg = get_config()
    st = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    book = st.get("book", "unknown")
    last_processed = st.get("last_processed_date")
    open_pos = st.get("open_positions") or {}

    store = ParquetStore(cfg.store_path)
    universe = sorted(set(open_pos) | set((st.get("params") or {}).get("universe") or []))
    last_bars: dict = {}
    last_prices: dict = {}
    for inst in universe:
        try:
            df = store.load(inst, "1d")
            last_bars[inst] = None if df.empty else df.index[-1].date()
            if not df.empty:
                last_prices[inst] = float(df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            last_bars[inst] = None

    mirror_records = []
    for p in sorted(glob.glob(str(MIRROR_DIR / "????-??-??.json"))):
        try:
            mirror_records.append(json.loads(Path(p).read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

    outcomes: list[str] = []
    if RESOLVER_STATE_PATH.exists():
        # processed keys only prove resolution happened; outcome labels live in the
        # memory rows. Cheap proxy kept read-only: re-derive from state.json trades.
        pass
    for t in st.get("trades") or []:
        reason = str(t.get("exit_reason") or "")
        outcomes.append({"target": "tp_hit", "stop": "sl_hit"}.get(reason, "invalidated"))

    d = dd_state(st.get("equity_curve") or [], st.get("peak"), bool(st.get("halted")),
                 cfg.risk.drawdown_reducing_limit, cfg.risk.drawdown_breaker)
    report = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "book": book, "last_processed_date": last_processed,
        "dd": d,
        "exposures": exposures(open_pos, last_prices, d["equity"], cfg.asset_class_of),
        "divergence": divergence_review(mirror_records),
        "staleness": data_staleness(last_bars, last_processed),
        "drift": outcome_drift(outcomes),
    }

    md = render_md(report)
    print(md, flush=True)
    if not args.no_write:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        day = (last_processed or datetime.now(timezone.utc).date().isoformat())
        (REPORTS_DIR / f"risk_{day}.json").write_text(json.dumps(report, indent=1, default=str),
                                                      encoding="utf-8")
        (REPORTS_DIR / f"risk_{day}.md").write_text(md, encoding="utf-8")
        print(f"artifacts: data_store/reports/risk_{day}.json / .md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
