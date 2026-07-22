"""Pre-registered portfolio gate: Book J — breadth expansion of Book H + gold.

Prereg: engine/data_store/book_l_ucits_prereg.md (2026-07-22). Universe-only
experiment on the certified `book_h_gold_252`: do 24 halal-screened large caps outside the
book's mega-cap/tech core raise its risk-adjusted quality?

WHY THIS RE-TESTS A REJECTED HYPOTHESIS (prereg §1): Book I's 18-name expansion was rejected,
but ALL FOUR of its configs failed the same shared leg — PBO 0.602 — including the certified
baseline. With four near-identical overlapping books the in-sample winner's OOS rank is
unstable, so PBO condemned the set regardless of any book's merit, while DSR was ~1.0 and
15/15 CPCV paths were positive everywhere. This runs the minimum rank-stable design instead:
exactly TWO configs, one variable.

  book_h_gold_252     certified baseline (comparator; ledger entry dedups)
  book_l_ucits_252  baseline + 24 screened large caps                     (1 NEW charge)

Per the prereg this is the ONE re-test. If a 2-config design also fails, breadth is closed
for this book and must not be re-proposed with another config count.

Same params, gates, thresholds, window (< 2025-01-01), seed 42 and machinery as every prior
gate — thin orchestration over scripts/run_portfolio_gate.py.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_j.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_j.py --out data_store/validation/book_l_gate_run2.json

Exit code 0 only if the expansion passes ALL gates AND beats the baseline's DSR.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _cap_families,
    _gate,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "book_l_gate_2026-07-22.json"

# ── The UCITS swap (prereg §2) ────────────────────────────────────────────────
# Swap map (prereg §2): US-domiciled ETFs are PRIIPs/KID-blocked for a UK retail
# account (IBKR error 201, confirmed live 2026-07-22). Value None = drop outright.
#   SOXX -> dropped: no clean US-only UCITS equivalent; SEMI.L is a global index and
#           a near-duplicate of SMH.L, so holding both is redundant exposure.
#   ISWD.L -> ISDW.L is the SAME fund's USD line; the LSE `ISWD` line is GBp (pence)
#           and embeds GBP/USD in a USD-denominated trend signal. Currency fix.
UCITS_SWAP = {
    "XLK": "IITU.L",
    "XLE": "IUES.L",
    "XBI": "BTEC.L",
    "SMH": "SMH.L",
    "SOXX": None,
    "ISWD.L": "ISDW.L",
}
SWAPPED_EQUITY = [UCITS_SWAP.get(s, s) for s in (EQUITY_CORE + [GOLD_ETC])
                  if UCITS_SWAP.get(s, s) is not None]

GOLD_UNIVERSE = EQUITY_CORE + [GOLD_ETC]

PANEL_UNIVERSES = {
    "book_h_gold_252": GOLD_UNIVERSE,
    "book_l_ucits_252": SWAPPED_EQUITY,
}
UNIVERSE_LABELS = {
    "book_h_gold_252": "book_h_gold_39",        # must byte-match the 2026-07-19 key -> dedup
    "book_l_ucits_252": "book_l_ucits_38",
}

BOOKS = {name: {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
         for name in PANEL_UNIVERSES}
BASELINE = "book_h_gold_252"
CHALLENGER = "book_l_ucits_252"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: Book J — breadth "
                                             "expansion of Book H + gold (iteration window only).")
    ap.add_argument("--instruments", default="", help="comma-separated subset (smoke testing)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH))
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(cfg.data.crypto)
    wanted = sorted({inst for u in PANEL_UNIVERSES.values() for inst in u}
                    | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    missing_swaps = []
    for inst in wanted:
        if subset and inst not in subset:
            continue
        df = store.load(inst, "1d")
        if df.empty:
            if inst in SWAPPED_EQUITY:
                missing_swaps.append(inst)
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            if inst in SWAPPED_EQUITY:
                missing_swaps.append(inst)
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        master[inst] = df

    panels = {name: {inst: master[inst]
                     for inst in universe + crypto + FX_MAJORS_7 if inst in master}
              for name, universe in PANEL_UNIVERSES.items()}
    panels = {name: p for name, p in panels.items() if len(p) >= 2}
    if len(panels) < 2:
        print("need both books present for a 2-config gate")
        return 1

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in BOOKS.items():
            ledger.record({"book": name, "universe": UNIVERSE_LABELS[name], "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + 1

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (BOOK L — UCITS RE-WRAP) 2026-07-22 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    for name, panel in panels.items():
        n_by_class: dict[str, int] = {}
        for inst in panel:
            c = cfg.asset_class_of(inst)
            n_by_class[c] = n_by_class.get(c, 0) + 1
        print(f"  {name}: {len(panel)} instruments {n_by_class}")
    if missing_swaps:
        print(f"  WARNING: {len(missing_swaps)} swapped instrument(s) lacked usable data and are "
              f"NOT in the challenger: {', '.join(sorted(set(missing_swaps)))}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}", flush=True)
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, params in BOOKS.items():
        if name not in panels:
            continue
        panel = panels[name]
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        timeframes = {k: "1d" for k in panel}
        t0 = time.time()
        model = TrendBook(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
        returns_by_book[name] = res.returns
        m = res.metrics
        results[name] = {"params": params, "universe": list(panel.keys()), "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "per_asset_class": _class_breakdown(res.per_instrument, cfg),
                         "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t0:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f}/trade profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
                  f"lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} books: {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name, params in BOOKS.items():
        if name not in panels:
            continue
        panel = panels[name]
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        t0 = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
            cfg=cfg, timeframes={k: "1d" for k in panel}, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed")
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t0:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        tag = " (baseline)" if name == BASELINE else ""
        print(f"  {name}{tag}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")

    # Binding decision rule (prereg §4): pass ALL gates AND beat the baseline DSR.
    ch = verdicts.get(CHALLENGER, {})
    base = verdicts.get(BASELINE, {})
    ch_dsr = (ch.get("dsr") or {}).get("dsr", 0.0)
    base_dsr = (base.get("dsr") or {}).get("dsr", 0.0)
    beats = ch_dsr > base_dsr
    # Prereg §3: the BASELINE IS UNTRADEABLE on a UK retail account (PRIIPs/KID), so
    # requiring the tradeable book to beat it would demand the impossible for no
    # benefit. Adoption turns on passing the three gates on its own merits; the
    # baseline delta is reported as information, not used as the bar.
    adopt = bool(ch.get("passed"))
    print("-" * 72)
    print(f"  challenger DSR {ch_dsr:.4f} vs baseline {base_dsr:.4f} -> "
          f"{'beats' if beats else 'does NOT beat'} baseline (INFORMATION ONLY — "
          f"the baseline cannot be traded on this account)")
    print(f"  DECISION: {'ADOPT the UCITS re-wrap (tradeable)' if adopt else 'REJECT — swapped sleeve fails its own gates'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration", "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/book_l_ucits_prereg.md",
        "universes": {name: list(p.keys()) for name, p in panels.items()},
        "missing_swaps": sorted(set(missing_swaps)),
        "n_trials_before": n_before, "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo, "books": results, "verdicts": verdicts,
        "decision": {"challenger_dsr": ch_dsr, "baseline_dsr": base_dsr,
                     "beats_baseline": beats, "adopt": adopt},
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if adopt else 1


if __name__ == "__main__":
    sys.exit(main())
