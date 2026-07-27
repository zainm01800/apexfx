"""Universe-expansion candidate screen — mechanical, ex-ante, NO performance data.

The selection rule is fixed before the gate (same rule as book_k_prereg.md §2, same
threshold), computed on daily returns over the iteration window only (strictly
< 2025-01-01):

  R0 data:        >= 300 in-window daily bars (all candidates already pass; recorded).
  R1 halal bar:   AAOIFI-style activity screen (documented per instrument; CNDX.L fails
                  — Nasdaq-100 applies no activity/debt screens, same ruling as Book H
                  dropping QQQ).
  R2 independence: reject any candidate with max |corr| >= 0.50 vs ANY certified-book
                  instrument (the 39 of book_h_gold_252). Near-duplicates are not breadth.
  R3 internal dedup: among R2 survivors, in the listing order below, drop a candidate
                  with |corr| >= 0.90 vs an already-kept candidate (kills same-exposure
                  pairs like SXLV.L/IUHC.L).

All survivors (max 24) enter the expanded universe. No price-performance figure enters
any rule — correlation is a structural property. Output: console table + JSON.

Usage: cd engine && .venv-mac/bin/python scratch/screen_univexp_candidates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean  # noqa: E402
from run_portfolio_gate import DEFAULT_HOLDOUT_START, MIN_BARS, _utc  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

CORR_REJECT = 0.50     # Book K rule, verbatim
INTERNAL_DUP = 0.90    # candidate-vs-candidate same-exposure cutoff
MAX_ADDITIONS = 24

# Listing order = R3 tie-break order (SPDR healthcare preferred over the iShares wrap
# of the same index for single-issuer consistency with the other four SPDR lines).
CANDIDATES = [
    "SXLV.L", "SXLI.L", "SXLB.L", "SXLP.L", "SXLY.L",
    "IUHC.L", "RBOT.L", "DGTL.L",
    "SPSK",
    "AMGN", "VRTX", "DHR", "SYK", "EMR", "ETN", "PH", "SHW", "ECL",
    "AMAT", "LRCX", "KLAC",
    "IUIT.L", "SXLE.L", "BTEC.L", "CNDX.L",
]
HALAL_FAILS = {"CNDX.L": "Nasdaq-100 applies no activity/debt screens (mapping doc "
                         "2026-07-24; same ruling as Book H dropping QQQ)"}


def main() -> None:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(DEFAULT_HOLDOUT_START)

    crypto = list(cfg.data.crypto)
    book = [inst for inst in EQUITY_CORE + [GOLD_ETC] + crypto + FX_MAJORS_7]

    def load(inst):
        df = clean(store.load(inst, "1d"))
        df = df[df.index < holdout_start]
        return df["close"] if len(df) >= MIN_BARS else None

    closes = {}
    skipped = []
    for inst in book + [c for c in CANDIDATES if c not in book]:
        s = load(inst)
        if s is None:
            skipped.append(inst)
            continue
        closes[inst] = s
    if skipped:
        print(f"skipped (<{MIN_BARS} in-window bars): {skipped}")

    px = pd.DataFrame(closes)
    rets = px.pct_change().iloc[1:]
    book_cols = [c for c in book if c in rets.columns]

    rows = []
    kept_so_far: list[str] = []
    for cand in CANDIDATES:
        if cand not in rets.columns:
            rows.append({"candidate": cand, "verdict": "EXCLUDE", "reason": "no data"})
            continue
        c = rets[book_cols].corrwith(rets[cand])
        max_abs = float(c.abs().max())
        argmax = str(c.abs().idxmax())
        mean_abs = float(c.abs().mean())
        row = {"candidate": cand, "max_abs_corr": round(max_abs, 3),
               "argmax": argmax, "mean_abs_corr": round(mean_abs, 3),
               "n_obs": int(rets[cand].dropna().shape[0])}
        if cand in HALAL_FAILS:
            row.update(verdict="EXCLUDE", reason=f"R1 halal bar: {HALAL_FAILS[cand]}")
        elif max_abs >= CORR_REJECT:
            row.update(verdict="EXCLUDE",
                       reason=f"R2 independence: |corr| {max_abs:.3f} vs {argmax} >= {CORR_REJECT}")
        else:
            dup_of = next((k for k in kept_so_far
                           if abs(float(rets[cand].corr(rets[k]))) >= INTERNAL_DUP), None)
            if dup_of:
                row.update(verdict="EXCLUDE",
                           reason=f"R3 internal dedup: |corr| >= {INTERNAL_DUP} vs {dup_of}")
            else:
                row.update(verdict="KEEP", reason="passes R0-R3")
                kept_so_far.append(cand)
        rows.append(row)

    survivors = [r["candidate"] for r in rows if r["verdict"] == "KEEP"]
    survivors.sort(key=lambda c: next(r["mean_abs_corr"] for r in rows if r["candidate"] == c))
    if len(survivors) > MAX_ADDITIONS:
        cut = survivors[MAX_ADDITIONS:]
        for r in rows:
            if r["candidate"] in cut:
                r.update(verdict="EXCLUDE", reason=f"cap {MAX_ADDITIONS}: least independent kept")
        survivors = survivors[:MAX_ADDITIONS]

    print(f"\n{'candidate':8s} {'max|r|':>7s} {'argmax':8s} {'mean|r|':>7s}  verdict/reason")
    for r in rows:
        print(f"{r['candidate']:8s} {r.get('max_abs_corr', float('nan')):7.3f} "
              f"{r.get('argmax', '-'):8s} {r.get('mean_abs_corr', float('nan')):7.3f}  "
              f"{r['verdict']}: {r['reason']}")
    print(f"\nSURVIVORS ({len(survivors)}), ranked by mean |corr| vs book: {survivors}")

    out = ENGINE_DIR / "scratch" / "screen_univexp_candidates.json"
    out.write_text(json.dumps({"rule": {"corr_reject": CORR_REJECT, "internal_dup": INTERNAL_DUP,
                                        "max_additions": MAX_ADDITIONS, "window": "2016-01-01..2024-12-31",
                                        "book": "book_h_gold_252 (39)"},
                               "rows": rows, "survivors_ranked": survivors}, indent=2))
    print(f"written: {out}")


if __name__ == "__main__":
    main()
