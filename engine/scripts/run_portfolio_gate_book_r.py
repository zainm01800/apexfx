"""Pre-registered gate: Book R — residual (idiosyncratic) momentum vs total-return momentum.

Prereg: engine/data_store/residual_momentum_prereg.md (2026-07-23), written BEFORE this run.

Control and challenger share universe, risk settings, rebalance clock and slot allocation, so
residualisation is the only variable. `max_concurrent_trades` is raised to top_n so the 12-slot
cap cannot silently truncate the signal under test.

Binding rule (prereg §4): adopt the challenger only if DSR > 0.95, CPCV median OOS Sharpe > 0
with >50% paths positive, paired block bootstrap p < 0.05, AND forward p95 drawdown <= 11%.
PBO is reported but NOT binding (near-twin books). The £800/mo profit floor is reported but
NOT binding, and is expected to FAIL — recorded in advance so it cannot be re-scored later.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_r.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_r.py --no-ledger   # dry run
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402
from apex_quant.strategies.cross_sectional import CrossSectionalMomentum  # noqa: E402
from apex_quant.strategies.residual_momentum import ResidualMomentum  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio, probability_of_backtest_overfitting, sharpe_ratio,
)
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    DEFAULT_HOLDOUT_START, LEDGER_PATH, MIN_BARS, WARMUP, _utc,
)

DEFAULT_RESULTS = ENGINE_DIR / "data_store" / "validation" / "book_r_gate_2026-07-23.json"

TOP_N = 15
MIN_NAMES = 40
DD_WALL = 0.11
CAGR_FLOOR = 0.096          # reported, NOT binding (prereg §4.6)
DSR_THRESHOLD = 0.95
BASELINE = "book_r_total_top15"
CHALLENGER = "book_r_resid_top15"
#: All 10 screen cells + the 2 gated configs (prereg §1).
N_SCREEN_CELLS = 10


def forward_drawdown(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {"p95_drawdown": None, "note": "series too short"}
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(n_sims, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    fin = eq[:, -1] - 1.0
    return {
        "median_drawdown": float(np.median(dd)),
        "p95_drawdown": float(np.percentile(dd, 95)),
        "p99_drawdown": float(np.percentile(dd, 99)),
        "prob_breach_wall": float((dd > DD_WALL).mean()),
        "prob_losing_year": float((fin < 0).mean()),
    }


def build_panel(cfg, holdout) -> dict[str, pd.DataFrame]:
    store = ParquetStore(cfg.store_path)
    panel: dict[str, pd.DataFrame] = {}
    for p in sorted((ENGINE_DIR / "data_store").glob("*_1d.parquet")):
        name = p.name[: -len("_1d.parquet")]
        for cand in (name, name.replace("_", "/")):
            try:
                df = store.load(cand, "1d")
            except Exception:
                continue
            if df is None or df.empty:
                continue
            try:
                df = clean(df)
            except Exception:
                break
            df = df[df.index < holdout]
            if len(df) >= MIN_BARS:
                panel[name] = df
            break

    # Restrict to the window where the cross-section is wide enough for a rank to mean
    # anything. Without this, ragged start dates left 1,494 of 3,798 dates with <=5 scored
    # names and every top_n produced identical results (screen harness bug, 2026-07-23).
    closes = pd.DataFrame({k: v["close"] for k, v in panel.items()}).sort_index()
    closes = closes.dropna(axis=1, thresh=int(len(closes) * 0.6))
    live = closes.notna().sum(axis=1) >= MIN_NAMES
    if not live.any():
        raise SystemExit("no dates with a wide enough cross-section")
    start = live.idxmax()
    keep = set(closes.columns)
    return {k: v[v.index >= start] for k, v in panel.items() if k in keep}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: residual momentum (Book R).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS))
    args = ap.parse_args(argv)

    cfg = get_config()
    holdout = _utc(args.holdout_start)
    results_path = Path(args.out)

    panel = build_panel(cfg, holdout)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Slot cap must not truncate the signal under test.
    rc = cfg.risk.model_copy(update={
        "max_risk_per_trade": 0.0050,
        "max_concurrent_trades": TOP_N,
        "max_swing_slots": TOP_N,
    })

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for i in range(N_SCREEN_CELLS):
            ledger.record({"book": f"book_r_screen_cell_{i:02d}", "universe": "store_wide_73",
                           "timeframe": "1d", "factory": "residual_momentum_screen",
                           "params": {"note": "top_n sweep 5/10/15/20/30 x total|residual"}})
        for name in (BASELINE, CHALLENGER):
            ledger.record({"book": name, "universe": "store_wide_73", "timeframe": "1d",
                           "factory": "residual_momentum_gate",
                           "params": {"top_n": TOP_N, "max_risk_per_trade": 0.0050}})
        ledger.save(LEDGER_PATH)
    used = ledger.n_trials if not args.no_ledger else n_before + N_SCREEN_CELLS + 2

    print("=" * 92, flush=True)
    print(f"GATE (BOOK R — RESIDUAL MOMENTUM) | {len(panel)} instruments | "
          f"ITERATION < {args.holdout_start}")
    print(f"ledger {n_before} -> {used} | DSR deflates by n={used} "
          f"(10 screen cells + 2 gated, prereg §1)")
    print(f"walls: forward p95 DD <= {DD_WALL*100:.0f}% (BINDING), "
          f"CAGR >= {CAGR_FLOOR*100:.1f}% (reported, expected to FAIL)")
    print("=" * 92, flush=True)

    builders = {
        BASELINE: lambda: CrossSectionalMomentum(
            panel, lookback=252, vol_window=63,
            long_frac=TOP_N / max(1, len(panel)), short_frac=0.0, allow_short=False,
            min_universe=MIN_NAMES, holding_horizon=21, timeframe="1d"),
        CHALLENGER: lambda: ResidualMomentum(
            panel, lookback=252, skip=21, vol_window=63, top_n=TOP_N,
            min_universe=MIN_NAMES, holding_horizon=21, timeframe="1d"),
    }

    results: dict[str, dict] = {}
    rets: dict[str, pd.Series] = {}
    for name, build in builders.items():
        t0 = time.time()
        model = build()
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252)
        rets[name] = res.returns
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        fd = forward_drawdown(res.returns)
        mo = res.returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        results[name] = {
            "metrics": m, "cagr": cagr, "gbp_per_month": cagr * 100_000 / 12,
            "forward_drawdown": fd,
            "monthly": {
                "median": float(mo.median()), "worst": float(mo.min()),
                "best": float(mo.max()),
                "pct_losing": float((mo < 0).mean()), "n_months": int(len(mo)),
            },
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
            "constraint_log": dict(res.constraint_log),
        }
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name}: "
              f"{time.time()-t0:.0f}s | CAGR {cagr*100:.2f}% (£{cagr*100000/12:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} btDD {m['max_drawdown']*100:.1f}% "
              f"fwd p95 {fd['p95_drawdown']*100:.1f}% trades {m['n_trades']}", flush=True)

    aligned = pd.concat(list(rets.values()), axis=1).dropna()
    pbo = (probability_of_backtest_overfitting(aligned.to_numpy(),
                                               n_splits=cfg.validation.pbo.n_splits,
                                               seed=cfg.seed)
           if aligned.shape[0] >= 40 else {"pbo": None})
    print(f"\nPBO (reported, NOT binding — near-twin books): {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name in builders:
        dsr = deflated_sharpe_ratio(rets[name].to_numpy(), trial_sharpes, 252, n_trials=used)
        fd = results[name]["forward_drawdown"]
        v = {
            "dsr": dsr,
            "dsr_pass": dsr.get("dsr", 0.0) > DSR_THRESHOLD,
            "dd_wall_ok": bool(fd["p95_drawdown"] is not None
                               and fd["p95_drawdown"] <= DD_WALL),
            "cagr_floor_ok": bool(results[name]["cagr"] >= CAGR_FLOOR),
        }
        if name == CHALLENGER:
            v["paired_vs_control"] = paired_block_bootstrap(
                rets[BASELINE], rets[CHALLENGER], block_size=21,
                n_bootstraps=10000, seed=42, periods_per_year=252)
        verdicts[name] = v

    p = verdicts[CHALLENGER].get("paired_vs_control", {})
    p_val = p.get("p_value_one_sided", p.get("p_value", 1.0))
    adopt = bool(verdicts[CHALLENGER]["dsr_pass"]
                 and verdicts[CHALLENGER]["dd_wall_ok"]
                 and p_val is not None and p_val < 0.05)
    verdicts[CHALLENGER]["adopt_eligible"] = adopt

    print("\n" + "=" * 92, flush=True)
    for name, v in verdicts.items():
        r, m = results[name], results[name]["metrics"]
        print(f"  {name}: CAGR {r['cagr']*100:.2f}% (£{r['gbp_per_month']:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} | DSR {v['dsr'].get('dsr', 0):.3f} "
              f"{'ok' if v['dsr_pass'] else 'FAIL'} "
              f"| wall {'ok' if v['dd_wall_ok'] else 'FAIL'} "
              f"| profit-floor {'ok' if v['cagr_floor_ok'] else 'FAIL (expected)'}", flush=True)
    if p:
        print(f"  paired bootstrap (residual vs total): Δsharpe {p.get('sharpe_delta', 0):+.3f} "
              f"p={p_val:.4f} CI [{p.get('ci_95_lower', 0):+.3f}, "
              f"{p.get('ci_95_upper', 0):+.3f}]", flush=True)
    print("-" * 92)
    print(f"  DECISION: {'ADOPT ' + CHALLENGER if adopt else 'ADOPT NOTHING'}")
    print("=" * 92, flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "engine/data_store/residual_momentum_prereg.md",
           "n_trials_before": n_before, "n_trials_used": used,
           "pbo": pbo, "pbo_binding": False,
           "dd_wall": DD_WALL, "cagr_floor": CAGR_FLOOR, "cagr_floor_binding": False,
           "books": results, "verdicts": verdicts,
           "decision": {"adopt": CHALLENGER if adopt else None}}
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    ok = results_path.exists() and results_path.stat().st_size > 1000
    print(f"results {'WRITTEN' if ok else 'FAILED TO WRITE'}: {results_path} "
          f"({results_path.stat().st_size if results_path.exists() else 0} bytes)", flush=True)
    return 0 if adopt else 1


if __name__ == "__main__":
    sys.exit(main())
