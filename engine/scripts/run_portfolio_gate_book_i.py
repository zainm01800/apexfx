"""Pre-registered portfolio gate: Book I — universe expansion + pruning of Book H + gold.

Prereg: engine/data_store/book_i_prereg.md (2026-07-20). Universe-only experiment on the
certified `book_h_gold_252`: does pruning the two documented in-window losers (XLE −£18,612/35tr,
ISWD.L −£8,110/51tr per data_store/validation/book_h_gate_2026-07-19.json — ISWD.L's GBp pence
line embeds GBP/USD) and/or adding 18 halal-screened diversifiers (healthcare / consumer /
industrials / semis; screens + exclusions documented in the prereg) survive the full gate?

Exactly 4 configs — the full pre-registered selection set (3 NEW ledger charges; the baseline
comparator dedups against its 2026-07-19 canonical key):

  book_h_gold_252        certified baseline (comparator)          21 equity+ETC
  book_i_prune_252       gold − {XLE, ISWD.L}                     19
  book_i_exp_252         gold + 18 additions                      39
  book_i_exp_prune_252   gold + 18 − {XLE, ISWD.L}                37

Every panel carries the unchanged 11-crypto + 7-FX sleeves (MATIC/USD drops via MIN_BARS, as in
Books D/H). Same params, gates, thresholds, window (< 2025-01-01), seed 42, and machinery as the
Book H gate — thin orchestration over scripts/run_portfolio_gate.py.

Honesty rules (identical to every prior gate):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
  * 3 new trials recorded in the shared TrialLedger BEFORE the runs; every DSR deflates by the
    ledger's FULL updated count (expected 208). Re-runs dedup against canonical keys.
  * Decision rule (binding, prereg §4): adopt the highest-DSR config that passes ALL gates;
    tie -> fewer instruments; only-baseline-passes or nothing-passes -> ADOPT NOTHING.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_i.py                    # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_book_i.py --out data_store/validation/book_i_gate_run2.json
    .venv-mac/bin/python scripts/run_portfolio_gate_book_i.py --instruments AAPL,JNJ,BTC/USD --no-ledger

Exit code 0 if at least one NON-baseline config passes, 1 otherwise.
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

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "book_i_gate_2026-07-20.json"

# ── Universe I (pre-registered in engine/data_store/book_i_prereg.md §2-3) ─────
PRUNED = ["XLE", "ISWD.L"]
ADDITIONS_18 = [
    # healthcare
    "JNJ", "MRK", "PFE", "ABBV",
    # consumer staples / discretionary
    "PG", "KO", "PEP", "NKE", "HD",
    # industrials / materials
    "LIN", "UNP", "ITW",
    # semis / equipment / networking
    "AMAT", "TXN", "QCOM", "MU", "INTC", "CSCO",
]

GOLD_UNIVERSE = EQUITY_CORE + [GOLD_ETC]
PRUNED_UNIVERSE = [i for i in GOLD_UNIVERSE if i not in PRUNED]

PANEL_UNIVERSES = {
    "book_h_gold_252": GOLD_UNIVERSE,
    "book_i_prune_252": PRUNED_UNIVERSE,
    "book_i_exp_252": GOLD_UNIVERSE + ADDITIONS_18,
    "book_i_exp_prune_252": PRUNED_UNIVERSE + ADDITIONS_18,
}
UNIVERSE_LABELS = {
    "book_h_gold_252": "book_h_gold_39",       # must byte-match the 2026-07-19 key -> dedup
    "book_i_prune_252": "book_i_prune_37",
    "book_i_exp_252": "book_i_exp_57",
    "book_i_exp_prune_252": "book_i_exp_prune_55",
}

BOOKS = {name: {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
         for name in PANEL_UNIVERSES}
BASELINE = "book_h_gold_252"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: Book I — universe "
                                             "expansion + pruning of Book H + gold (iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset intersected with each book's universe "
                         "(smoke testing; books left with < 2 instruments are skipped)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + 3 new)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (override for the determinism re-run)")
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(cfg.data.crypto)  # MATIC/USD included; drops out via MIN_BARS skip as in Books D/H
    wanted = sorted({inst for universe in PANEL_UNIVERSES.values() for inst in universe}
                    | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        if subset and inst not in subset:
            continue
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        master[inst] = df

    panels = {name: {inst: master[inst]
                     for inst in universe + crypto + FX_MAJORS_7 if inst in master}
              for name, universe in PANEL_UNIVERSES.items()}
    panels = {name: p for name, p in panels.items() if len(p) >= 2}
    if not panels:
        print("need >= 2 instruments in at least one book for a portfolio gate")
        return 1

    # Record the pre-registered trials BEFORE running. The baseline's canonical key
    # dedups against its 2026-07-19 record; the 3 book_i configs are NEW charges.
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in BOOKS.items():
            ledger.record({"book": name, "universe": UNIVERSE_LABELS[name], "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + 3

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (BOOK I — EXPANSION + PRUNE) 2026-07-20 | mode=ITERATION "
          f"(strictly < {args.holdout_start})")
    for name, panel in panels.items():
        n_by_class = {}
        for inst in panel:
            cls = cfg.asset_class_of(inst)
            n_by_class[cls] = n_by_class.get(cls, 0) + 1
        print(f"  {name}: {len(panel)} instruments {n_by_class}")
    print(f"window: {min(df.index[0] for df in master.values()).date()} "
          f"-> {max(df.index[-1] for df in master.values()).date()} | books: {list(panels)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}", flush=True)
    print("=" * 72, flush=True)

    # 1. Full-window run per book on its own panel.
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, params in BOOKS.items():
        if name not in panels:
            continue
        panel = panels[name]
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        timeframes = {k: "1d" for k in panel}
        t_start = time.time()
        model = TrendBook(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": params, "universe": list(panel.keys()), "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "per_asset_class": _class_breakdown(res.per_instrument, cfg),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% "
                  f"maxDD={m['max_drawdown']*100:.1f}% lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

    # 2. PBO across the four books — the whole pre-registered selection set. (Caveat,
    #    pre-registered: universes overlap heavily, PBO's discriminative power is
    #    limited by construction — reported as computed, pass or fail.)
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} books: {pbo}", flush=True)

    # 3. CPCV OOS distribution per book (the same 15 paths as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name, params in BOOKS.items():
        if name not in panels:
            continue
        panel = panels[name]
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        timeframes = {k: "1d" for k in panel}
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        tag = " (baseline)" if name == BASELINE else ""
        print(f"  {name}{tag}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/book_i_prereg.md",
        "universes": {name: list(p.keys()) for name, p in panels.items()},
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)

    non_baseline_pass = any(v["passed"] for n, v in verdicts.items() if n != BASELINE)
    return 0 if non_baseline_pass else 1


if __name__ == "__main__":
    sys.exit(main())
