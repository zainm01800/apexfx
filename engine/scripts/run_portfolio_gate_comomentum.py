"""Pre-registered portfolio-level gate: COMOMENTUM CROWDING gate on the certified
Book H gold trend book.

Pre-registration: engine/data_store/comomentum_prereg.md (2026-07-28, written BEFORE any
run; the 2 trials are recorded before execution, dedup-safe). Mechanism (Lou & Polk RFS
2022): when the same-direction momentum cohort moves abnormally in lockstep, the trade is
CROWDED and reversal-prone. The gate computes, per direction, the 60-row mean pairwise
daily-return correlation within the cohort of instruments whose 252d momentum sign is in
that direction, z-scores it against its trailing 252d distribution, and blocks NEW
entries in that direction while the z-score exceeds +1.5. Everything else — sizing,
exits, costs, caps, regime/HTF gates — is the unchanged certified machinery.

Exactly 2 pre-registered configs (the full selection set) on the certified Book H gold
panel (certified insertion order), certified params verbatim, certified risk anchor
max_risk_per_trade = 0.01:
  comom_control_252   entry_gate=None                        control / anchor hard-check
  comom_gate_252      entry_gate={"kind":"comomentum",...}   challenger

Adoption (prereg section 5, binding): left-tail metrics improve (worst daily return AND
worst month pnl less negative than control) AND full-window Sharpe cost <= 0.03 AND
full-window DSR > 0.95 at the full ledger count AND PBO < 0.5 across the 2-config set.
Any leg fails => REJECTED.

Iteration window only: strictly < 2025-01-01. Seed 42. Determinism: run twice,
byte-identical modulo generated_at and the ledger pre-state (the rerun dedups).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_comomentum.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_comomentum.py --out <twin>    # determinism rerun
    .venv-mac/bin/python scripts/run_portfolio_gate_comomentum.py --instruments AAPL,MSFT,ISWD.L,BTC/USD,EUR/USD --no-ledger

Exit code 0 iff the challenger is ADOPTED under the pre-registered rule, 1 otherwise
(ADOPT NOTHING is a legitimate verdict) or if the anchor hard-check fails.
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

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.entry_gates import comomentum_series  # noqa: E402
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
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "comomentum_gate_2026-07-28.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

CONTROL = "comom_control_252"
CHALLENGER = "comom_gate_252"
GATE_SPEC = {"kind": "comomentum", "lookback": 252, "corr_window": 60,
             "ref_window": 252, "z_thresh": 1.5}
BOOKS = {CONTROL: None, CHALLENGER: GATE_SPEC}

# Pre-registered adoption thresholds (prereg section 5).
DSR_REQUIRED = 0.95          # at the full updated ledger count
PBO_REQUIRED = 0.5           # across the 2-config selection set
MAX_SHARPE_COST = 0.03       # left-tail improvement may cost at most this much Sharpe

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control MUST
# reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _params(spec):
    return {**GOLD_PARAMS, "entry_gate": spec}


def _blocked_state_split(res, panel) -> dict:
    """Mechanism diagnostic (prereg section 5, not verdict-binding): split the control's
    trades by whether their (entry date, direction) would have been BLOCKED by the gate
    (abnormal comomentum > +1.5 in the trade's direction at entry)."""
    z = comomentum_series(panel, 252, 60, 252)
    idx = z.index
    kept, blocked = [], []
    n_blocked_dates = {"long": int((z["long"] > 1.5).sum()), "short": int((z["short"] > 1.5).sum())}
    for tr in res.trades:
        t = pd.Timestamp(tr.entry_time)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        pos = idx.searchsorted(t, side="right") - 1
        col = "long" if tr.direction == "long" else "short"
        zv = z[col].iloc[pos] if pos >= 0 else np.nan
        (blocked if (np.isfinite(zv) and zv > 1.5) else kept).append(float(tr.pnl))
    return {
        "n_blocked_dates": n_blocked_dates,
        "kept": {"n_trades": len(kept),
                 "expectancy_pnl": round(float(np.mean(kept)), 2) if kept else None,
                 "win_rate": round(float(np.mean([x > 0 for x in kept])), 4) if kept else None},
        "blocked": {"n_trades": len(blocked),
                    "expectancy_pnl": round(float(np.mean(blocked)), 2) if blocked else None,
                    "win_rate": round(float(np.mean([x > 0 for x in blocked])), 4) if blocked else None},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: comomentum crowding "
                                             "gate on the certified Book H gold book.")
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first), NOT
    # load order — the certified numbers are ordering-sensitive.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 2 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "comomentum_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": _params(spec)})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"COMOMENTUM CROWDING GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-28 "
          f"| mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config (the entry gate is the ONLY difference).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    res_by_book = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **_params(spec))
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        res_by_book[name] = res
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        n_veto = sum(getattr(s, "n_vetoes", 0) for s in model._strategies.values())
        results[name] = {"params": _params(spec), "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "gate_vetoes": n_veto,
                         "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()} | vetoes={n_veto}", flush=True)
        mt = results[name]["monthly_tail"]
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
              f"worst_month={mt['worst_month_pnl']:+,.0f} | avg {mt['avg_monthly_pnl']:+,.0f}/month", flush=True)

    # Certified-anchor reproduction: control must reproduce book_h_gapaware_2026-07-22.json
    # (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[CONTROL]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",) else
                                          1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # Mechanism diagnostic: control trades split by would-be-blocked state at entry.
    split = _blocked_state_split(res_by_book[CONTROL], panel)
    print(f"mechanism diagnostic (control trades by would-be-blocked state): {split}", flush=True)

    # 2. PBO across the 2-config selection set (standing overlapping-family caveat).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 folds, reported for information and the gate record).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), _params(spec),
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. The pre-registered adoption rule (prereg section 5, binding): left tail improves,
    #    Sharpe cost <= 0.03, DSR > 0.95, PBO < 0.5.
    base_m = results[CONTROL]["metrics"]
    base_mt = results[CONTROL]["monthly_tail"]
    chal_m = results[CHALLENGER]["metrics"]
    chal_mt = results[CHALLENGER]["monthly_tail"]
    leg_worst_day = bool(chal_mt["worst_daily_return"] > base_mt["worst_daily_return"])
    leg_worst_month = bool(chal_mt["worst_month_pnl"] > base_mt["worst_month_pnl"])
    sharpe_cost = base_m["sharpe"] - chal_m["sharpe"]
    leg_sharpe = bool(sharpe_cost <= MAX_SHARPE_COST)
    leg_dsr = bool(verdicts[CHALLENGER]["dsr"].get("dsr", 0.0) > DSR_REQUIRED)
    leg_pbo = bool(pbo.get("pbo") is not None and pbo["pbo"] < PBO_REQUIRED)
    adopted = bool(leg_worst_day and leg_worst_month and leg_sharpe and leg_dsr and leg_pbo)
    adoption = {
        "leg_worst_day_improves": leg_worst_day,
        "leg_worst_month_improves": leg_worst_month,
        "worst_day": {"control": base_mt["worst_daily_return"], "challenger": chal_mt["worst_daily_return"]},
        "worst_month": {"control": base_mt["worst_month_pnl"], "challenger": chal_mt["worst_month_pnl"]},
        "leg_sharpe_cost": leg_sharpe, "sharpe_cost": round(float(sharpe_cost), 5),
        "max_sharpe_cost": MAX_SHARPE_COST,
        "leg_dsr": leg_dsr, "leg_pbo": leg_pbo, "adopted": adopted,
    }

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("  ADOPTION RULE (prereg section 5, binding):")
    print(f"    worst day: {base_mt['worst_daily_return']*100:.2f}% -> {chal_mt['worst_daily_return']*100:.2f}% "
          f"(improves? {leg_worst_day})")
    print(f"    worst month: {base_mt['worst_month_pnl']:+,.0f} -> {chal_mt['worst_month_pnl']:+,.0f} "
          f"(improves? {leg_worst_month})")
    print(f"    Sharpe cost: {sharpe_cost:.5f} <= {MAX_SHARPE_COST}? {leg_sharpe}")
    print(f"    DSR: {verdicts[CHALLENGER]['dsr'].get('dsr', 0):.4f} > {DSR_REQUIRED}? {leg_dsr} | "
          f"PBO {pbo.get('pbo')} < {PBO_REQUIRED}? {leg_pbo}")
    print(f"  DECISION: {'ADOPT ' + CHALLENGER if adopted else 'ADOPT NOTHING — certified ungated entries stand'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/comomentum_prereg.md",
        "kind": "comomentum_gate",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "mechanism_diagnostic_blocked_split": split,
        "adoption": adoption,
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
