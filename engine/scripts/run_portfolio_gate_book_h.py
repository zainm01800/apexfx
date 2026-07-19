"""Pre-registered portfolio-level gate: BOOK H — the halal re-platformed trend book.

Pre-registration: engine/data_store/book_h_prereg.md (2026-07-19, written BEFORE any
run; the 3 ledger trials are recorded before execution, dedup-safe). Book H changes the
UNIVERSE of Book D ("book_d_multiasset_252", frozen forward-paper trend book) and nothing
else — same lookback 252 / vol 63 / hold 21 / rr 1.5 / rule_based regime / HTF 1w x 50 gate /
managed exits / vol-scaled sizing / config caps / v5 per-class costs / iteration window
strictly < 2025-01-01 / seed 42 — so every performance delta vs Book D is attributable to
the halal constraint:

  DROPPED   SPY QQQ IWM (unscreened broad index -> certified Islamic UCITS),
            XLF (conventional banks/insurers), ARKK (holds COIN/HOOD/Block financials),
            GLD (-> allocated ETC in the +gold config), TLT (-> sukuk in the +sukuk config)
  ADDED     ISWD.L (iShares MSCI World Islamic), ISDU.L (iShares MSCI USA Islamic, USD
            line), ISDE.L (iShares MSCI EM Islamic)
  KEPT      12 screened stocks (AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX
            UBER), XLK XLE XBI SMH SOXX (no financials; XLK's Visa/Mastercard borderline
            call documented in the prereg), 11 crypto, 7 FX majors (user decision; the
            tom-next riba question is quantified in the prereg for the user's scholar)

Exactly 3 pre-registered configs (the full selection set):
  book_h_core_252   core universe (38 instruments)
  book_h_gold_252   core + SGLD.L (Invesco Physical Gold ETC, allocated) (39)
  book_h_sukuk_252  core + SPSK (SP Funds Dow Jones Global Sukuk ETF) (39)

Same three gates, same thresholds, same machinery as every prior gate - thin orchestration
over scripts/run_portfolio_gate.py (TrendBook adapter, _gate, helpers), differing from
run_portfolio_gate_multiasset.py only in that each book has its OWN universe (panel), which
is the point of the re-platforming. MATIC/USD still has no cached 1d data and drops out via
the standard MIN_BARS skip, exactly as in Book D.

Honesty rules (same as run_portfolio_gate.py):
  * ITERATION window only: data strictly BEFORE --holdout-start (2025-01-01).
  * Exactly 3 trials recorded in the shared TrialLedger BEFORE the runs; the ledger's full
    updated count (193) deflates every DSR. Re-runs dedup against the same canonical keys.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_h.py                 # full Book H gate
    .venv-mac/bin/python scripts/run_portfolio_gate_book_h.py --instruments ISWD.L,BTC/USD,USD/JPY --no-ledger

Exit code 0 if all three configs pass, 1 otherwise.
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
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "book_h_gate_2026-07-19.json"

# ── Universe H (pre-registered in engine/data_store/book_h_prereg.md §3) ───────
STOCKS_12 = ["AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
             "PLTR", "TSM", "NFLX", "UBER"]
ISLAMIC_UCITS = ["ISWD.L", "ISDU.L", "ISDE.L"]
SECTOR_KEPT = ["XLK", "XLE", "XBI", "SMH", "SOXX"]
GOLD_ETC = "SGLD.L"      # Invesco Physical Gold ETC (allocated), USD line
SUKUK_ETF = "SPSK"       # SP Funds Dow Jones Global Sukuk ETF (HSBC HSKD.L has no in-window data)

EQUITY_CORE = STOCKS_12 + ISLAMIC_UCITS + SECTOR_KEPT
PANEL_UNIVERSES = {
    "book_h_core_252": EQUITY_CORE,
    "book_h_gold_252": EQUITY_CORE + [GOLD_ETC],
    "book_h_sukuk_252": EQUITY_CORE + [SUKUK_ETF],
}
UNIVERSE_LABELS = {
    "book_h_core_252": "book_h_core_38",
    "book_h_gold_252": "book_h_gold_39",
    "book_h_sukuk_252": "book_h_sukuk_39",
}

# ── Pre-registered configurations (the full selection set: 3 trials) ──────────
# Identical Book D parameters; the books differ ONLY in universe.
BOOKS = {name: {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
         for name in PANEL_UNIVERSES}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered portfolio gate: Book H — halal "
                                             "re-platformed multi-asset trend book (iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset intersected with each book's universe "
                         "(smoke testing; books left with < 2 instruments are skipped)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + 3)")
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}

    crypto = list(cfg.data.crypto)  # MATIC/USD included; drops out via MIN_BARS skip as in Book D
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

    # Record the 3 pre-registered trials BEFORE running (canonical-JSON dedup: the
    # preregistration already recorded them; re-runs keep the count at 193).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, params in BOOKS.items():
            ledger.record({"book": name, "universe": UNIVERSE_LABELS[name], "timeframe": "1d",
                           "factory": "trend_book_mtf", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"PORTFOLIO GATE (BOOK H — HALAL RE-PLATFORM) 2026-07-19 | mode=ITERATION "
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
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per book on its own panel -> returns (DSR/PBO) + trade metrics,
    #    one shared equity curve per book with config risk caps binding.
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
            for cls, agg in sorted(results[name]["per_asset_class"].items()):
                print(f"    {cls:7s}: {agg['n_trades']:5d} trades, net {agg['net_pnl']:+12.2f} "
                      f"across {len(agg['instruments'])} instruments", flush=True)

    # 2. PBO across the three books - the whole pre-registered selection set. (Caveat,
    #    pre-registered: the universes overlap ~95%, so PBO's discriminative power is
    #    limited by construction - reported as computed, pass or fail.)
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
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/book_h_prereg.md",
        "universes": {name: list(p.keys()) for name, p in panels.items()},
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if verdicts and all(v["passed"] for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
