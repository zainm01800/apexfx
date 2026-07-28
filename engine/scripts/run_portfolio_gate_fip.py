"""Pre-registered portfolio-level gate: FIP / INFORMATION-DISCRETENESS entry gate.

Pre-registration: engine/data_store/fip_prereg.md (2026-07-28, written BEFORE any run;
the 2 trials are recorded before execution, dedup-safe). Mechanism (Da, Gurun & Warachka
RFS 2014): trends built from many small same-sign days (continuous information, LOW ID)
persist; trends built from few big jumps (HIGH ID) reverse. The challenger keeps only
momentum-mode entries whose 126d-formation ID sits in the continuous half (<= the
cross-sectional median over the certified panel at the decision bar); everything else —
sizing, exits, costs, caps, regime/HTF gates — is the unchanged certified machinery.

Exactly 2 pre-registered configs (the full selection set) on the certified Book H gold
panel (certified insertion order: EQUITY_CORE first, then SGLD.L, crypto, FX majors),
certified params verbatim, certified risk anchor max_risk_per_trade = 0.01:
  fip_control_252   entry_gate=None  control / certified-anchor hard-check
  fip_gate_252      entry_gate={"kind":"fip","formation":126}  challenger

Adoption (prereg section 5, binding): challenger beats control's per-path mean net pnl
per trade on >= 12/15 CPCV paths AND full-window DSR > 0.95 at the full ledger count
AND PBO < 0.5 across the 2-config set AND full-window Sharpe drop <= 0.05. Any leg
fails => REJECTED.

Iteration window only: strictly < 2025-01-01. Seed 42. Determinism: run twice,
byte-identical modulo generated_at and the ledger pre-state (the rerun dedups).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_fip.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_fip.py --out data_store/validation/fip_gate_2026-07-28_twin.json
    .venv-mac/bin/python scripts/run_portfolio_gate_fip.py --instruments AAPL,MSFT,ISWD.L,BTC/USD,EUR/USD --no-ledger

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
from apex_quant.strategies.entry_gates import fip_id_series  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv_trades  # noqa: E402
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

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "fip_gate_2026-07-28.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

CONTROL = "fip_control_252"
CHALLENGER = "fip_gate_252"
BOOKS = {
    CONTROL: None,
    CHALLENGER: {"kind": "fip", "formation": 126},
}

# Pre-registered adoption thresholds (prereg section 5).
CPCV_PATHS_REQUIRED = 12     # of 15: challenger per-path expectancy strictly greater
DSR_REQUIRED = 0.95          # at the full updated ledger count
PBO_REQUIRED = 0.5           # across the 2-config selection set
MAX_SHARPE_DROP = 0.05       # trade-count reduction must not degrade book Sharpe beyond noise

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


def _cohort_split(res, panel) -> dict:
    """Mechanism diagnostic (prereg section 5, not verdict-binding): split the control's
    trades by their ID half at entry. Membership uses the instrument's ID and the
    cross-sectional median at the last union-timeline date <= the entry fill (the
    decision bar's value — the fill is the next bar's open)."""
    frame = pd.DataFrame({inst: fip_id_series(df, 126) for inst, df in panel.items()}).sort_index().ffill()
    median = frame.median(axis=1, skipna=True)
    idx = frame.index
    cohorts = {"continuous": [], "discrete": [], "undefined": []}
    for tr in res.trades:
        t = pd.Timestamp(tr.entry_time)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        pos = idx.searchsorted(t, side="right") - 1
        if pos < 0 or tr.instrument not in frame.columns:
            cohorts["undefined"].append(float(tr.pnl))
            continue
        idv = frame[tr.instrument].iloc[pos]
        med = median.iloc[pos]
        if not np.isfinite(idv) or not np.isfinite(med):
            cohorts["undefined"].append(float(tr.pnl))
        elif idv <= med:
            cohorts["continuous"].append(float(tr.pnl))
        else:
            cohorts["discrete"].append(float(tr.pnl))
    return {
        k: {"n_trades": len(v),
            "expectancy_pnl": round(float(np.mean(v)), 2) if v else None,
            "win_rate": round(float(np.mean([x > 0 for x in v])), 4) if v else None,
            "net_pnl": round(float(np.sum(v)), 2)}
        for k, v in cohorts.items()
    }


def _path_expectancies(cpcv: dict) -> list[dict]:
    """Per-path trade aggregates (raw trade lists are NOT persisted)."""
    out = []
    for p in cpcv["paths"]:
        pnls = [t["pnl"] for t in p["trades"]]
        out.append({"sharpe": p["sharpe"], "test_start": p["test_start"], "test_end": p["test_end"],
                    "n_trades": len(pnls),
                    "expectancy_pnl": float(np.mean(pnls)) if pnls else None})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: FIP / information-"
                                             "discreteness entry gate on the certified Book H gold book.")
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
                           "factory": "trend_book_mtf", "kind": "fip_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": _params(spec)})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"FIP / INFORMATION-DISCRETENESS GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-28 "
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
              f"| avg {mt['avg_monthly_pnl']:+,.0f}/month over {mt['n_months']}m", flush=True)

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

    # Mechanism diagnostic: control trades split by ID half at entry.
    cohorts = _cohort_split(res_by_book[CONTROL], panel)
    print(f"mechanism diagnostic (control trades by ID half at entry): {cohorts}", flush=True)

    # 2. PBO across the 2-config selection set (standing overlapping-family caveat, prereg §4).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 folds) WITH per-path trade lists for the expectancy leg.
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    path_stats: dict[str, list[dict]] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv_trades(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), _params(spec),
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        path_stats[name] = _path_expectancies(cpcv)
        # _gate embeds the cpcv dict it receives — pass the SLIM version (no raw
        # per-path trade lists) so the persisted verdict stays a compact record.
        cpcv_slim = {k: v for k, v in cpcv.items() if k != "paths"}
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv_slim, used_trials)
        results[name]["cpcv"] = cpcv_slim
        results[name]["cpcv_paths"] = path_stats[name]
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. The pre-registered adoption rule (prereg section 5, binding).
    base_m = results[CONTROL]["metrics"]
    chal_m = results[CHALLENGER]["metrics"]
    paired = []
    n_improved = 0
    for pb, pc in zip(path_stats[CONTROL], path_stats[CHALLENGER]):
        improved = (pb["expectancy_pnl"] is not None and pc["expectancy_pnl"] is not None
                    and pc["expectancy_pnl"] > pb["expectancy_pnl"])
        n_improved += int(improved)
        paired.append({"test_start": pb["test_start"], "test_end": pb["test_end"],
                       "control_exp": pb["expectancy_pnl"], "challenger_exp": pc["expectancy_pnl"],
                       "control_n": pb["n_trades"], "challenger_n": pc["n_trades"],
                       "improved": improved})
    leg_expectancy = n_improved >= CPCV_PATHS_REQUIRED
    leg_dsr = bool(verdicts[CHALLENGER]["dsr"].get("dsr", 0.0) > DSR_REQUIRED)
    leg_pbo = bool(pbo.get("pbo") is not None and pbo["pbo"] < PBO_REQUIRED)
    sharpe_drop = base_m["sharpe"] - chal_m["sharpe"]
    leg_sharpe = bool(sharpe_drop <= MAX_SHARPE_DROP)
    adopted = bool(leg_expectancy and leg_dsr and leg_pbo and leg_sharpe)
    adoption = {"n_paths_expectancy_improved": n_improved, "paths_required": CPCV_PATHS_REQUIRED,
                "leg_expectancy": leg_expectancy, "leg_dsr": leg_dsr, "leg_pbo": leg_pbo,
                "leg_sharpe_noise": leg_sharpe, "sharpe_drop": round(float(sharpe_drop), 5),
                "paired_paths": paired, "adopted": adopted}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("  ADOPTION RULE (prereg section 5, binding):")
    print(f"    expectancy leg: {n_improved}/15 paths improved (>= {CPCV_PATHS_REQUIRED}? {leg_expectancy})")
    print(f"    DSR leg: {verdicts[CHALLENGER]['dsr'].get('dsr', 0):.4f} > {DSR_REQUIRED}? {leg_dsr}")
    print(f"    PBO leg: {pbo.get('pbo')} < {PBO_REQUIRED}? {leg_pbo}")
    print(f"    Sharpe-noise leg: drop {sharpe_drop:.5f} <= {MAX_SHARPE_DROP}? {leg_sharpe}")
    print(f"  DECISION: {'ADOPT ' + CHALLENGER if adopted else 'ADOPT NOTHING — certified ungated entries stand'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/fip_prereg.md",
        "kind": "fip_gate",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "mechanism_diagnostic_cohorts": cohorts,
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
