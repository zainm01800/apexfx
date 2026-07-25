"""Pre-registered portfolio-level gate: VOL-ADAPTIVE FIRST PARTIAL on the certified trend book.

Pre-registration: engine/data_store/vol_adaptive_partial_prereg.md (2026-07-25, written
BEFORE any run; the 2 trials are recorded before execution, dedup-safe). The certified exit
ladder banks the first 50% at +1R for every instrument. The FLAT earlier triggers were
already gated and REJECTED (early_partial_gate.md: +0.75R for all cost -40.5% of monthly
profit and deepened drawdown). This experiment conditions ONLY that trigger on instrument
volatility — p1_r = 0.75R for HIGH-vol instruments, 1.0R (certified) for LOW-vol — on the
hypothesis that high-vol names revert faster (earlier banking helps) while low-vol names
trend persistently (earlier banking hurts). The 50% fraction, Partial 2 (25% at +1.5R +
lock 0.5R), 2xATR Chandelier trail, squeeze tightening, 21-bar time exit and gap-aware
fills are all unchanged. One variable.

The vol classification is MECHANICAL and identical for backtest honesty (prereg §2): per
instrument, the median of the book's existing annualised vol-63 series (the same
_vol_series the backtester computes: 63d rolling std of log returns x sqrt(class
annualisation)) over the in-window data; HIGH-vol = strictly above the universe median.
Computed once from the full in-window panel before any run, held fixed for the full-window
run and every CPCV fold. NO hand-picked per-name assignments.

This is an OWNER-TRADE experiment (prereg §1): more safety, PRICED honestly — NOT a claim
of higher profit. The binding adoption rule (prereg §5) is: adopt the challenger ONLY IF
(1) average monthly-profit cost vs baseline <= 5%, (2) max drawdown IMPROVES (lower),
(3) win rate does not fall. DSR/PBO/CPCV run with identical machinery and thresholds as
every prior gate and are recorded for information.

Exactly 2 pre-registered configs (the full selection set) on the certified Book H gold
panel (certified insertion order: EQUITY_CORE first, then SGLD.L, crypto, FX majors —
alphabetical order is a known ~0.33-Sharpe artifact), certified params verbatim, certified
risk anchor max_risk_per_trade = 0.01 (the current config.yaml has drifted to 0.0075 by
owner decision 2026-07-23; this gate prices the exit ladder against the certified anchor,
so it pins 0.01):
  book_h_gold_252               p1_r = 1.00 everywhere   baseline / anchor hard-check
  book_h_gold_252_p1_voladapt   p1_r = 0.75 HIGH / 1.00 LOW vol   challenger

Iteration window only: strictly < 2025-01-01. Seed 42. Determinism: run twice, byte-identical
modulo generated_at and the ledger pre-state (the rerun dedups 271 -> 271).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_vol_adaptive_partial.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_vol_adaptive_partial.py --out data_store/validation/vol_adaptive_partial_gate_run2.json
    .venv-mac/bin/python scripts/run_portfolio_gate_vol_adaptive_partial.py --instruments AAPL,MSFT,BTC/USD --no-ledger

Exit code 0 if the challenger is ADOPTED under the pre-registered rule, 1 otherwise
(ADOPT NOTHING is a legitimate verdict, printed as such) or if the certified-anchor
reproduction hard-check fails.
"""

from __future__ import annotations

import argparse
import copy
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester, _vol_series  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
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
    _gate,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "vol_adaptive_partial_gate_2026-07-25.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor era (see prereg header)

# The full pre-registered selection set (2 trials): ONLY the first-partial trigger differs,
# conditioned on instrument volatility (prereg §2 rule).
BASELINE = "book_h_gold_252"
CHALLENGER = "book_h_gold_252_p1_voladapt"
BOOKS = [BASELINE, CHALLENGER]
P1_R_HIGH_VOL = 0.75
P1_R_LOW_VOL = 1.00            # certified
VOL_WINDOW = 63                # the book's existing vol measure (backtester default)
VOL_RULE = "median_vol63_split_at_universe_median"

# Pre-registered owner-trade thresholds (prereg §5).
MAX_MONTHLY_COST_PCT = 0.05    # avg monthly-profit cost vs baseline must be <= 5%

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json, risk 1.00% era):
# the p1_r=1.0 baseline MUST reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _vol_classification(panel: dict[str, pd.DataFrame], cfg) -> dict:
    """The mechanical split (prereg §2): per instrument, the median of the book's existing
    annualised vol-63 series (identical to what the backtester computes: 63d rolling std of
    log close returns x sqrt(class annualisation)) over the in-window data; HIGH-vol =
    strictly above the universe median, LOW otherwise. Computed once, before any run."""
    scalars: dict[str, float] = {}
    for inst, df in panel.items():
        ann = cfg.mechanics_for(inst).annualization
        scalars[inst] = float(np.nanmedian(_vol_series(df["close"], VOL_WINDOW, ann)))
    universe_median = float(np.median(list(scalars.values())))
    classes = {inst: ("high" if s > universe_median else "low") for inst, s in scalars.items()}
    p1_map = {inst: (P1_R_HIGH_VOL if classes[inst] == "high" else P1_R_LOW_VOL)
              for inst in panel}
    return {"vol63_median": scalars, "universe_median": universe_median,
            "class": classes, "p1_r_map": p1_map,
            "rule": VOL_RULE, "vol_window": VOL_WINDOW,
            "p1_r_high_vol": P1_R_HIGH_VOL, "p1_r_low_vol": P1_R_LOW_VOL}


def _monthly_tail_stats(res, initial_equity: float = 100000.0) -> dict:
    """In-window monthly-profit and tail figures on the 100k book (prereg §6)."""
    eq = res.equity
    if eq.empty:
        return {}
    month_last = eq.groupby(eq.index.to_period("M")).last()
    prev = pd.Series([initial_equity, *month_last.iloc[:-1]], index=month_last.index)
    monthly_pnl = month_last - prev
    daily_pnl = eq.diff().dropna()
    rets = res.returns
    return {
        "n_months": int(len(monthly_pnl)),
        "avg_monthly_pnl": round(float(monthly_pnl.mean()), 2),
        "median_monthly_pnl": round(float(monthly_pnl.median()), 2),
        "pct_positive_months": round(float((monthly_pnl > 0).mean()), 4),
        "worst_month_pnl": round(float(monthly_pnl.min()), 2),
        "worst_daily_return": round(float(rets.min()), 6),
        "worst_daily_pnl": round(float(daily_pnl.min()), 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: vol-adaptive first partial on "
                                             "the certified Book H gold trend book (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first), NOT load
    # order — the certified numbers are ordering-sensitive (ordering_sensitivity_audit.md).
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # The mechanical vol classification (prereg §2): computed ONCE from the full in-window
    # panel BEFORE any run; held fixed for the full-window run and every CPCV fold.
    vol = _vol_classification(panel, base_cfg)
    p1_map = vol["p1_r_map"]
    tm_by_book = {
        BASELINE: TradeManager(p1_r=1.0),
        CHALLENGER: TradeManager(p1_r=1.0, p1_r_by_instrument=p1_map),
    }

    # Record the 2 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        ledger.record({"book": BASELINE, "universe": "book_h_gold_39", "timeframe": "1d",
                       "factory": "trend_book_mtf", "kind": "vol_adaptive_partial_gate",
                       "max_risk_per_trade": CERTIFIED_MRPT,
                       "params": {**GOLD_PARAMS, "p1_r": 1.0}})
        ledger.record({"book": CHALLENGER, "universe": "book_h_gold_39", "timeframe": "1d",
                       "factory": "trend_book_mtf", "kind": "vol_adaptive_partial_gate",
                       "max_risk_per_trade": CERTIFIED_MRPT,
                       "params": {**GOLD_PARAMS, "p1_r_high_vol": P1_R_HIGH_VOL,
                                  "p1_r_low_vol": P1_R_LOW_VOL, "vol_rule": VOL_RULE,
                                  "p1_r_map": p1_map}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    n_high = sum(1 for c in vol["class"].values() if c == "high")
    print("=" * 72, flush=True)
    print(f"VOL-ADAPTIVE-PARTIAL GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-25 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {BASELINE} (p1_r=1.0), "
          f"{CHALLENGER} (0.75 HIGH / 1.0 LOW vol)")
    print(f"vol split ({VOL_RULE}): {n_high} HIGH-vol / {len(panel) - n_high} LOW-vol "
          f"| universe vol63 median {vol['universe_median']:.4f}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config (the TradeManager is the ONLY difference).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name in BOOKS:
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **GOLD_PARAMS)
        res = PortfolioBacktester(cfg, exit_mode="managed",
                                  trade_manager=tm_by_book[name]).run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": ({**GOLD_PARAMS, "p1_r": 1.0} if name == BASELINE else
                                    {**GOLD_PARAMS, "p1_r_high_vol": P1_R_HIGH_VOL,
                                     "p1_r_low_vol": P1_R_LOW_VOL, "vol_rule": VOL_RULE}),
                         "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        mt = results[name]["monthly_tail"]
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
              f"| avg {mt['avg_monthly_pnl']:+,.0f}/month over {mt['n_months']}m", flush=True)

    # Certified-anchor reproduction: p1_r=1.0 at risk 1.00% must reproduce
    # book_h_gapaware_2026-07-22.json (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[BASELINE]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",) else
                                          1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # 2. PBO across the 2-config selection set (reported as computed; near-degenerate by
    #    construction — same caveat as every prior overlapping-family PBO, prereg §5).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 paths as every prior gate), forwarding the SAME
    #    TradeManager into every fold — measuring the baseline exit OOS while reporting
    #    the challenger would be an invalid gate.
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name in BOOKS:
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), GOLD_PARAMS,
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
            trade_manager=tm_by_book[name],
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. The pre-registered owner-trade rule (prereg §5): adopt the challenger ONLY IF
    #    monthly-profit cost <= 5% AND maxDD improves (lower) AND win rate does not fall.
    base_m = results[BASELINE]["metrics"]
    base_mt = results[BASELINE]["monthly_tail"]
    m = results[CHALLENGER]["metrics"]
    mt = results[CHALLENGER]["monthly_tail"]
    cost_pct = ((base_mt["avg_monthly_pnl"] - mt["avg_monthly_pnl"])
                / base_mt["avg_monthly_pnl"])
    dd_improves = bool(m["max_drawdown"] < base_m["max_drawdown"])
    wr_not_falls = bool(m["win_rate"] >= base_m["win_rate"])
    qualifies = bool(cost_pct <= MAX_MONTHLY_COST_PCT and dd_improves and wr_not_falls)
    evaluation = {
        "monthly_cost_pct": round(cost_pct, 4),
        "monthly_cost_abs": round(base_mt["avg_monthly_pnl"] - mt["avg_monthly_pnl"], 2),
        "maxdd_improves": dd_improves,
        "maxdd_delta": round(m["max_drawdown"] - base_m["max_drawdown"], 6),
        "winrate_not_falls": wr_not_falls,
        "winrate_delta_pts": round((m["win_rate"] - base_m["win_rate"]) * 100.0, 2),
        "qualifies": qualifies,
    }
    adopted = CHALLENGER if qualifies else None

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("  OWNER-TRADE RULE (prereg §5, binding):")
    print(f"    {CHALLENGER}: monthly cost {evaluation['monthly_cost_abs']:+,.0f}/mo "
          f"({evaluation['monthly_cost_pct']*100:.1f}% <= 5%? {cost_pct <= MAX_MONTHLY_COST_PCT}) | "
          f"maxDD improves? {dd_improves} ({m['max_drawdown']*100:.2f}% vs {base_m['max_drawdown']*100:.2f}%) | "
          f"win rate not falls? {wr_not_falls} ({evaluation['winrate_delta_pts']:+.2f}pts) "
          f"=> {'QUALIFIES' if qualifies else 'no'}")
    print(f"  DECISION: {'ADOPT ' + adopted if adopted else 'ADOPT NOTHING — certified +1R ladder stands'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/vol_adaptive_partial_prereg.md",
        "kind": "vol_adaptive_partial_gate",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "vol_classification": vol,
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "owner_trade_rule": {"max_monthly_cost_pct": MAX_MONTHLY_COST_PCT,
                             "evaluation": evaluation,
                             "adopted": adopted},
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if adopted else 1


if __name__ == "__main__":
    sys.exit(main())
