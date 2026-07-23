"""Pre-registered gate: Book Q — portfolio-level volatility-target overlay.

Prereg: engine/data_store/vol_target_overlay_prereg.md (2026-07-23), written BEFORE the
frontier grid returned.

The engine's existing `target_portfolio_vol` caps each position against its OWN instrument
volatility and therefore cannot see that ten positions have become one correlated bet. The
overlay (`portfolio_vol_target`, RiskManager step 4.6) scales the whole book by
`clip(target / realised_book_vol, min, max)` from the equity curve, strictly causally.

Binding decision rule (prereg §4) — adopt the highest-Sharpe config satisfying ALL of:
  1. DSR > 0.95 at the full ledger count
  2. CPCV median OOS Sharpe > 0 and >50% of paths positive
  3. PAIRED block bootstrap vs the MATCHED no-overlay config, p < 0.05
     (PBO reported but NOT binding — it cannot discriminate near-twin books)
  4. 95th-percentile forward 1-year drawdown <= 11%   (the funded-account wall)
  5. CAGR >= 9.6%  (£800/month on £100k), compounded — never arithmetic mean / 12

If no config satisfies both 4 and 5, the honest output is "target not reachable on this book".

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_q.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_q.py --no-ledger   # dry run
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
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting, sharpe_ratio,
)
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, HORIZON, LEDGER_PATH, MIN_BARS, WARMUP,
    TrendBook, _cap_families, _gate, _max_gross_leverage, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

DEFAULT_RESULTS = ENGINE_DIR / "data_store" / "validation" / "book_q_gate_2026-07-23.json"

DD_WALL = 0.11          # funded-account 95th-percentile forward drawdown limit
CAGR_FLOOR = 0.096      # £800/month on £100k

#: Gated configs. Each overlay config is paired against the no-overlay config at the SAME
#: risk-per-trade, so the overlay is the only variable in every A/B comparison.
CONFIGS = {
    "book_q_rpt050_off":  {"max_risk_per_trade": 0.0050, "portfolio_vol_target": 0.00},
    "book_q_rpt050_vt06": {"max_risk_per_trade": 0.0050, "portfolio_vol_target": 0.06},
    "book_q_rpt075_off":  {"max_risk_per_trade": 0.0075, "portfolio_vol_target": 0.00},
    "book_q_rpt075_vt06": {"max_risk_per_trade": 0.0075, "portfolio_vol_target": 0.06},
    "book_q_rpt075_vt07": {"max_risk_per_trade": 0.0075, "portfolio_vol_target": 0.07},
}
#: Each gated config is paired against its matched no-overlay twin.
PAIRS = {
    "book_q_rpt050_vt06": "book_q_rpt050_off",
    "book_q_rpt075_vt06": "book_q_rpt075_off",
    "book_q_rpt075_vt07": "book_q_rpt075_off",
}
#: EVERY cell of the exploratory frontier is charged (prereg §3), not just the gated five.
SWEPT_GRID = [(rpt, vt)
              for rpt in (0.0050, 0.0075, 0.0100, 0.0125)
              for vt in (0.00, 0.05, 0.06, 0.07, 0.08)]


def forward_drawdown(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    """Distribution of 1-year max drawdown, bootstrapped from the realised return process.

    The backtest's max drawdown is the worst of ONE path; a funded account faces the
    distribution. The wall is judged against the tail, not the single realisation.
    """
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {"p95_drawdown": None, "note": "series too short"}
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(n_sims, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {
        "median_drawdown": float(np.median(dd)),
        "p95_drawdown": float(np.percentile(dd, 95)),
        "prob_breach_wall": float((dd > DD_WALL).mean()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: vol-target overlay (Book Q).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS))
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(args.holdout_start)
    results_path = Path(args.out)

    panel: dict[str, pd.DataFrame] = {}
    for inst in EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for rpt, vt in SWEPT_GRID:
            ledger.record({
                "book": f"book_q_rpt{int(rpt*10000):04d}_vt{int(vt*10000):04d}",
                "universe": "book_h_gold_39", "timeframe": "1d",
                "factory": "trend_book_ev_voltarget",
                "params": {**params, "max_risk_per_trade": rpt,
                           "portfolio_vol_target": vt,
                           "slot_allocation": "expected_value"},
            })
        ledger.save(LEDGER_PATH)
    used = ledger.n_trials if not args.no_ledger else n_before + len(SWEPT_GRID)

    print("=" * 86, flush=True)
    print(f"GATE (BOOK Q — VOL-TARGET OVERLAY) | {len(panel)} instruments | "
          f"ITERATION < {args.holdout_start}")
    print(f"ledger {n_before} -> {used} | DSR deflates by n={used} "
          f"(ALL {len(SWEPT_GRID)} grid cells charged)")
    print(f"walls: forward p95 DD <= {DD_WALL*100:.0f}%  AND  CAGR >= {CAGR_FLOOR*100:.1f}%")
    print("=" * 86, flush=True)

    results: dict[str, dict] = {}
    rets: dict[str, pd.Series] = {}
    for name, over in CONFIGS.items():
        rc = cfg.risk.model_copy(update=over)
        t0 = time.time()
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252)
        rets[name] = res.returns
        m = res.metrics
        eq = res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        caphits = sum(v for k, v in res.constraint_log.items() if "portfolio_risk" in k)
        scalar_hits = sum(v for k, v in res.constraint_log.items()
                          if "portfolio_vol_scalar" in k)
        results[name] = {
            **over, "metrics": m, "cagr": cagr, "gbp_per_month": cagr * 100_000 / 12,
            "portfolio_cap_hits": caphits, "vol_scalar_applications": scalar_hits,
            "max_gross_leverage": _max_gross_leverage(res),
            "per_asset_class": _class_breakdown(res.per_instrument, cfg),
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
            "forward_drawdown": forward_drawdown(res.returns),
        }
        fd = results[name]["forward_drawdown"]
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name}: "
              f"{time.time()-t0:.0f}s | CAGR {cagr*100:.2f}% (£{cagr*100000/12:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} btDD {m['max_drawdown']*100:.1f}% "
              f"fwd p95 {fd['p95_drawdown']*100:.1f}% trades {m['n_trades']}", flush=True)

    aligned = pd.concat(list(rets.values()), axis=1).dropna()
    pbo = (probability_of_backtest_overfitting(aligned.to_numpy(),
                                               n_splits=cfg.validation.pbo.n_splits,
                                               seed=cfg.seed)
           if aligned.shape[0] >= 40 else {"pbo": None})
    print(f"\nPBO (reported, NOT binding — cannot discriminate near-twins): {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name in CONFIGS:
        t0 = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p_, **kw: TrendBook(p_, **kw), params, cfg=cfg,
            timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed")
        v = _gate(name, rets[name], trial_sharpes, pbo, cpcv, used)
        base = PAIRS.get(name)
        paired = (paired_block_bootstrap(rets[base], rets[name], periods_per_year=252)
                  if base else None)
        fd = results[name]["forward_drawdown"]
        v["paired_vs_matched_control"] = paired
        v["control"] = base
        v["dd_wall_ok"] = bool(fd["p95_drawdown"] is not None
                               and fd["p95_drawdown"] <= DD_WALL)
        v["cagr_floor_ok"] = bool(results[name]["cagr"] >= CAGR_FLOOR)
        v["adopt_eligible"] = bool(
            v["dsr_pass"] and v["cpcv_pass"] and v["dd_wall_ok"] and v["cagr_floor_ok"]
            and (base is None or (paired and paired["p_value_one_sided"] < 0.05))
        )
        verdicts[name] = v
        results[name]["cpcv"] = cpcv
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time()-t0:.0f}s | median {cpcv['oos_sharpe_median']:.4f} "
              f"{cpcv['frac_positive']*100:.0f}% positive", flush=True)

    print("\n" + "=" * 86, flush=True)
    for name, v in verdicts.items():
        r, fd, m = results[name], results[name]["forward_drawdown"], results[name]["metrics"]
        p = v.get("paired_vs_matched_control")
        print(f"  {name}: CAGR {r['cagr']*100:.2f}% (£{r['gbp_per_month']:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} "
              f"| DSR {'ok' if v['dsr_pass'] else 'FAIL'} "
              f"| CPCV {'ok' if v['cpcv_pass'] else 'FAIL'} "
              f"| wall {'ok' if v['dd_wall_ok'] else f'FAIL ({fd[chr(112)+chr(57)+chr(53)+chr(95)+chr(100)+chr(114)+chr(97)+chr(119)+chr(100)+chr(111)+chr(119)+chr(110)]*100:.1f}%)'} "
              f"| profit {'ok' if v['cagr_floor_ok'] else 'FAIL'}"
              + (f" | paired p={p['p_value_one_sided']:.4f} "
                 f"Δsharpe {p['sharpe_delta']:+.3f}" if p else "")
              + f"  -> {'ELIGIBLE' if v['adopt_eligible'] else 'REJECT'}", flush=True)

    elig = [n for n, v in verdicts.items() if v["adopt_eligible"]]
    winner = max(elig, key=lambda n: results[n]["metrics"]["sharpe"]) if elig else None
    print("-" * 86)
    if winner:
        print(f"  DECISION: ADOPT {winner}")
    else:
        inside = [(n, results[n]["gbp_per_month"]) for n, v in verdicts.items()
                  if v["dd_wall_ok"]]
        best = max(inside, key=lambda x: x[1]) if inside else None
        print("  DECISION: ADOPT NOTHING — the £800/mo @ 11% DD target is NOT reachable "
              "on this book.")
        if best:
            print(f"            Best inside the drawdown wall: {best[0]} at £{best[1]:.0f}/mo.")
    print("=" * 86, flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "engine/data_store/vol_target_overlay_prereg.md",
           "n_trials_before": n_before, "n_trials_used": used,
           "swept_grid_charged": SWEPT_GRID,
           "pbo": pbo, "pbo_binding": False,
           "dd_wall": DD_WALL, "cagr_floor": CAGR_FLOOR,
           "books": results, "verdicts": verdicts,
           "decision": {"winner": winner, "eligible": elig}}
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    ok = results_path.exists() and results_path.stat().st_size > 1000
    print(f"results {'WRITTEN' if ok else 'FAILED TO WRITE'}: {results_path} "
          f"({results_path.stat().st_size if results_path.exists() else 0} bytes)", flush=True)
    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())
